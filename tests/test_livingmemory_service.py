"""LivingMemory 可选适配器的生命周期和安全降级测试。"""

import asyncio
from types import SimpleNamespace

from astrbot_plugin_bond.services.livingmemory_service import LivingMemoryService


def config(*, enabled=False, read_enabled=False, write_enabled=False):
    return {
        "livingmemory": {
            "enabled": enabled,
            "read_enabled": read_enabled,
            "write_enabled": write_enabled,
        }
    }


class Context:
    def __init__(self, current=None, error=None):
        self.current = current
        self.error = error
        self.lookups = 0

    def get_registered_star(self, name):
        assert name == "astrbot_plugin_livingmemory"
        self.lookups += 1
        if self.error is not None:
            raise self.error
        return self.current


class TrapPlugin:
    def __getattribute__(self, name):
        if name in {"api", "initializer", "memory_engine", "db"}:
            raise AssertionError(f"不应访问 LivingMemory 内部对象: {name}")
        return super().__getattribute__(name)


def metadata(*, activated=True, star=None):
    return SimpleNamespace(activated=activated, star_cls=star or TrapPlugin())


def request_kwargs():
    return {
        "query": "用户偏好",
        "bot_id": "bot-1",
        "group_id": "group-1",
        "user_id": "user-1",
    }


def memorize_kwargs():
    return {
        "memory": "用户偏好简洁回答",
        "bot_id": "bot-1",
        "group_id": "group-1",
        "user_id": "user-1",
        "source_id": "bond",
        "event_id": "event-1",
    }


def test_default_standalone_has_zero_discovery_calls():
    context = Context(metadata())
    service = LivingMemoryService(context, {})

    assert service.status()["status"] == "standalone"
    assert asyncio.run(service.recall(**request_kwargs()))["items"] == []
    assert asyncio.run(service.memorize(**memorize_kwargs()))["written"] is False
    assert context.lookups == 0


def test_invalid_config_has_explicit_status_and_management_error():
    context = Context(metadata())
    settings = config(enabled=True)
    settings["livingmemory"]["read_enabled"] = "true"
    service = LivingMemoryService(context, settings)

    state = service.status()

    assert state["status"] == "invalid_config"
    assert state["reason_code"] == "invalid_config"
    assert context.lookups == 0
    try:
        LivingMemoryService.validate_config(settings)
    except ValueError as exc:
        assert "read_enabled" in str(exc)
    else:
        raise AssertionError("invalid config must raise ValueError at management entry")


def test_missing_plugin_fails_safe():
    context = Context(None)
    service = LivingMemoryService(context, config(enabled=True, read_enabled=True))

    state = service.status()
    result = asyncio.run(service.recall(**request_kwargs()))

    assert state["status"] == "missing"
    assert state["reason_code"] == "plugin_missing"
    assert state["read"]["available"] is False
    assert result["items"] == []
    assert result["status"] == "missing"
    assert context.lookups == 2


def test_registered_but_disabled_plugin_fails_safe():
    context = Context(metadata(activated=False))
    service = LivingMemoryService(context, config(enabled=True, read_enabled=True))

    state = service.status()

    assert state["status"] == "disabled"
    assert state["reason_code"] == "plugin_disabled"
    assert state["plugin"] == {
        "name": "astrbot_plugin_livingmemory",
        "found": True,
        "active": False,
    }


def test_enabled_plugin_is_explicitly_unsupported_without_public_scope_api():
    context = Context(metadata())
    service = LivingMemoryService(
        context, config(enabled=True, read_enabled=True, write_enabled=True)
    )

    state = service.status()
    recall = asyncio.run(service.recall(**request_kwargs()))
    memorize = asyncio.run(service.memorize(**memorize_kwargs()))

    assert state["status"] == "unsupported_scope"
    assert state["reason_code"] == "public_api_missing"
    assert state["read"]["enabled"] is True
    assert state["read"]["available"] is False
    assert state["write"]["enabled"] is True
    assert state["write"]["available"] is False
    assert recall["available"] is False and recall["items"] == []
    assert memorize["available"] is False and memorize["written"] is False
    assert context.lookups == 3


def test_read_and_write_switches_are_independent():
    context = Context(metadata())
    service = LivingMemoryService(
        context, config(enabled=True, read_enabled=False, write_enabled=True)
    )

    state = service.status()

    assert state["read"]["enabled"] is False
    assert state["read"]["available"] is False
    assert state["read"]["reason"] == "livingmemory.read_enabled=false"
    assert state["write"]["enabled"] is True
    assert state["write"]["available"] is False
    assert state["write"]["reason"] == state["reason"]


def test_dynamic_disable_stops_discovery_and_requests():
    context = Context(metadata())
    settings = config(enabled=True, read_enabled=True)
    service = LivingMemoryService(context, settings)

    assert service.status()["status"] == "unsupported_scope"
    assert context.lookups == 1

    settings["livingmemory"]["enabled"] = False
    result = asyncio.run(service.recall(**request_kwargs()))

    assert result["status"] == "standalone"
    assert result["reason_code"] == "config_disabled"
    assert context.lookups == 1


def test_uninstall_and_reinstall_are_seen_without_cached_instance():
    first = metadata()
    second = metadata()
    context = Context(first)
    service = LivingMemoryService(context, config(enabled=True, read_enabled=True))

    assert service.status()["plugin"]["active"] is True
    context.current = None
    assert service.status()["status"] == "missing"
    context.current = second
    assert service.status()["plugin"]["active"] is True

    assert context.lookups == 3


def test_discovery_exception_is_a_local_error_status():
    context = Context(error=RuntimeError("registry unavailable"))
    service = LivingMemoryService(context, config(enabled=True, read_enabled=True))

    state = service.status()
    result = asyncio.run(service.memorize(**memorize_kwargs()))

    assert state["status"] == "error"
    assert state["reason_code"] == "discovery_error"
    assert result["written"] is False
    assert result["status"] == "error"


def test_memorize_does_not_touch_memory_state_or_internal_database():
    local_state = {"memory": "bond-local", "changed": False}
    plugin = TrapPlugin()
    context = Context(metadata(star=plugin))
    service = LivingMemoryService(context, config(enabled=True, write_enabled=True))

    result = asyncio.run(service.memorize(**memorize_kwargs()))

    assert result["written"] is False
    assert local_state == {"memory": "bond-local", "changed": False}
