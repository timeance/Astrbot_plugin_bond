"""按真实 Bot 与固定作用域装配原有服务；不保留默认数据库。"""

from __future__ import annotations

import asyncio
import json

from ..core.scope import account_id, scope_mode


class RuntimeService:
    def __init__(self, storage, config, factory) -> None:
        self.storage = storage
        self._config = config
        self._factory = factory
        self._bundles = {}
        self._lock = asyncio.Lock()

    def modes(self) -> dict[str, str]:
        raw = self._config.get("bot_scope_modes", "{}") or "{}"
        modes = json.loads(raw)
        if not isinstance(modes, dict):
            raise ValueError("bot_scope_modes 必须是 JSON 对象")
        for bot_id, mode in modes.items():
            account_id(bot_id)
            scope_mode(mode)
        return modes

    def current_mode(self, bot_id: str) -> str:
        return self.modes().get(account_id(bot_id), "per_group")

    async def select(self, bot_id: str, mode: str | None = None):
        bot_id = account_id(bot_id)
        mode = scope_mode(mode or self.current_mode(bot_id))
        async with self._lock:
            key = (bot_id, mode)
            if key not in self._bundles:
                backend = await self.storage.get(bot_id, mode)
                self._bundles[key] = self._factory(backend, mode)
            return self._bundles[key]

    async def for_event(self, event):
        # 调用时捕获模式，之后后台任务只持该 bundle，不再读取可变配置。
        account_id(str(event.get_group_id() or ""))
        account_id(str(event.get_sender_id() or ""))
        return await self.select(str(event.get_self_id() or ""))

    async def switch_mode(self, bot_id: str, mode: str) -> None:
        bot_id, mode = account_id(bot_id), scope_mode(mode)
        if bot_id not in self.storage.known_bot_ids():
            raise ValueError("请选择已连接并产生可信事件的 Bot")
        async with self._lock:
            previous = self._config.get("bot_scope_modes", "{}")
            modes = self.modes()
            modes[bot_id] = mode
            self._config["bot_scope_modes"] = json.dumps(modes, sort_keys=True)
            try:
                save = getattr(self._config, "save_config", None)
                if not callable(save):
                    raise RuntimeError("宿主配置不可持久化")
                save()
            except BaseException:
                self._config["bot_scope_modes"] = previous
                raise

    async def describe(self) -> list[dict]:
        return [
            {"bot_id": bot_id, "scope_mode": self.current_mode(bot_id)}
            for bot_id in self.storage.known_bot_ids()
        ]
