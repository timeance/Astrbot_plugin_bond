"""心弦好感度 - 事件监听 handler。

on_group_message：触发 LLM 评估（好感度变化的唯一自动引擎）。
on_llm_request：向 system_prompt 注入好感度档案。
身份一律从事件取（get_sender_id/get_group_id），不解析任何注入文本。
"""

from __future__ import annotations

from dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest

from ..core.assessment import Interaction
from ..core.taskregistry import TaskRegistry
from ..services.favor_service import FavorService
from ..services.impression_service import ImpressionService
from ..services.inject_service import InjectService
from ..services.judge_service import JudgeService

try:  # 消息组件（兼容新旧导入路径）
    from astrbot.api.message_components import At, Reply
except ImportError:  # pragma: no cover
    from astrbot.core.message.components import At, Reply


@dataclass
class Deps:
    """监听器依赖包（由 main.py 装配）。"""

    favor: FavorService
    judge: JudgeService
    inject: InjectService
    registry: TaskRegistry  # 与插件卸载共享同一注册表
    impressions: ImpressionService | None = None
    inject_enabled: bool = True
    memory_count: int = 3
    memory_days: int = 7
    memory_sig_threshold: float = 0.0
    memory_sig_window_mult: float = 1.0
    # §6.6 情绪耦合（v1.31.0）：context 供跨插件取麦麦 facade；
    # 开关只控本侧调用，麦麦侧两个总开关任一关闭桥也自动空转
    context: object | None = None
    emotion_coupling: bool = True
    assessment: object | None = None
    prompts: object | None = None
    bot_id: str = ""


@dataclass(frozen=True)
class AssessmentEvent:
    """排队时固定可信身份，后台不再引用可变的平台事件。"""

    group_id: str
    user_id: str
    bot_id: str
    unified_msg_origin: str
    platform_name: str
    message_id: str = ""

    def get_group_id(self):
        return self.group_id

    def get_sender_id(self):
        return self.user_id

    def get_self_id(self):
        return self.bot_id

    def get_platform_name(self):
        return self.platform_name


def _chain_flags(event: AstrMessageEvent) -> tuple[bool, bool]:
    """只接受无冲突的当前 Bot 指向；缺失或多来源时让关系评估弃权。"""
    from ..core.scope import is_account_id

    self_id = str(event.get_self_id() or "")
    if not is_account_id(self_id):
        return False, False
    chain = getattr(getattr(event, "message_obj", None), "message", None) or []
    ats = [c for c in chain if isinstance(c, At)]
    replies = [c for c in chain if isinstance(c, Reply)]
    if any(str(getattr(c, "qq", "")) != self_id for c in ats):
        return False, False
    source_ids = set()
    for reply in replies:
        if str(getattr(reply, "sender_id", "")) != self_id:
            return False, False
        source_id = getattr(reply, "id", None)
        if type(source_id) not in (str, int):
            return False, False
        source_id = str(source_id)
        if not source_id.strip() or source_id == "0" or len(source_id) > 200:
            return False, False
        source_ids.add(source_id)
    if len(source_ids) > 1:
        return False, False
    return bool(ats), bool(replies)


def _reply_metadata(event):
    """只读取已通过结构冲突检查的目标 Bot 引用，不从引文解析身份。"""
    if not _chain_flags(event)[1]:
        return {}
    reply = next(c for c in event.message_obj.message if isinstance(c, Reply))
    text = getattr(reply, "message_str", None)
    if not isinstance(text, str):
        text = getattr(reply, "text", "")
    return dict(
        reply_message_id=str(reply.id),
        reply_sender_id=str(reply.sender_id),
        reply_text=text[:2000] if isinstance(text, str) else "",
    )


async def on_llm_response(deps: Deps, event: AstrMessageEvent, response) -> None:
    """使用入场时冻结的服务与身份，不按回复完成时的配置重新选择空间。"""
    binding = getattr(event, "_bond_assessment_binding", None)
    if not isinstance(binding, tuple) or len(binding) != 2 or binding[0] is not deps:
        return
    interaction = binding[1]
    if not isinstance(interaction, Interaction):
        return
    text = getattr(response, "completion_text", None)
    if deps.assessment is not None and isinstance(text, str) and text:
        deps.assessment.remember_bot_reply(interaction, text)


# X10：后台评审任务经 Deps.registry（TaskRegistry）发起——裸 create_task
# 只被事件循环弱引用可能被 GC 中途回收，且卸载时需 cancel_and_wait


async def on_group_message(deps: Deps, event: AstrMessageEvent) -> None:
    """群消息入口：评估引擎（唯一自动引擎，内部自行判断开关/冷却/降级）。

    评审是事后打分（评的是已说出口的话），结果只影响下一条消息的注入——
    因此整个评审+落库放后台任务，不阻塞本条消息的回复链路（AstrBot 的
    pipeline 按 stage 串行 await，同步评审会让 @ 消息先卡一次评审 LLM 调用）。
    评审本身 fail-silent：后台任务的异常只记日志。
    """
    group_id, user_id = event.get_group_id(), event.get_sender_id()
    if not group_id or not user_id:
        return
    if user_id == str(event.get_self_id() or ""):
        return  # 自己的消息不计

    # 消息链与文本在调度前取好（事件对象在后台任务里不再可靠）
    try:
        _nick = event.get_sender_name()
    except Exception:
        # 部分适配器无该访问器/抛错：昵称缺席只影响显示名,不阻断评审
        _nick = None
    from ..core.member_names import event_names

    nickname, card = event_names(event)
    # 保留平台原文用于证据校验，不把清洗后的字符串伪装成原始证据。
    text = event.message_str or ""
    has_at_bot, is_reply_bot = _chain_flags(event)
    reply_metadata = _reply_metadata(event)
    original_event = event
    event = AssessmentEvent(
        str(group_id),
        str(user_id),
        str(event.get_self_id()),
        str(getattr(event, "unified_msg_origin", "") or ""),
        str(event.get_platform_name() or ""),
        str(getattr(event.message_obj, "message_id", "") or ""),
    )

    admission_state = await deps.favor.temporary_state(group_id, user_id)
    admitted_interaction = None
    if (
        deps.assessment is not None
        and deps.assessment.config.enabled
        and (has_at_bot or is_reply_bot)
        and not admission_state["active"]
    ):
        from ..core.assessment import locally_relevant

        if locally_relevant(text, True):
            try:
                admitted_interaction = Interaction(
                    event.bot_id,
                    event.group_id,
                    event.user_id,
                    event.platform_name,
                    event.message_id,
                    text,
                    **reply_metadata,
                )
                original_event._bond_assessment_binding = (deps, admitted_interaction)
                deps.assessment.remember_admitted_interaction(admitted_interaction)
            except (ValueError, TypeError):
                admitted_interaction = None

    async def _bg() -> None:
        try:
            await deps.favor.observe_member(group_id, user_id)
            await deps.favor.update_member_names(
                str(group_id), str(user_id), nickname, card
            )
            if (
                deps.assessment is not None
                and deps.assessment.config.enabled
                and text.strip()
            ):
                interaction = admitted_interaction or Interaction(
                    event.bot_id,
                    event.group_id,
                    event.user_id,
                    event.platform_name,
                    event.message_id,
                    text,
                    **reply_metadata,
                )
                status = await deps.assessment.assess(
                    interaction,
                    directed=has_at_bot or is_reply_bot,
                    admission_state=admission_state,
                )
                if (
                    status == "accepted"
                    and deps.impressions is not None
                    and deps.favor.scope_mode == "per_group"
                ):
                    await deps.impressions.maybe_refresh(
                        group_id, user_id, umo=event.unified_msg_origin
                    )
        except Exception:
            # 意外异常属疑难：warning 必须带堆栈，否则后台静默死因不可查
            logger.warning(
                "[八千代] 后台评估任务异常（忽略，不影响对话）", exc_info=True
            )

    deps.registry.spawn(_bg(), name=f"judge:{group_id}/{user_id}")


async def on_llm_request(
    deps: Deps, event: AstrMessageEvent, req: ProviderRequest
) -> None:
    """LLM 请求前：注入好感度档案（仅群聊，私聊跳过）。

    X2：注入是每次对话的必经路径而非增值功能——任何失败（存储/渲染/
    模板）只 warning 并跳过本次注入，绝不把异常抛进框架钩子打断回复。"""
    try:
        await _on_llm_request_inner(deps, event, req)
    except Exception:
        logger.warning(
            "[八千代] 注入链路异常，本次跳过注入（不影响对话）", exc_info=True
        )


async def _on_llm_request_inner(
    deps: Deps, event: AstrMessageEvent, req: ProviderRequest
) -> None:
    if not deps.inject_enabled:
        return
    group_id, user_id = event.get_group_id(), event.get_sender_id()
    if not group_id or not user_id:
        return
    rec = await deps.favor.get(group_id, user_id)
    # 全局关系可用于当前成员的称谓，但不召回其他群的印象或聊天流水。
    local = deps.favor.scope_mode == "per_group"
    events = (
        await deps.favor.recent_events(
            group_id,
            user_id,
            deps.memory_count,
            deps.memory_days,
            sig_threshold=deps.memory_sig_threshold,
            sig_window_mult=deps.memory_sig_window_mult,
        )
        if local
        else []
    )
    template = deps.prompts.resolve("behavior", deps.bot_id, str(group_id))
    description = deps.favor.describe_relation(rec.familiarity, rec.favor, rec.trust)
    block = deps.inject.build_relation_block(
        template,
        relationship=description.text,
        impression=rec.impression if local else "",
        events=events,
        configured_master=deps.favor.is_master(str(user_id)),
    )
    deps.inject.inject(req, block)
    deps.favor.record_diagnostic(
        group_id,
        user_id,
        "render",
        status="injected",
        after=dict(familiarity=rec.familiarity, affinity=rec.favor, trust=rec.trust),
        bands_after=description.bands or None,
        term_after=description.key,
    )
