"""受限异步评估流水线；失败停止本次评分，不切换供应商或使用旧标量结果。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from ..core.assessment import (
    Interaction,
    SYSTEM_PROMPT,
    locally_relevant,
    needs_review,
    parse_verdict,
)


@dataclass(frozen=True)
class AssessmentConfig:
    enabled: bool = False
    small_provider_id: str = ""
    large_provider_id: str = ""
    assessment_mode: str = "tiered"
    small_timeout_sec: float = 20
    large_timeout_sec: float = 30
    cooldown_sec: float = 120
    per_user_daily_large_cap: int = 2
    per_bot_daily_large_cap: int = 20

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("enabled 必须是布尔值")
        if not isinstance(self.small_provider_id, str) or not isinstance(
            self.large_provider_id, str
        ):
            raise ValueError("Provider ID 必须是字符串")
        if self.assessment_mode not in {"single", "tiered"}:
            raise ValueError("未知评估模式")
        for name in ("small_timeout_sec", "large_timeout_sec", "cooldown_sec"):
            import math

            value = getattr(self, name)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} 必须是有限正数")
        for name in ("per_user_daily_large_cap", "per_bot_daily_large_cap"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} 必须是非负整数")
        if self.enabled and not self.small_provider_id.strip():
            raise ValueError("启用评估必须指定 small_provider_id")


class AssessmentCapacity:
    """插件级在途上限；忙时丢弃候选，禁止创建无界等待队列。"""

    def __init__(self, limit=2):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("并发上限应为 1..32")
        self.limit, self.active = limit, 0
        self.budget_lock = asyncio.Lock()


class AssessmentService:
    def __init__(self, storage, favor, context, config, capacity, budget_stores):
        self.storage, self.favor, self.context = storage, favor, context
        self.config, self.capacity = config, capacity
        self.pending = set()
        self.budget_stores = budget_stores

    async def _reserve_call(
        self, event_id, group_id, user_id, tier, provider_id, user_cap, bot_cap
    ):
        async with self.capacity.budget_lock:
            if tier == "large":
                stores = await self.budget_stores()
                counts = [await store.large_call_counts(user_id) for store in stores]
                if (
                    sum(c[0] for c in counts) >= bot_cap
                    or sum(c[1] for c in counts) >= user_cap
                ):
                    return None
            return await self.storage.begin_assessment_call(
                event_id, group_id, user_id, tier, provider_id, user_cap, bot_cap
            )

    async def _call(self, provider_id, timeout, interaction, tier):
        # 调用尝试先持久化，异常/超时也消耗调用预算。
        call_id = await self._reserve_call(
            interaction.event_id,
            interaction.group_id,
            interaction.user_id,
            tier,
            provider_id,
            self.config.per_user_daily_large_cap,
            self.config.per_bot_daily_large_cap,
        )
        if call_id is None:
            return None
        try:
            provider = self.context.get_provider_by_id(provider_id)
            if provider is None:
                raise ValueError("配置的 Provider 不可用")
            payload = json.dumps(
                {
                    "target_bot": interaction.bot_id,
                    "sender": interaction.user_id,
                    "text": interaction.text,
                },
                ensure_ascii=False,
            )
            response = await asyncio.wait_for(
                provider.text_chat(prompt=SYSTEM_PROMPT + "\n输入：" + payload), timeout
            )
            usage = getattr(response, "usage", None)
            tokens = getattr(usage, "total", None) if usage is not None else None
            try:
                result = parse_verdict(response.completion_text, interaction)
            except (ValueError, TypeError):
                await self.storage.finish_assessment_call(call_id, "invalid", tokens)
                return None
            await self.storage.finish_assessment_call(call_id, "ok", tokens)
            return result
        except asyncio.CancelledError:
            await self.storage.finish_assessment_call(call_id, "cancelled", None)
            raise
        except Exception as exc:
            status = "timeout" if isinstance(exc, asyncio.TimeoutError) else "failed"
            await self.storage.finish_assessment_call(call_id, status, None)
            return None

    async def assess(self, interaction: Interaction, *, directed: bool):
        cfg = self.config
        if interaction.bot_id != self.storage.bot_id:
            raise ValueError("评估 Bot 与数据库身份不符")
        if not cfg.enabled:
            return "disabled"
        if not locally_relevant(interaction.text, directed):
            await self.storage.record_assessment_skip(interaction, "filtered")
            return "filtered"
        key = (self.favor.group_key(interaction.group_id), interaction.user_id)
        if key in self.pending or self.capacity.active >= self.capacity.limit:
            await self.storage.record_assessment_skip(interaction, "busy")
            return "busy"
        self.pending.add(key)
        self.capacity.active += 1
        claimed = False
        try:
            claimed = await self.storage.claim_assessment(
                interaction.event_id, key[0], key[1], cfg.cooldown_sec
            )
            if not claimed:
                return "duplicate_or_cooldown"
            verdict = await self._call(
                cfg.small_provider_id, cfg.small_timeout_sec, interaction, "small"
            )
            if verdict is None:
                status = "failed"
            else:
                review = needs_review(verdict, interaction.text)
                if review:
                    verdict = (
                        await self._call(
                            cfg.large_provider_id,
                            cfg.large_timeout_sec,
                            interaction,
                            "large",
                        )
                        if cfg.assessment_mode == "tiered" and cfg.large_provider_id
                        else None
                    )
                if (
                    verdict is None
                    or verdict.confidence < 0.8
                    or verdict.event_type == "uncertain"
                ):
                    status = "abstained"
                else:
                    change = await self.favor.apply_relation_event(
                        interaction.group_id,
                        interaction.user_id,
                        verdict.event_type,
                        event_id=interaction.event_id,
                        reason=verdict.event_type,
                        message=verdict.evidence,
                        # 自动评估以 claim_assessment 的入场时间为唯一冷却边界。
                        # 提交仍检查事件去重和每日限幅，不按推理完成时间二次拒绝。
                        cooldown_sec=0,
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
