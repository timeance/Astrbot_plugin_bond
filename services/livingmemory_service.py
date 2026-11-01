"""LivingMemory 可选探测服务；当前只做状态探测和本地安全降级。"""

from __future__ import annotations

from typing import Any

LIVINGMEMORY_PLUGIN_NAME = "astrbot_plugin_livingmemory"
_OFF = {"enabled": False, "read_enabled": False, "write_enabled": False}


class LivingMemoryService:
    def __init__(self, context: Any, config: dict[str, Any]) -> None:
        self.context = context
        self.config = config

    @staticmethod
    def validate_config(config: dict[str, Any]) -> dict[str, bool]:
        if not isinstance(config, dict):
            raise ValueError("LivingMemory 配置必须是对象")
        raw = config.get("livingmemory")
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError("livingmemory 配置必须是对象")
        switches = {
            name: raw.get(name, False)
            for name in ("enabled", "read_enabled", "write_enabled")
        }
        for name, value in switches.items():
            if type(value) is not bool:
                raise ValueError(f"livingmemory.{name} 必须是 bool")
        return switches

    def status(self, *, check: bool = False) -> dict[str, Any]:
        try:
            switches = self.validate_config(self.config)
        except ValueError as exc:
            return self._state(
                _OFF,
                status="invalid_config",
                reason_code="invalid_config",
                reason=str(exc),
                capability_reason=str(exc),
            )
        if check or switches["enabled"]:
            return self._state(switches, **self._discover())
        return self._state(
            switches,
            status="standalone",
            reason_code="config_disabled",
            reason="livingmemory.enabled=false，保持 standalone",
        )

    async def recall(
        self,
        *,
        query: str,
        bot_id: str,
        group_id: str | None = None,
        user_id: str | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        del query, bot_id, group_id, user_id, limit
        return self._operation_result("recall", "read", {"items": []})

    async def memorize(
        self,
        *,
        memory: str,
        bot_id: str,
        group_id: str | None = None,
        user_id: str | None = None,
        source_id: str,
        event_id: str,
    ) -> dict[str, Any]:
        del memory, bot_id, group_id, user_id, source_id, event_id
        return self._operation_result("memorize", "write", {"written": False})

    def _operation_result(
        self, operation: str, side: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        state = self.status()
        return {
            "operation": operation,
            "status": state["status"],
            "reason_code": state["reason_code"],
            "available": False,
            **payload,
            "reason": state[side]["reason"],
            "state": state,
        }

    @staticmethod
    def _discovery(
        status: str,
        code: str,
        reason: str,
        found: bool = False,
        active: bool = False,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "reason_code": code,
            "reason": reason,
            "plugin_found": found,
            "plugin_active": active,
        }

    def _discover(self) -> dict[str, Any]:
        try:
            metadata = self.context.get_registered_star(LIVINGMEMORY_PLUGIN_NAME)
            if metadata is None:
                return self._discovery(
                    "missing", "plugin_missing", "未发现已注册的 LivingMemory 插件"
                )
            if metadata.activated is not True:
                return self._discovery(
                    "disabled", "plugin_disabled", "LivingMemory 已注册但未激活", True
                )
            if metadata.star_cls is None:
                return self._discovery(
                    "unsupported_scope",
                    "instance_unavailable",
                    "LivingMemory 已激活但没有可用实例",
                    True,
                )
        except Exception:
            return self._discovery(
                "error",
                "discovery_error",
                "发现 LivingMemory 或读取 StarMetadata 时发生异常，已安全降级",
            )
        return self._discovery(
            "unsupported_scope",
            "public_api_missing",
            "当前 LivingMemory 只注册模型工具，没有能证明 Bond Bot/group 来源的公开服务 API",
            True,
            True,
        )

    @staticmethod
    def _state(
        switches: dict[str, bool],
        *,
        status: str,
        reason_code: str,
        reason: str,
        plugin_found: bool = False,
        plugin_active: bool = False,
        capability_reason: str | None = None,
    ) -> dict[str, Any]:
        def capability(name: str, enabled: bool) -> dict[str, Any]:
            detail = capability_reason or (
                "livingmemory.enabled=false"
                if not switches["enabled"]
                else f"livingmemory.{name}_enabled=false" if not enabled else reason
            )
            return {"enabled": enabled, "available": False, "reason": detail}

        return {
            "config": dict(switches),
            "mode": "standalone",
            "status": status,
            "reason_code": reason_code,
            "reason": reason,
            "plugin": {
                "name": LIVINGMEMORY_PLUGIN_NAME,
                "found": plugin_found,
                "active": plugin_active,
            },
            "read": capability("read", switches["read_enabled"]),
            "write": capability("write", switches["write_enabled"]),
        }
