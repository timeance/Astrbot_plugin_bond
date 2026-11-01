"""Bond 跨插件只读查询；调用者必须明确 Bot 与作用域。"""

from __future__ import annotations
from ..core.scope import account_id, scope_mode


class BondFacade:
    def __init__(self, runtime):
        self._runtime = runtime

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
            "level": deps.favor.level_of(rec.favor).name,
        }

    async def get_ranking(self, bot_id, mode, group_id, limit=10):
        account_id(group_id)
        bot_id, mode = account_id(bot_id), scope_mode(mode)
        if bot_id not in self._runtime.storage.known_bot_ids():
            raise ValueError("未知 Bot")
        deps = await self._runtime.select(bot_id, mode)
        rows = await deps.favor.ranking(group_id, min(10, max(1, int(limit))))
        return [
            {"user_id": r.user_id, "level": deps.favor.level_of(r.favor).name}
            for r in rows
        ]
