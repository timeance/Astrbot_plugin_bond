"""群内在场熟悉度：仅缓存可信身份和收件时间，不读取聊天内容。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass

from ..core.scope import is_account_id
from ..core.passive_familiarity import PassiveFamiliarityConfig


@dataclass(frozen=True)
class ActivityBatch:
    group_id: str
    trigger_id: str
    members: tuple[tuple[str, float], ...]
    triggered_at: float


class PassiveActivity:
    """每个 Bot/空间一个轻量缓存；冷却、额度和去重由持久化事务裁决。"""

    MAX_MEMBERS = 4096
    MAX_MESSAGES = 8192
    MAX_PENDING = 16

    def __init__(
        self,
        bot_id: str,
        *,
        enabled: bool = True,
        config: PassiveFamiliarityConfig | None = None,
    ):
        self.bot_id = bot_id
        self.enabled = enabled is True
        self._window_sec = (config or PassiveFamiliarityConfig()).window_minutes * 60
        self._members: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._messages: OrderedDict[tuple[str, str, str, str], float] = OrderedDict()
        self._pending: dict[str, OrderedDict[str, tuple[str, float, float]]] = {}
        self._last_time = 0.0

    def observe(
        self,
        group_id: str,
        user_id: str,
        *,
        platform: str,
        message_id: str,
        mentioned: bool,
        has_content: bool,
        ignored_ids=(),
    ) -> ActivityBatch | None:
        """同步固定候选集合；后续到达的消息不进入本次触发。"""
        if not self.enabled or not all(
            is_account_id(value) for value in (self.bot_id, group_id, user_id)
        ):
            return None
        ignored = set(ignored_ids) | {self.bot_id}
        if user_id in ignored or not has_content:
            return None
        now = time.time()
        if now < self._last_time:
            self._members.clear()
            self._messages.clear()
        self._last_time = now
        for cache in (self._members, self._messages):
            while cache and next(iter(cache.values())) < now - self._window_sec:
                cache.popitem(last=False)
        # 缺失消息编号仍可记录在场，但不能作为可靠、可重试的奖励触发。
        identifiable = (
            isinstance(platform, str)
            and 0 < len(platform) <= 128
            and isinstance(message_id, str)
            and 0 < len(message_id.strip()) <= 200
            and message_id != "0"
        )
        if identifiable:
            message_key = (platform, group_id, user_id, message_id)
            if message_key in self._messages:
                return None
            self._messages[message_key] = now
            while len(self._messages) > self.MAX_MESSAGES:
                self._messages.popitem(last=False)
        key = (group_id, user_id)
        self._members[key] = now
        self._members.move_to_end(key)
        while len(self._members) > self.MAX_MEMBERS:
            self._members.popitem(last=False)
        if not mentioned or not identifiable:
            return None
        members = tuple(
            (member, seen)
            for (group, member), seen in self._members.items()
            if group == group_id and member != user_id and member not in ignored
        )
        if not members:
            return None
        trigger_id = hashlib.sha256(
            json.dumps(
                [self.bot_id, platform, group_id, user_id, message_id],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return ActivityBatch(group_id, trigger_id, members, now)

    def schedule(self, batch, favor, registry):
        """同群串行写入并合并新候选；有界排队，不阻塞宿主回复。"""
        if batch is None:
            return
        queue = self._pending.get(batch.group_id)
        if queue is None:
            if len(self._pending) >= self.MAX_PENDING:
                return
            queue = OrderedDict()
            self._pending[batch.group_id] = queue
            start_worker = True
        else:
            start_worker = False
        for user_id, observed_at in batch.members:
            if user_id in queue:
                if batch.triggered_at >= queue[user_id][2]:
                    queue[user_id] = (batch.trigger_id, observed_at, batch.triggered_at)
            elif len(queue) < self.MAX_MEMBERS:
                queue[user_id] = (batch.trigger_id, observed_at, batch.triggered_at)
        if not start_worker:
            return

        async def run():
            try:
                processed = 0
                while queue:
                    user_id, (trigger_id, observed_at, triggered_at) = queue.popitem(
                        last=False
                    )
                    try:
                        await favor.apply_passive_familiarity(
                            batch.group_id,
                            user_id,
                            trigger_id=trigger_id,
                            observed_at=observed_at,
                            triggered_at=triggered_at,
                        )
                    except Exception:
                        from astrbot.api import logger

                        logger.warning("[Bond] 群聊活跃熟悉度写入失败，继续正常对话")
                    processed += 1
                    if processed % 16 == 0:
                        await asyncio.sleep(0)
            finally:
                self._pending.pop(batch.group_id, None)

        task = run()
        try:
            running = registry.spawn(task, name=f"passive-familiarity:{batch.group_id}")
            # 在首次执行前取消也会释放有界队列；不会遗留无法再次调度的群。
            running.add_done_callback(
                lambda _: (
                    self._pending.pop(batch.group_id, None)
                    if self._pending.get(batch.group_id) is queue
                    else None
                )
            )
        except BaseException:
            task.close()
            self._pending.pop(batch.group_id, None)
            raise
