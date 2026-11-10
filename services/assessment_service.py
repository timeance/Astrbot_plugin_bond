"""受限异步评估流水线；失败停止本次评分，不切换供应商或使用旧标量结果。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from collections import OrderedDict, deque

from .diagnostic_capture import CURRENT, capture_content, interaction_capture

from ..core.assessment import (
    Interaction,
    SYSTEM_PROMPT,
    locally_relevant,
    needs_review,
    parse_verdict,
    TRUST_CONTRACT,
    verify_trust_evidence,
)
from ..core.relation_policy import TRUST_EVENTS


@dataclass(frozen=True)
class AssessmentConfig:
    enabled: bool = False
    small_provider_id: str = ""
    large_provider_id: str = ""
    assessment_mode: str = "tiered"
    small_timeout_sec: float = 20
    large_timeout_sec: float = 30
    cooldown_sec: float = 120
    window_sec: float = 300
    window_max_calls: int = 5
    per_user_daily_small_cap: int = 30
    per_user_daily_large_cap: int = 8
    per_bot_daily_small_cap: int = 300
    per_bot_daily_large_cap: int = 100
    context_turns: int = 6
    context_chars: int = 4000
    context_events: int = 8
    context_days: int = 7

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("enabled 必须是布尔值")
        if not isinstance(self.small_provider_id, str) or not isinstance(
            self.large_provider_id, str
        ):
            raise ValueError("Provider ID 必须是字符串")
        if self.assessment_mode not in {"single", "tiered"}:
            raise ValueError("未知评估模式")
        for name in (
            "small_timeout_sec",
            "large_timeout_sec",
            "cooldown_sec",
            "window_sec",
        ):
            import math

            value = getattr(self, name)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} 必须是有限正数")
        for name in (
            "per_user_daily_small_cap",
            "per_bot_daily_small_cap",
            "per_user_daily_large_cap",
            "per_bot_daily_large_cap",
            "window_max_calls",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} 必须是非负整数")
        for name, maximum in (
            ("context_turns", 20),
            ("context_chars", 20000),
            ("context_events", 20),
            ("context_days", 90),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"{name} 应为 0..{maximum}")
        if self.enabled and not self.small_provider_id.strip():
            raise ValueError("启用评估必须指定 small_provider_id")


class AssessmentCapacity:
    """插件级在途上限；忙时丢弃候选，禁止创建无界等待队列。"""

    def __init__(self, limit=2):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("并发上限应为 1..32")
        self.limit, self.active = limit, 0
        self.budget_lock = asyncio.Lock()
        # 仅保存已入场的本人与 Bot 相关上下文；跨空间复用但按真实群严格隔离。
        self.recent = OrderedDict()


class AssessmentService:
    def __init__(
        self, storage, favor, context, config, capacity, budget_stores, prompts=None
    ):
        self.storage, self.favor, self.context = storage, favor, context
        self.config, self.capacity = config, capacity
        self.prompts = prompts
        self.pending = set()
        self.budget_stores = budget_stores

    async def today_usage(self, *, now=None):
        """按 UTC 汇总同 Bot 两个持久空间；配额来自本 bundle 的运行配置。"""
        from datetime import datetime, timedelta, timezone

        now = time.time() if now is None else now
        today = datetime.fromtimestamp(now, timezone.utc).date()
        counts = {"small": 0, "large": 0}
        async with self.capacity.budget_lock:
            stores = (
                list(await self.budget_stores())
                if self.budget_stores
                else [self.storage]
            )
            if self.storage not in stores:
                stores.append(self.storage)
            for store in stores:
                for tier, count in (
                    await store.assessment_day_counts(today.isoformat())
                ).items():
                    counts[tier] += count
        return dict(
            day=today.isoformat(),
            timezone="UTC",
            reset_at=datetime.combine(
                today + timedelta(days=1), datetime.min.time(), timezone.utc
            ).timestamp(),
            initial=dict(used=counts["small"], cap=self.config.per_bot_daily_small_cap),
            review=dict(used=counts["large"], cap=self.config.per_bot_daily_large_cap),
        )

    def _remember(self, interaction, role, text, message_id, reply=None):
        if (
            not self.config.enabled
            or not self.config.context_turns
            or not self.config.context_days
            or not text
        ):
            return
        key = (interaction.bot_id, interaction.group_id, interaction.user_id)
        recent = self.capacity.recent
        turns = recent.pop(key, deque(maxlen=20))
        item = dict(
            role=role,
            message_id=message_id,
            text=text[:2000],
            ts=time.time(),
            reply=reply,
        )
        if not any(t["message_id"] == message_id and t["role"] == role for t in turns):
            turns.append(item)
        recent[key] = turns
        while len(recent) > 128:
            recent.popitem(last=False)

    def remember_admitted_interaction(self, interaction):
        """由可信监听入口在调度前记入；保证先到的 Bot 回复仍可绑定当前成员。"""
        if interaction.bot_id != self.storage.bot_id:
            return
        reply = (
            dict(
                message_id=interaction.reply_message_id,
                sender=interaction.reply_sender_id,
                text=interaction.reply_text,
            )
            if interaction.reply_message_id
            else None
        )
        self._remember(
            interaction, "user", interaction.text, interaction.message_id, reply
        )

    def remember_bot_reply(self, interaction, text):
        """宿主响应钩子传入可信目标 Bot 给本成员的实际回复，不读取第三方会话。"""
        if interaction.bot_id != self.storage.bot_id:
            return
        key = (interaction.bot_id, interaction.group_id, interaction.user_id)
        turns = self.capacity.recent.get(key, ())
        if not any(
            t["role"] == "user" and t["message_id"] == interaction.message_id
            for t in turns
        ):
            return
        self._remember(interaction, "bot", text, "response:" + interaction.message_id)

    async def _snapshot(self, interaction):
        cfg = self.config
        reply = (
            dict(
                message_id=interaction.reply_message_id,
                sender=interaction.reply_sender_id,
                text=interaction.reply_text[: cfg.context_chars],
            )
            if interaction.reply_message_id
            else None
        )
        key = (interaction.bot_id, interaction.group_id, interaction.user_id)
        turns = [
            dict(t)
            for t in self.capacity.recent.get(key, ())
            if time.time() - t["ts"] <= cfg.context_days * 86400
            and t["message_id"]
            not in {interaction.message_id, "response:" + interaction.message_id}
        ]
        self._remember(
            interaction, "user", interaction.text, interaction.message_id, reply
        )
        events = (
            await self.storage.assessment_context_events(
                self.favor.group_key(interaction.group_id),
                interaction.user_id,
                origin_group_id=interaction.group_id,
                limit=cfg.context_events,
                days=cfg.context_days,
            )
            if cfg.context_events and cfg.context_days
            else []
        )
        # 只收取存储提供的受限证据摘要；原始群过滤须在 SQL 分页之前执行。
        scored = []
        for event in events[: cfg.context_events]:
            scored.append(
                {
                    k: event[k]
                    for k in ("event_id", "ts", "reason", "message")
                    if k in event
                }
            )
        remaining = max(0, cfg.context_chars - (len(reply["text"]) if reply else 0))
        bounded_events, bounded_turns = [], []
        # 证据摘要跨日保留优先级，余量用于最近相关发言；不截断当前证据。
        for item in scored:
            cost = len(json.dumps(item, ensure_ascii=False))
            if cost <= remaining:
                bounded_events.append(item)
                remaining -= cost
        for item in reversed(turns[-cfg.context_turns :] if cfg.context_turns else []):
            cost = len(json.dumps(item, ensure_ascii=False))
            if cost <= remaining:
                bounded_turns.append(item)
                remaining -= cost
        return json.dumps(
            dict(
                target_bot=interaction.bot_id,
                sender=interaction.user_id,
                text=interaction.text,
                trusted_metadata=dict(
                    bot_id=interaction.bot_id,
                    group_id=interaction.group_id,
                    sender_id=interaction.user_id,
                    message_id=interaction.message_id,
                    reply=reply,
                ),
                recent_turns=list(reversed(bounded_turns)),
                scored_events=bounded_events,
            ),
            ensure_ascii=False,
        )

    async def _reserve_call(self, interaction, tier, provider_id):
        cfg = self.config
        user_cap = getattr(cfg, f"per_user_daily_{tier}_cap")
        bot_cap = getattr(cfg, f"per_bot_daily_{tier}_cap")
        async with self.capacity.budget_lock:
            stores = list(await self.budget_stores())
            if self.storage not in stores:
                stores.append(self.storage)
            # 同一个稳定消息在切换数据空间后也不得再次调用同一级模型。
            for store in stores:
                if await store.assessment_call_exists(interaction.event_id, tier):
                    return None
            counts = [
                await store.assessment_call_counts(
                    interaction.user_id, tier, window_sec=cfg.window_sec
                )
                for store in stores
            ]
            if (
                sum(c[0] for c in counts) >= bot_cap
                or sum(c[1] for c in counts) >= user_cap
                or (
                    tier == "small"
                    and sum(c[2] for c in counts) >= cfg.window_max_calls
                )
            ):
                return None
            return await self.storage.reserve_assessment_call(
                interaction.event_id,
                interaction.group_id,
                interaction.user_id,
                tier,
                provider_id,
                user_cap,
                bot_cap,
                window_sec=cfg.window_sec,
                window_max_calls=cfg.window_max_calls,
            )

    async def _call(self, provider_id, timeout, interaction, tier, prompt):
        started = time.perf_counter() if self.favor.diagnostics_enabled else 0

        async def finish(call_id, status, tokens):
            await self.storage.finish_assessment_call(call_id, status, tokens)
            if self.favor.diagnostics_enabled:
                self.favor.record_diagnostic(
                    interaction.group_id,
                    interaction.user_id,
                    "call",
                    event_id=interaction.event_id,
                    tier=tier,
                    status=status,
                    tokens=tokens,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                )

        # 无 Provider 时没有实际调用，不消耗预算。
        try:
            provider = self.context.get_provider_by_id(provider_id)
        except Exception:
            return None
        if provider is None:
            return None
        call_id = await self._reserve_call(interaction, tier, provider_id)
        if call_id is None:
            self.favor.record_diagnostic(
                interaction.group_id,
                interaction.user_id,
                "call",
                event_id=interaction.event_id,
                tier=tier,
                status="budget",
            )
            return None
        try:
            capture_content("evaluation", {"tier": tier, "prompt": prompt})
            response = await asyncio.wait_for(
                provider.text_chat(prompt=prompt), timeout
            )
            capture_content(
                "evaluation",
                {"tier": tier, "output": getattr(response, "completion_text", None)},
            )
            usage = getattr(response, "usage", None)
            tokens = getattr(usage, "total", None) if usage is not None else None
            try:
                result = parse_verdict(response.completion_text, interaction)
            except (ValueError, TypeError):
                await finish(call_id, "invalid", tokens)
                return None
            await finish(call_id, "ok", tokens)
            return result
        except asyncio.CancelledError:
            await finish(call_id, "cancelled", None)
            raise
        except Exception as exc:
            status = "timeout" if isinstance(exc, asyncio.TimeoutError) else "failed"
            await finish(call_id, status, None)
            return None

    async def assess(
        self, interaction: Interaction, *, directed: bool, admission_state=None
    ):
        if not self.favor.diagnostics_enabled:
            return await self._assess(
                interaction, directed=directed, admission_state=admission_state
            )
        started, status, trace = time.perf_counter(), "failed", {}
        capture = interaction_capture(self.favor, interaction)
        inherited = CURRENT.get() is capture and capture is not None
        token = CURRENT.set(capture)
        if not inherited and directed:
            capture_content(
                "chat", {"text": interaction.text, "reply_text": interaction.reply_text}
            )
        try:
            status = await self._assess(
                interaction,
                directed=directed,
                admission_state=admission_state,
                trace=trace,
            )
            return status
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        finally:
            self.favor.record_diagnostic(
                interaction.group_id,
                interaction.user_id,
                "assessment",
                event_id=interaction.event_id,
                status=status,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                **trace,
            )
            CURRENT.reset(token)

    async def _assess(
        self,
        interaction: Interaction,
        *,
        directed: bool,
        admission_state=None,
        trace=None,
    ):
        cfg = self.config
        if interaction.bot_id != self.storage.bot_id:
            raise ValueError("评估 Bot 与数据库身份不符")
        if not cfg.enabled:
            return "disabled"
        if not locally_relevant(interaction.text, directed):
            if trace is not None:
                trace["reason"] = "not_directed" if not directed else "filtered"
            await self.storage.record_assessment_skip(interaction, "filtered")
            return "filtered"
        temporary = await self.favor.temporary_state(
            interaction.group_id, interaction.user_id
        )
        if temporary["active"] or (
            admission_state is not None
            and (
                admission_state["active"]
                or admission_state["revision"] != temporary["revision"]
            )
        ):
            await self.storage.record_assessment_skip(interaction, "temporary")
            return "temporary"
        key = (self.favor.group_key(interaction.group_id), interaction.user_id)
        if key in self.pending or self.capacity.active >= self.capacity.limit:
            await self.storage.record_assessment_skip(interaction, "busy")
            return "busy"
        self.pending.add(key)
        self.capacity.active += 1
        claimed = False
        try:
            claimed = await self.storage.claim_assessment(
                interaction.event_id, key[0], key[1], 0
            )
            if not claimed:
                return "duplicate_or_cooldown"
            # 同一事件的小模型与复核共享一次模板快照；损坏配置不触发调用。
            instructions = (
                self.prompts.resolve(
                    "assessment", interaction.bot_id, interaction.group_id
                )
                if self.prompts is not None
                else SYSTEM_PROMPT
            )
            payload = await self._snapshot(interaction)
            frozen_prompt = instructions + TRUST_CONTRACT + "\n输入：" + payload
            verdict = await self._call(
                cfg.small_provider_id,
                cfg.small_timeout_sec,
                interaction,
                "small",
                frozen_prompt,
            )
            if verdict is None:
                status = "failed"
            else:
                first_verdict = verdict
                trust_evidence = None
                review = verdict.should_update and needs_review(
                    verdict, interaction.text
                )
                current = await self.favor.temporary_state(
                    interaction.group_id, interaction.user_id
                )
                if current["active"] or current["revision"] != temporary["revision"]:
                    await self.storage.finish_assessment(
                        interaction.event_id, "temporary"
                    )
                    return "temporary"
                if review:
                    verdict = (
                        await self._call(
                            cfg.large_provider_id,
                            cfg.large_timeout_sec,
                            interaction,
                            "large",
                            frozen_prompt,
                        )
                        if cfg.assessment_mode == "tiered" and cfg.large_provider_id
                        else None
                    )
                if verdict is not None and (
                    first_verdict.event_type in TRUST_EVENTS
                    or verdict.event_type in TRUST_EVENTS
                ):
                    try:
                        # 单模型模式、复核升级为新的信任类别或引用不一致均不计 T。
                        if not review:
                            raise ValueError("缺少独立复核")
                        trust_evidence = verify_trust_evidence(
                            interaction, first_verdict, verdict, payload
                        )
                    except (ValueError, KeyError, TypeError):
                        verdict = None
                if trace is not None and verdict is not None:
                    trace.update(
                        event_type=verdict.event_type, confidence=verdict.confidence
                    )
                if (
                    verdict is None
                    or verdict.confidence < 0.8
                    or verdict.event_type == "uncertain"
                    or not verdict.should_update
                ):
                    status = "abstained"
                else:
                    change = await self.favor.apply_relation_event(
                        interaction.group_id,
                        interaction.user_id,
                        verdict.event_type,
                        event_id=(
                            trust_evidence.event_id
                            if trust_evidence
                            else interaction.event_id
                        ),
                        reason=verdict.event_type,
                        message=verdict.evidence,
                        # 初评窗口由持久化调用账本控制；提交仅检查稳定 ID、额度及临时关系修订。
                        cooldown_sec=0,
                        expected_revision=temporary["revision"],
                        trust_evidence=trust_evidence,
                    )
                    status = (
                        "accepted"
                        if not change["skipped"] and any(change["delta"].values())
                        else "unchanged"
                    )
            await self.storage.finish_assessment(interaction.event_id, status)
            return status
        except asyncio.CancelledError:
            if claimed:
                await self.storage.finish_assessment(interaction.event_id, "cancelled")
            raise
        except Exception:
            if claimed:
                await self.storage.finish_assessment(interaction.event_id, "failed")
            return "failed"
        finally:
            self.pending.discard(key)
            self.capacity.active -= 1

    async def stats(self):
        return await self.storage.assessment_stats()
