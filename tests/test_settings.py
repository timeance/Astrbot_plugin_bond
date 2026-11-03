import asyncio
import copy
import json
from pathlib import Path
import pytest
from astrbot_plugin_bond.core.settings import fields, snapshot, validate_patch
from astrbot_plugin_bond.services.runtime_service import RuntimeService


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
    assert "livingmemory" not in schema
    assert schema["levels"]["invisible"] is True
    assert not schema["assessment"]["items"]["enabled"].get("invisible")
    assert not schema["master_ids"]["invisible"]
    assert schema["group_whitelist"]["default"] == []
    for key in ("small_provider_id", "large_provider_id"):
        assert schema["assessment"]["items"][key]["_special"] == "select_provider"
    data = snapshot({})
    for field in data["fields"]:
        validate_patch({field["path"]: data["values"][field["path"]]})
    assert "inject.enabled" not in data["values"]
    assert "judge.enabled" not in data["values"]


@pytest.mark.parametrize(
    "values",
    [
        {"bot_scope_modes": "{}"},
        {"group_whitelist": "10001"},
        {"group_whitelist": [10001]},
        {"group_whitelist": ["10001", "fake"]},
        {"master_ids": "10001,not-an-account"},
        {"default_scope_mode": "other"},
        {"inject.enabled": True},
        {"inject.memory_days": True},
        {"inject.memory_days": float("nan")},
        {"impression.interval": 0},
        {"timezone": "bad/timezone"},
        {"economy.preset": "unknown"},
        {"relation_mode": "unknown"},
        {"command.ranking_access": "admin"},
        {"decay.baseline": 10},
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


def test_user_settings_offer_two_profiles_and_scoped_ranking_permission():
    data = snapshot({})
    by_path = {f["path"]: f for f in data["fields"]}
    assert by_path["relation_mode"]["options"] == ["realistic", "galgame"]
    assert by_path["command.ranking_access"]["options"] == ["all", "master"]
    assert data["values"]["command.ranking_access"] == "all"
    assert data["values"]["command.ranking_image"] is True
    assert "render.font_path" in by_path
    assert "decay.baseline" not in by_path and "economy.preset" not in by_path
