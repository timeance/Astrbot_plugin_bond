"""Bond 进程内接口：只读关系查询及可选的群聊防护通知。"""

from __future__ import annotations
from ..core.scope import account_id, scope_mode


class BondFacade:
    def __init__(self, runtime, context=None):
        self._runtime = runtime
        self._context = context

    async def receive_protection_fact_v1(self, ticket):
        if type(ticket) is not object or self._context is None:
            return "invalid"
        source = self._context.get_registered_star("antipromptinjector")
        take = getattr(
            getattr(source, "star_cls", None), "take_protection_fact_v1", None
        )
        if not getattr(source, "activated", False) or not callable(take):
            return "unavailable"
        return await self._runtime.receive_protection_fact(take(ticket))

    async def submit_assessment_event_v1(self, event):
        """请 Bond 评估本次平台互动，返回处理状态；不能指定分值或目标成员。"""
        from astrbot.api.event import AstrMessageEvent
        from .listeners import submit_group_candidate

        if not isinstance(event, AstrMessageEvent):
            return "invalid"
        if not self._runtime.external_assessment_enabled:
            return "disabled"
        plugins = event.plugins_name
        if plugins is not None and "astrbot_plugin_bond" not in plugins:
            return "disabled"
        if event.get_platform_name() != "aiocqhttp":
            return "ignored"
        try:
            bot, group, user = (
                account_id(event.get_self_id()),
                account_id(event.get_group_id()),
                account_id(event.get_sender_id()),
            )
        except ValueError:
            return "ignored"
        if not self._runtime.allows_member(bot, group, user):
            return "ignored"
        return await submit_group_candidate(self._runtime, event)

    async def get_profile(self, bot_id, mode, group_id, user_id):
        account_id(group_id)
        account_id(user_id)
        bot_id, mode = account_id(bot_id), scope_mode(mode)
        if bot_id not in self._runtime.storage.known_bot_ids():
            raise ValueError("未知 Bot")
        deps = await self._runtime.select(bot_id, mode)
        if mode == "global" and not await deps.favor.is_observed(group_id, user_id):
            raise ValueError("当前群没有观察到该成员")
        rec = await deps.favor.get(group_id, user_id)
        return {
            "bot_id": bot_id,
            "scope_mode": mode,
            "group_id": group_id,
            "user_id": user_id,
            "relation_title": await deps.favor.relation_title(group_id, user_id),
        }

    async def get_ranking(self, bot_id, mode, group_id, limit=10):
        account_id(group_id)
        bot_id, mode = account_id(bot_id), scope_mode(mode)
        if bot_id not in self._runtime.storage.known_bot_ids():
            raise ValueError("未知 Bot")
        deps = await self._runtime.select(bot_id, mode)
        rows = await deps.favor.ranking(group_id, min(10, max(1, int(limit))))
        return [
            {
                "user_id": r.user_id,
                "relation_title": await deps.favor.relation_title(group_id, r.user_id),
            }
            for r in rows
        ]
