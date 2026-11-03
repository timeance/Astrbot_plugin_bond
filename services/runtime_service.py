"""按真实 Bot 与固定作用域装配原有服务；不保留默认数据库。"""

from __future__ import annotations

import asyncio
import json

from ..core.scope import account_id, scope_mode


class RuntimeService:
    def __init__(
        self, storage, config, factory, *, bot_discovery=None, diagnostics=None
    ) -> None:
        self.diagnostics = diagnostics
        self._bot_discovery = bot_discovery
        self.storage = storage
        self._config = config
        from ..core.settings import snapshot
        from copy import deepcopy

        self._active_detail_values = deepcopy(snapshot(config)["values"])
        self._factory = factory
        self._bundles = {}
        self._lock = asyncio.Lock()
        self._observed_bot_ids = set()
        self._names_lock = asyncio.Lock()
        self._names_refreshed = {}
        self._default_mode = scope_mode(config.get("default_scope_mode", "per_group"))
        whitelist = config.get("group_whitelist", [])
        if not isinstance(whitelist, list):
            raise ValueError("群白名单必须是群号列表")
        self._group_whitelist = frozenset(account_id(item) for item in whitelist)

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
        return self.modes().get(account_id(bot_id), self._default_mode)

    async def select(self, bot_id: str, mode: str | None = None):
        bot_id = account_id(bot_id)
        mode = scope_mode(mode or self.current_mode(bot_id))
        async with self._lock:
            key = (bot_id, mode)
            if key not in self._bundles:
                backend = await self.storage.get(bot_id, mode)
                self._bundles[key] = self._factory(backend, mode)
            return self._bundles[key]

    def observe_bot(self, event):
        """仅信任宿主事件的真实账号，私聊也可登记；不为发现账号创建数据库。"""
        self._observed_bot_ids.add(account_id(str(event.get_self_id() or "")))

    def group_enabled(self, group_id):
        group_id = account_id(group_id)
        return not self._group_whitelist or group_id in self._group_whitelist

    async def for_event(self, event):
        self.observe_bot(event)
        group_id = str(event.get_group_id() or "")
        if not group_id or not self.group_enabled(group_id):
            return None
        # 调用时捕获模式，之后后台任务只持该 bundle，不再读取可变配置。
        account_id(str(event.get_sender_id() or ""))
        return await self.select(str(event.get_self_id() or ""))

    async def switch_mode(self, bot_id: str, mode: str) -> None:
        bot_id, mode = account_id(bot_id), scope_mode(mode)
        if bot_id not in self.known_bot_ids():
            raise ValueError("请选择当前已连接或已有本地记录的 Bot")
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

    async def restore(self, bot_id, mode, backup_name):
        bot_id, mode = account_id(bot_id), scope_mode(mode)
        async with self._lock:
            await self.storage.restore(bot_id, mode, backup_name)
            bundle = self._bundles.get((bot_id, mode))
            if bundle is not None:
                bundle.favor.invalidate_cache()

    def assessment_settings(self):
        from .assessment_service import AssessmentConfig
        from dataclasses import asdict

        raw = dict(self._config.get("assessment") or {})
        limit = raw.pop("max_concurrent_assessments", 2)
        return asdict(AssessmentConfig(**raw)), limit

    async def save_assessment_settings(self, values, limit):
        from .assessment_service import AssessmentConfig, AssessmentCapacity
        from dataclasses import asdict

        if not isinstance(values, dict):
            raise ValueError("评估设置须为对象")
        current = dict(self._config.get("assessment") or {})
        current.pop("max_concurrent_assessments", None)
        validated = asdict(AssessmentConfig(**{**current, **values}))
        AssessmentCapacity(limit)
        async with self._lock:
            previous = self._config.get("assessment")
            self._config["assessment"] = dict(
                validated, max_concurrent_assessments=limit
            )
            try:
                self._config.save_config()
            except BaseException:
                self._config["assessment"] = previous
                raise
        # 已捕获的在途任务继续使用旧配置。插件重载后统一生效。

    def detailed_settings(self):
        from ..core.settings import snapshot

        return dict(snapshot(self._config), active_values=self._active_detail_values)

    async def save_detailed_settings(self, values, revision):
        from copy import deepcopy
        from ..core.settings import validate_patch

        validate_patch(values)
        async with self._lock:
            if revision != self.detailed_settings()["revision"]:
                raise ValueError("设置已变更，请刷新后重试")
            previous = deepcopy(dict(self._config))
            try:
                for path, value in values.items():
                    target = self._config
                    parts = path.split(".")
                    for part in parts[:-1]:
                        target = target.setdefault(part, {})
                    target[parts[-1]] = value
                self._config.save_config()
            except BaseException:
                self._config.clear()
                self._config.update(previous)
                raise

    def known_bot_ids(self) -> list[str]:
        ids = set(self.storage.known_bot_ids()) | self._observed_bot_ids
        if self._bot_discovery is not None:
            ids.update(self._bot_discovery.account_ids())
        return sorted(ids)

    async def describe(self) -> list[dict]:
        return [
            {"bot_id": bot_id, "scope_mode": self.current_mode(bot_id)}
            for bot_id in self.known_bot_ids()
        ]

    async def refresh_names(self, bot_id, mode, group_id, user_id):
        import time

        if self._names_lock.locked():
            raise ValueError("正在刷新昵称，请稍后重试")
        key = (bot_id, mode, group_id, user_id)
        async with self._names_lock:
            if time.monotonic() - self._names_refreshed.get(key, -100) < 30:
                raise ValueError("请间隔 30 秒再刷新")
            if self._bot_discovery is None:
                raise ValueError("平台资料查询不可用")
            result = await self._bot_discovery.fetch_names(bot_id, group_id, user_id)
            nickname = result.get("nickname")
            card = result.get("card") if group_id else None
            if nickname is not None and not isinstance(nickname, str):
                raise ValueError("平台昵称格式无效")
            if card is not None and not isinstance(card, str):
                raise ValueError("平台群名片格式无效")
            deps = await self.select(bot_id, mode)
            await deps.favor.update_member_names(group_id, user_id, nickname, card)
            if len(self._names_refreshed) >= 4096:
                self._names_refreshed.clear()
            self._names_refreshed[key] = time.monotonic()
