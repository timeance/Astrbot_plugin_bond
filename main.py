"""心弦好感度 - 插件入口（组合根）。

装配顺序：storage → levels → services → deps → api。
所有 @filter 钩子均为本 Star 类的薄壳方法（AstrBot 只扫描 Star 实例方法），
实际逻辑全部转发给 api/ 层的纯 handler 函数。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register

try:
    from astrbot.api import AstrBotConfig
except ImportError:  # pragma: no cover
    from astrbot.api.all import AstrBotConfig

from .api import commands as cmd
from .api import llm_tools as tools
from .api.facade import BondFacade
from .api.listeners import Deps, on_group_message, on_llm_request
from .core.config_migrations import migrate_saved_defaults
from .core.identity import parse_master_ids
from .core.judge_parse import DEFAULT_ATTITUDE_DELTAS
from .core.level_economy import EconomyConfig
from .core.levels import LevelTable
from .core.naming import TIER_NAME_BY_KEY
from .core.relationship import RelationshipTable
from .core.taskregistry import TaskRegistry
from .services.favor_service import FavorService
from .services.impression_service import ImpressionService
from .services.inject_service import InjectService
from .services.prompt_service import PromptService
from .services.assessment_service import (
    AssessmentConfig,
    AssessmentCapacity,
    AssessmentService,
)
from .services.judge_service import JudgeService
from .storage.router import BotScopeStorageRouter
from .services.runtime_service import RuntimeService

_PLUGIN_DIR = Path(__file__).resolve().parent


def _read_resource(rel: str) -> str:
    return (_PLUGIN_DIR / rel).read_text(encoding="utf-8").strip()


def _split_phrases(raw: str) -> set[str]:
    """逗号分隔的短语（兼容中文逗号）→ 去空去重的集合。"""
    return {
        x.strip() for x in str(raw or "").replace("，", ",").split(",") if x.strip()
    }


_RANKING_GROUP_REQUIRED = "请在群聊中查询本群排行。"


def _group_entry_id(event) -> str | None:
    """群功能入口契约：先确认群身份，再交给 RuntimeService 解析。"""
    group_id = event.get_group_id()
    return str(group_id) if group_id else None


@register(
    "astrbot_plugin_bond",
    "yiwuerxin, Timeance",
    "Bond 三维关系与成员印象",
    "0.2.3",
    "https://github.com/timeance/Astrbot_plugin_bond",
)
class BondPlugin(Star):
    """Bond 三维关系与成员印象。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config

        # 存量配置默认值迁移（仅当存值与已知旧默认逐字一致，见 core/config_migrations.py）
        migrated = migrate_saved_defaults(config)
        if migrated:
            try:
                config.save_config()
                logger.info(f"[八千代] 配置默认值迁移：{', '.join(migrated)}")
            except Exception:
                # 本次运行已用上内存中的新值；文件未写入则下次加载重试
                logger.warning("[八千代] 配置默认值迁移后保存失败，将在下次加载时重试")

        from copy import deepcopy

        # 设置保存后统一重载生效，后创建的 Bot/空间也使用本次加载快照。
        self._dependency_config = deepcopy(dict(config))

        data_dir = StarTools.get_data_dir("astrbot_plugin_bond")

        self._prompts = PromptService(data_dir / "prompts.json")
        if self._prompts.load_error:
            logger.warning(
                f"[Bond] {self._prompts.load_error}；已暂停关系提示词和自动评估"
            )
        self._registry = TaskRegistry()
        # 所有 bundle 使用本次加载时的同一模型配置；面板保存后统一重载生效。
        self._assessment_values = dict(config.get("assessment") or {})
        self._assessment_values.pop("max_concurrent_assessments", None)
        self._assessment_capacity = AssessmentCapacity(
            (config.get("assessment") or {}).get("max_concurrent_assessments", 2)
        )
        self._storage = BotScopeStorageRouter(data_dir)
        from .services.bot_discovery import BotDiscovery

        self._runtime = RuntimeService(
            self._storage,
            config,
            self._build_deps,
            bot_discovery=BotDiscovery(context),
        )
        self._relationships = None
        cmd_cfg = config.get("command") or {}
        self._ranking_limit = int(cmd_cfg.get("ranking_limit", 10))
        self._text_wake_enabled = bool(cmd_cfg.get("text_wake_enabled", False))
        self._text_wake_ranking = _split_phrases(
            cmd_cfg.get("text_wake_ranking", "好感排行,好感榜单,好感排名")
        )
        render_cfg = config.get("render") or {}
        self._render_font = (render_cfg.get("font_path") or "").strip()
        self._render_rows = int(render_cfg.get("rows_per_col", 12))

        # 跨插件 API：context.get_registered_star("astrbot_plugin_bond").star_cls.api
        self.api = BondFacade(self._runtime)

        # 原生 dashboard 页面 API（框架支持时注册，内嵌于主面板，无独立端口/鉴权）
        from .api.page_api import PageApi

        self._page_api = PageApi(self._runtime)
        self._page_api.register(context)

    def _build_deps(self, storage, scope_mode):
        config, context = self._dependency_config, self.context
        max_favor = float(config.get("max_favor", 100))
        min_favor = float(config.get("min_favor", -100))
        levels = LevelTable.from_config(config)
        master_ids = parse_master_ids(config.get("master_ids", ""))

        decay_cfg = config.get("decay") or {}
        rel_cfg = config.get("relationship") or {}
        economy_cfg = config.get("economy") or {}
        impression_cfg = config.get("impression") or {}
        relationships = (
            RelationshipTable.from_config(rel_cfg)
            if bool(rel_cfg.get("enabled", False))
            else None
        )
        self._relationships = relationships
        # 防通胀经济学层（拼音配置键→中文等级名的映射在 from_config 内处理）
        eco = EconomyConfig.from_config(economy_cfg)
        favor = FavorService(
            storage,
            levels,
            max_favor=max_favor,
            min_favor=min_favor,
            default_favor=float(config.get("default_favor", 0)),
            daily_cap_up=float(config.get("daily_cap_up", 4)),
            daily_cap_down=float(config.get("daily_cap_down", 8)),
            master_ids=master_ids,
            decay_enabled=bool(decay_cfg.get("enabled", False)),
            half_life_base=float(decay_cfg.get("half_life_base", 10)),
            half_life_growth=float(decay_cfg.get("half_life_growth", 1.3)),
            half_life_max=float(decay_cfg.get("half_life_max", 60)),
            decay_baseline=float(decay_cfg.get("baseline", 0.0)),
            decay_floor_enabled=bool(decay_cfg.get("floor_enabled", True)),
            relationships=relationships,
            economy=eco,
            tz_name=str(config.get("timezone", "") or ""),
            scope_mode=scope_mode,
        )

        judge_cfg = config.get("judge") or {}
        inject_cfg = config.get("inject") or {}

        # 五档分值映射：配置键（拼音）→ 档位名（中文），映射表见 core/naming.py
        ad_raw = judge_cfg.get("attitude_deltas") or {}
        attitude_deltas = {
            tier: float(ad_raw.get(key, DEFAULT_ATTITUDE_DELTAS[tier]))
            for key, tier in TIER_NAME_BY_KEY.items()
        }

        # 运行注入只使用三维模板；旧标量提示词不再定义人格或关系身份。
        inject = InjectService(levels, "")
        judge = JudgeService(
            context,
            storage,
            enabled=False,
            provider_id=(judge_cfg.get("provider_id") or "").strip() or None,
            cooldown_sec=int(judge_cfg.get("cooldown_sec", 120)),
            max_abs_delta=float(judge_cfg.get("max_abs_delta", 3)),
            only_when_at_or_reply=bool(judge_cfg.get("only_when_at_or_reply", True)),
            prompt_template=_read_resource(
                "resources/prompts/judge_prompt_narrative.txt"
                if bool(judge_cfg.get("narrative_reason", False))
                else "resources/prompts/judge_prompt.txt"
            ),
            force_session_model=bool(judge_cfg.get("narrative_reason", False)),
            context_window=(
                int(judge_cfg.get("context_window", 0))
                if scope_mode == "per_group"
                else 0
            ),
            follow_persona=bool(judge_cfg.get("follow_persona", True)),
            bot_name=(judge_cfg.get("bot_name") or "").strip() or "当前助手",
            attitude_deltas=attitude_deltas,
            roster=(judge_cfg.get("roster") or "").strip(),
            timeout_sec=float(judge_cfg.get("timeout_sec", 60)),
            follow_maisoul=bool(judge_cfg.get("follow_maisoul_replyer", False)),
            scope_mode=scope_mode,
        )

        # 后台任务注册表（X10）：评审/印象/延迟清理统一持引用，卸载时
        # cancel_and_wait_all 后再关存储
        # 印象服务独立于好感度门面（借 JudgeService 的 provider/人格解析做汇总）
        impressions = ImpressionService(
            storage,
            interval=int(impression_cfg.get("interval", 8)),
            timeout_sec=float(judge_cfg.get("timeout_sec", 60)),
            registry=self._registry,
            points_mode=bool(impression_cfg.get("points_mode", False)),
            scope_mode=scope_mode,
        )
        impressions.bind_summarizer(judge)

        assessment_cfg = dict(self._assessment_values)

        async def budget_stores():
            return [
                await self._storage.get(storage.bot_id, mode)
                for mode in ("per_group", "global")
            ]

        assessment = AssessmentService(
            storage,
            favor,
            context,
            AssessmentConfig(**assessment_cfg),
            self._assessment_capacity,
            budget_stores=budget_stores,
            prompts=self._prompts,
        )

        return Deps(
            assessment=assessment,
            prompts=self._prompts,
            bot_id=storage.bot_id,
            favor=favor,
            judge=judge,
            inject=inject,
            impressions=impressions,
            registry=self._registry,
            inject_enabled=bool(inject_cfg.get("enabled", False)),
            memory_count=int(inject_cfg.get("memory_count", 3)),
            memory_days=int(inject_cfg.get("memory_days", 7)),
            memory_sig_threshold=float(inject_cfg.get("memory_sig_threshold", 1.0)),
            memory_sig_window_mult=float(inject_cfg.get("memory_sig_window_mult", 3.0)),
            # §6.6 情绪耦合（v1.31.0）：context 取麦麦 facade；开关只控本侧
            context=context,
            emotion_coupling=bool((config.get("coupling") or {}).get("enabled", False)),
        )

    async def initialize(self) -> None:
        self._runtime.modes()
        logger.info("[八千代] 好感度插件已加载")

    async def terminate(self) -> None:
        # X10：先取消并等待在飞任务（评审/印象刷新/延迟清理），再关存储
        await self._registry.cancel_and_wait_all(timeout=5.0)
        await self._storage.close()
        logger.info("[八千代] 好感度插件已卸载")

    # ---------------- 事件钩子（薄壳转发） ----------------

    # 框架排序实测（4.26.7 star_handler.py：sort(key=-priority)，数字大者
    # 先执行；waking_check 按注册表序派发，star_request 逐个执行并在
    # is_stopped 后中断）：麦麦 ≥6.12.0 的 -1000 实际排在所有默认 0 之后
    # （最后），"先跑并 stop_event 掐断默认优先级监听器"在其上不成立——
    # 饿死只可能来自优先级更高（或同优先级更早注册）的接管型监听器，
    # 而部署环境各插件实际取值无法逐版本确证。本监听器是纯后台观察者
    # （不发声、不 stop、不改正文），取 1000 保证恒先于任何接管者执行
    # （priority=1 盖不过正数大优先级的接管者）；文字唤醒默认关，开启时
    # 由 _bond_cmd_done 防重。
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1000)
    async def _on_group_msg(self, event: AstrMessageEvent):
        # 文字唤醒：群里直接发文字（不用 /）触发查询指令；与 / 指令一致，之后照常跑评估引擎
        try:
            self._runtime.observe_bot(event)
            sender = str(event.get_sender_id() or "")
            ignored = {str(x) for x in (self.config.get("ignored_bot_ids") or [])}
            ignored.update(self._runtime.known_bot_ids())
            ignored.add(str(event.get_self_id() or ""))
            if sender in ignored:
                return
            deps = await self._runtime.for_event(event)
            if deps is None:
                return
        except Exception:
            logger.warning("[Bond] 数据路由不可用，本次跳过关系观察")
            return
        try:
            if self._text_wake_enabled and not getattr(event, "_bond_cmd_done", False):
                reply = await cmd.try_text_wake(
                    deps.favor,
                    event,
                    self._text_wake_ranking,
                    self._render_font,
                    self._render_rows,
                    text_limit=self._ranking_limit,
                )
                if reply is not None:
                    kind, payload = reply
                    if kind == "image":
                        try:
                            yield event.image_result(payload)
                        finally:
                            self._schedule_tmp_cleanup(payload)
                    else:  # 未安装 Pillow：降级文字排行
                        yield event.plain_result(payload)
        except Exception:
            logger.warning("[Bond] 文字查询失败，继续正常对话")
        try:
            await on_group_message(deps, event)
        except Exception:
            logger.warning("[Bond] 关系观察失败，本次跳过，不影响对话")

    @filter.on_llm_request(priority=-100)
    async def _on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            deps = await self._runtime.for_event(event)
            if deps is None:
                return
        except Exception:
            logger.warning("[Bond] 数据路由不可用，本次跳过关系注入")
            return
        await on_llm_request(deps, event, req)

    # ---------------- 指令 ----------------

    @filter.command("我的关系")
    async def _cmd_self_relation(self, event: AstrMessageEvent):
        """查看当前发言者自己的关系称谓。"""
        if _group_entry_id(event) is None:
            yield event.plain_result("请在群聊中查询自己的关系。")
            return
        deps = await self._runtime.for_event(event)
        if deps is None:
            return
        yield event.plain_result(await tools.tool_query_favor(deps.favor, event))

    @filter.command("好感排行")
    async def _cmd_rank(self, event: AstrMessageEvent):
        """查看本群前十名关系称谓，不显示分值。"""
        if _group_entry_id(event) is None:
            yield event.plain_result(_RANKING_GROUP_REQUIRED)
            return
        deps = await self._runtime.for_event(event)
        if deps is None:
            return
        event._bond_cmd_done = True
        kind, payload = await cmd.rank_reply(
            deps.favor,
            event,
            font_path=self._render_font,
            rows_per_col=self._render_rows,
            text_limit=self._ranking_limit,
        )
        if kind == "image":
            try:
                yield event.image_result(payload)
            finally:
                self._schedule_tmp_cleanup(payload)
        else:
            yield event.plain_result(payload)

    def _schedule_tmp_cleanup(self, path: str, delay: float = 5.0) -> None:
        """排行图临时 PNG 延迟删除（X8）：yield 恢复即已发出，留几秒兜底
        平台侧异步读取，经注册表发起防裸任务。"""

        async def _rm():
            await asyncio.sleep(delay)
            try:
                os.unlink(path)
            except OSError:
                pass  # 已被清理或平台侧占用失败都无碍（渲染前还有兜底清扫）

        self._registry.spawn(_rm(), name="rank_tmp_cleanup")

    @filter.command("好感设置")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def _cmd_set(
        self, event: AstrMessageEvent, target: str = "", value: float = 0.0
    ):
        """设置某成员好感度（管理员，支持一位小数与负值）。用法：/好感设置 QQ号 数值"""
        deps = await self._runtime.for_event(event)
        if deps is None:
            return
        yield event.plain_result(await cmd.handle_set(deps.favor, event, target, value))

    @filter.command("好感重置")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def _cmd_reset(self, event: AstrMessageEvent, target: str = ""):
        """重置好感度（管理员）。用法：/好感重置 [QQ号]，不带参数清空整群"""
        deps = await self._runtime.for_event(event)
        if deps is None:
            return
        yield event.plain_result(await cmd.handle_reset(deps.favor, event, target))

    @filter.command("关系设置")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def _cmd_set_rel(
        self, event: AstrMessageEvent, target: str = "", key: str = ""
    ):
        """设置某成员与小千的关系（管理员）。用法：/关系设置 QQ号 类型"""
        deps = await self._runtime.for_event(event)
        if deps is None:
            return
        yield event.plain_result(
            await cmd.handle_set_relationship(
                deps.favor, self._relationships, event, target, key
            )
        )

    @filter.command("印象设置")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def _cmd_set_tags(
        self, event: AstrMessageEvent, target: str = "", tags: str = ""
    ):
        """设置成员标签（管理员）。用法：/印象设置 QQ号 标签1,标签2"""
        deps = await self._runtime.for_event(event)
        if deps is None:
            return
        yield event.plain_result(
            await cmd.handle_set_tags(deps.impressions, event, target, tags)
        )

    @filter.command("印象刷新")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def _cmd_refresh_imp(self, event: AstrMessageEvent, target: str = ""):
        """立即刷新成员印象（管理员）。用法：/印象刷新 QQ号"""
        deps = await self._runtime.for_event(event)
        if deps is None:
            return
        yield event.plain_result(
            await cmd.handle_refresh_impression(deps.impressions, event, target)
        )

    # ---------------- LLM 工具 ----------------

    @filter.llm_tool(name="query_favor")
    async def _tool_query(self, event: AstrMessageEvent, target: str = "") -> str:
        """查询当前发言者自己的关系称谓，不提供分值。

        Args:
            target(str): 仅允许留空或当前发言者的账号
        """
        if _group_entry_id(event) is None:
            return "请在群聊中查询自己的关系。"
        deps = await self._runtime.for_event(event)
        if deps is None:
            return "本群未启用关系功能。"
        return await tools.tool_query_favor(deps.favor, event, target)

    @filter.llm_tool(name="query_favor_ranking")
    async def _tool_rank(self, event: AstrMessageEvent) -> str:
        """查询本群前十名关系称谓，不提供分值。"""
        if _group_entry_id(event) is None:
            return _RANKING_GROUP_REQUIRED
        deps = await self._runtime.for_event(event)
        if deps is None:
            return "本群未启用关系功能。"
        return await tools.tool_query_ranking(deps.favor, event, self._ranking_limit)
