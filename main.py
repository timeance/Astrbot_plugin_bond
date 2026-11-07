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
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.api.star import Context, Star, StarTools, register

try:
    from astrbot.api import AstrBotConfig
except ImportError:  # pragma: no cover
    from astrbot.api.all import AstrBotConfig

from .api import commands as cmd
from .api import llm_tools as tools
from .api.facade import BondFacade
from .api.listeners import (
    Deps,
    clear_relation_injection,
    on_group_message,
    on_llm_request,
    on_llm_response,
)
from .core.config_migrations import migrate_saved_defaults
from .core.passive_familiarity import PassiveFamiliarityConfig
from .core.identity import parse_master_ids
from .core.judge_parse import DEFAULT_ATTITUDE_DELTAS
from .core.level_economy import EconomyConfig
from .core.levels import LevelTable
from .core.naming import TIER_NAME_BY_KEY
from .core.relationship import RelationshipTable
from .core.relation_profiles import DEFAULT_DAILY_CAPS, build_relation_profile
from .core.taskregistry import TaskRegistry
from .services.avatar_service import RankingAvatars
from .services.diagnostics_service import DiagnosticRecorder
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
from .services.self_impression_service import SelfImpressionService

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
    "Bond 关系好感系统：三维关系与成员印象",
    "0.5.7",
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
        self._diagnostics = DiagnosticRecorder(
            data_dir / "diagnostics",
            self._registry,
            enabled=(config.get("diagnostics") or {}).get("enabled", False),
            detail_level=(config.get("diagnostics") or {}).get(
                "detail_level", "assessment"
            ),
            max_bytes=(config.get("diagnostics") or {}).get("max_size_mb", 100)
            * 1_000_000,
        )
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
            diagnostics=self._diagnostics,
        )
        self._relationships = None
        cmd_cfg = config.get("command") or {}
        self._self_impression = SelfImpressionService(
            context,
            mode={"关闭": "off", "输出关系称谓": "title", "大模型总结": "summary"}.get(
                cmd_cfg.get("impression_mode", "关闭"), "off"
            ),
        )
        self._ranking_limit = int(cmd_cfg.get("ranking_limit", 10))
        self._ranking_access = str(cmd_cfg.get("ranking_access", "all"))
        self._ranking_image = bool(cmd_cfg.get("ranking_image", True))
        self._ranking_avatars = RankingAvatars()
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
        base_caps = config.get("relation_daily_caps") or {}
        relation_policy, relation_caps = build_relation_profile(
            config.get("relation_mode", "galgame"),
            caps={
                key: (
                    base_caps.get(key + "_up", default[0]),
                    base_caps.get(key + "_down", default[1]),
                )
                for key, default in DEFAULT_DAILY_CAPS.items()
            },
        )
        passive_rules = PassiveFamiliarityConfig.from_config(
            config.get("passive_familiarity_rules")
        )
        favor = FavorService(
            storage,
            levels,
            max_favor=max_favor,
            min_favor=min_favor,
            default_favor=float(config.get("default_favor", 0)),
            daily_cap_up=float(config.get("daily_cap_up", 4)),
            daily_cap_down=float(config.get("daily_cap_down", 8)),
            master_ids=master_ids,
            decay_enabled=bool(decay_cfg.get("enabled", True)),
            decay_grace_days=float(decay_cfg.get("grace_days", 5)),
            maintenance_strategy=decay_cfg.get("maintenance_strategy", "independent"),
            half_life_base=float(decay_cfg.get("half_life_base", 30)),
            half_life_growth=float(decay_cfg.get("half_life_growth", 1.0)),
            half_life_max=float(decay_cfg.get("half_life_max", 120)),
            decay_baseline=float(decay_cfg.get("affinity_baseline", 0)),
            familiarity_decay_enabled=bool(decay_cfg.get("familiarity_enabled", True)),
            familiarity_half_life=float(decay_cfg.get("familiarity_half_life", 30)),
            familiarity_baseline=float(decay_cfg.get("familiarity_baseline", 10)),
            trust_decay_enabled=bool(decay_cfg.get("trust_enabled", True)),
            trust_half_life=float(decay_cfg.get("trust_half_life", 60)),
            trust_baseline=float(decay_cfg.get("trust_baseline", 20)),
            familiarity_growth=config.get("familiarity_growth"),
            passive_familiarity=passive_rules,
            decay_floor_enabled=bool(decay_cfg.get("floor_enabled", True)),
            relationships=relationships,
            economy=eco,
            relation_policy=relation_policy,
            relation_caps=relation_caps,
            tz_name=str(config.get("timezone", "") or ""),
            scope_mode=scope_mode,
            diagnostics=self._diagnostics,
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

        from .services.passive_activity import PassiveActivity

        return Deps(
            passive_activity=PassiveActivity(
                storage.bot_id,
                enabled=config.get("passive_familiarity", True),
                config=passive_rules,
            ),
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
        await self._diagnostics.start()
        self._runtime.modes()
        # 重载时结算全部既有空间，不等待成员再次出现。
        for bot_id in self._storage.known_bot_ids():
            for mode in ("per_group", "global"):
                await self._runtime.select(bot_id, mode)
        logger.info("[八千代] 好感度插件已加载")

    async def terminate(self) -> None:
        # X10：先取消并等待在飞任务（评审/印象刷新/延迟清理），再关存储
        await self._registry.cancel_and_wait_all(timeout=5.0)
        await self._diagnostics.close()
        await self._storage.close()
        logger.info("[八千代] 好感度插件已卸载")

    # ---------------- 事件钩子（薄壳转发） ----------------

    # 框架排序实测（4.26.7 star_handler.py：sort(key=-priority)，数字大者
    # 先执行；waking_check 按注册表序派发，star_request 逐个执行并在
    # is_stopped 后中断）：麦麦 ≥6.12.0 的 -1000 实际排在所有默认 0 之后
    # （最后），"先跑并 stop_event 掐断默认优先级监听器"在其上不成立——
    # 饿死只可能来自优先级更高（或同优先级更早注册）的接管型监听器，
    # 而部署环境各插件实际取值无法逐版本确证。本监听器是纯后台观察者
    # （不发声、不 stop、不改正文），取 1000 先于常见的默认优先级接管者执行
    # （更高优先级仍可提前终止，不能观察被截断的消息）；文字唤醒默认关，开启时
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
                    ranking_access=self._ranking_access,
                    image_enabled=self._ranking_image,
                    avatar_service=self._ranking_avatars,
                )
                if reply is not None:
                    kind, payload = reply
                    if kind == "image":
                        try:
                            yield event.image_result(payload)
                        finally:
                            self._schedule_tmp_cleanup(payload)
                    else:  # 关闭图片或绘制失败时返回相同的公开资料
                        yield event.plain_result(payload)
        except Exception:
            logger.warning("[Bond] 文字查询失败，继续正常对话")
        try:
            await on_group_message(deps, event, ignored_ids=ignored)
        except Exception:
            logger.warning("[Bond] 关系观察失败，本次跳过，不影响对话")

    @filter.on_llm_request(priority=-100)
    async def _on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            clear_relation_injection(req)
            deps = await self._runtime.for_event(event)
            if deps is None:
                return
        except Exception:
            logger.warning("[Bond] 数据路由不可用，本次跳过关系注入")
            return
        await on_llm_request(deps, event, req)

    @filter.on_llm_response()
    async def _on_llm_resp(self, event: AstrMessageEvent, response: LLMResponse):
        binding = getattr(event, "_bond_assessment_binding", None)
        if not isinstance(binding, tuple) or len(binding) != 2:
            from .services.diagnostic_capture import record_bound_response

            record_bound_response(event, response)
            return
        try:
            await on_llm_response(binding[0], event, response)
        except Exception:
            logger.warning("[Bond] 回复上下文不可用，本次跳过，不影响聊天")

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

    @filter.command("我的印象")
    async def _cmd_self_impression(self, event: AstrMessageEvent):
        """按设置输出自己的关系称谓或当前人设的一句话印象。"""
        if _group_entry_id(event) is None:
            yield event.plain_result("请在群聊中查询自己的印象。")
            return
        deps = await self._runtime.for_event(event)
        if deps is not None:
            yield event.plain_result(
                await self._self_impression.query(deps.favor, event)
            )

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
            ranking_access=self._ranking_access,
            image_enabled=self._ranking_image,
            avatar_service=self._ranking_avatars,
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
        """提供当前 Bot 对当前发言者的关系称谓资料，不提供分值。

        资料不要求披露或复述，如何使用由当前人格、系统规则和对话语境决定。
        不支持查询其他成员，也不代表发言者对 Bot 或群友之间的关系。
        发言者询问“我的印象”“对我的感觉”时，优先使用 query_my_impression。

        Args:
            target(str): 仅允许留空或当前发言者的账号，不能用他人姓名代替
        """
        if _group_entry_id(event) is None:
            return tools.query_context(
                status="unavailable", reason="请在群聊中查询自己的关系。"
            )
        deps = await self._runtime.for_event(event)
        if deps is None:
            return tools.query_context(
                status="unavailable", reason="本群未启用关系功能。"
            )
        return await tools.tool_query_favor(deps.favor, event, target, for_llm=True)

    @filter.llm_tool(name="query_my_impression")
    async def _tool_self_impression(self, event: AstrMessageEvent) -> str:
        """查询当前 Bot 对发言者本人的印象。

        发言者问“你对我有什么印象”“你觉得我怎么样”“你对我的感觉如何”时可调用。
        仅查询本人，不查询其他成员；关闭时不可用。返回已按设置生成的答复，
        转达该答复即可，不补充具体分值、私人证据或其他群资料。
        """
        if _group_entry_id(event) is None:
            return tools.query_context(
                status="unavailable", reason="请在群聊中查询自己的印象。"
            )
        deps = await self._runtime.for_event(event)
        if deps is None:
            return tools.query_context(
                status="unavailable", reason="本群未启用关系功能。"
            )
        if self._self_impression.mode == "off":
            return tools.query_context(
                status="unavailable", reason="我的印象功能未开启。"
            )
        reply = await self._self_impression.query(deps.favor, event)
        return tools.query_context(object="当前发言者", impression_reply=reply)

    @filter.llm_tool(name="query_favor_ranking")
    async def _tool_rank(self, event: AstrMessageEvent) -> str:
        """提供当前 Bot 对本群成员的前十名关系称谓，按亲近度排序，不提供分值。

        资料不要求披露或复述，如何使用由当前人格、系统规则和对话语境决定。
        不用于回答某个群友最喜欢谁，也不能查询群友之间的关系。
        """
        if _group_entry_id(event) is None:
            return tools.query_context(
                status="unavailable", reason=_RANKING_GROUP_REQUIRED
            )
        deps = await self._runtime.for_event(event)
        if deps is None:
            return tools.query_context(
                status="unavailable", reason="本群未启用关系功能。"
            )
        return await tools.tool_query_ranking(
            deps.favor,
            event,
            self._ranking_limit,
            for_llm=True,
            ranking_access=self._ranking_access,
        )
