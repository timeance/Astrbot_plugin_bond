import asyncio
import copy
import json
from pathlib import Path
import pytest
from astrbot_plugin_bond.core.settings import fields, snapshot, validate_patch
from astrbot_plugin_bond.services.runtime_service import RuntimeService
from astrbot_plugin_bond.services.livingmemory_service import LivingMemoryService


class Config(dict):
    fail = False

    def save_config(self):
        if self.fail:
            raise OSError("disk full")
        self.saved = copy.deepcopy(dict(self))


def test_defaults_and_hidden_fields_preserve_schema():
    schema = json.loads(
        (Path(__file__).parents[1] / "_conf_schema.json").read_text(encoding="utf-8")
    )
    assert schema["livingmemory"]["invisible"] is True
    assert schema["levels"]["invisible"] is True
    assert not schema["assessment"]["items"]["enabled"].get("invisible")
    data = snapshot({})
    for field in data["fields"]:
        validate_patch({field["path"]: data["values"][field["path"]]})
    assert "inject.enabled" not in data["values"]
    assert "judge.enabled" not in data["values"]


@pytest.mark.parametrize(
    "values",
    [
        {"bot_scope_modes": "{}"},
        {"inject.enabled": True},
        {"inject.memory_days": True},
        {"inject.memory_days": float("nan")},
        {"impression.interval": 0},
        {"timezone": "bad/timezone"},
        {"economy.preset": "unknown"},
    ],
)
def test_invalid_patch_rejected(values):
    with pytest.raises((ValueError, KeyError)):
        validate_patch(values)


def test_save_preserves_unrelated_config_rejects_stale_revision_and_rolls_back():
    async def run():
        config = Config(
            assessment={"enabled": True, "small_provider_id": "small"},
            livingmemory={"enabled": True},
            judge={"provider_id": "summary", "roster": "saved"},
        )
        runtime = RuntimeService(None, config, None)
        before = copy.deepcopy(dict(config))
        revision = runtime.detailed_settings()["revision"]
        await runtime.save_detailed_settings({"judge.provider_id": "new"}, revision)
        assert config["judge"] == {"provider_id": "new", "roster": "saved"}
        assert config["assessment"] == before["assessment"]
        assert config["livingmemory"] == before["livingmemory"]
        with pytest.raises(ValueError):
            await runtime.save_detailed_settings({"inject.memory_days": 8}, revision)
        config.fail = True
        before = copy.deepcopy(dict(config))
        with pytest.raises(OSError):
            await runtime.save_detailed_settings(
                {"inject.memory_days": 9}, runtime.detailed_settings()["revision"]
            )
        assert config == before

    asyncio.run(run())


def test_manual_memory_check_does_not_enable_integration():
    class Context:
        calls = 0

        def get_registered_star(self, name):
            self.calls += 1
            return None

    config = Config(
        livingmemory={"enabled": False, "read_enabled": True, "write_enabled": True}
    )
    context = Context()
    service = LivingMemoryService(context, config)
    before = copy.deepcopy(dict(config))
    assert service.status()["reason_code"] == "config_disabled"
    assert context.calls == 0
    assert service.status(check=True)["reason_code"] == "plugin_missing"
    assert context.calls == 1
    assert config == before
