import asyncio
import copy
import json
from pathlib import Path
import pytest
from astrbot_plugin_bond.core.settings import (
    SettingsRevisionError,
    fields,
    snapshot,
    validate_patch,
)
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
    assert data["values"]["inject.enabled"] is False
    assert data["values"]["assessment.enabled"] is False
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
        {"inject.enabled": "true"},
        {"assessment.enabled": 1},
        {"assessment.assessment_mode": "unknown"},
        {"ignored_bot_ids": ["not-an-account"]},
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
        with pytest.raises(SettingsRevisionError):
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


def test_quickstart_field_order_unique_groups_and_provider_metadata():
    definitions = fields()
    assert [f["path"] for f in definitions if f["group"] == "basic"] == [
        "assessment.enabled",
        "assessment.assessment_mode",
        "assessment.small_provider_id",
        "assessment.large_provider_id",
        "inject.enabled",
        "default_scope_mode",
        "group_whitelist",
        "ignored_bot_ids",
        "master_ids",
        "relation_mode",
    ]
    by_path = {f["path"]: f for f in definitions}
    assert len(by_path) == len(definitions)
    for path in (
        "assessment.small_provider_id",
        "assessment.large_provider_id",
        "judge.provider_id",
    ):
        assert by_path[path]["special"] == "select_provider"
    assert by_path["inject.memory_count"]["group"] == "memory"


def test_quickstart_save_merges_assessment_and_keeps_loaded_scope():
    async def run():
        config = Config(
            assessment={"cooldown_sec": 45, "max_concurrent_assessments": 4},
            inject={"memory_days": 8},
        )
        runtime = RuntimeService(None, config, None)
        values = {
            "assessment.enabled": True,
            "assessment.assessment_mode": "single",
            "assessment.small_provider_id": "daily",
            "assessment.large_provider_id": "",
            "inject.enabled": True,
            "default_scope_mode": "global",
            "group_whitelist": ["10001"],
            "ignored_bot_ids": ["20002"],
            "master_ids": "30003",
            "relation_mode": "galgame",
        }
        saved = await runtime.save_detailed_settings(
            values, runtime.detailed_settings()["revision"]
        )
        assert {path: saved["values"][path] for path in values} == values
        assert config["assessment"]["cooldown_sec"] == 45
        assert config["assessment"]["max_concurrent_assessments"] == 4
        assert config["inject"] == {"memory_days": 8, "enabled": True}
        assert config.saved == config
        assert runtime.current_mode("40004") == "per_group"
        assert runtime.group_enabled("50005")
        reloaded = RuntimeService(None, config, None)
        assert reloaded.current_mode("40004") == "global"
        assert not reloaded.group_enabled("50005")

    asyncio.run(run())


@pytest.mark.parametrize(
    "values",
    [
        {"assessment.enabled": True},
        {"assessment.enabled": True, "assessment.small_provider_id": "  "},
    ],
)
def test_quickstart_requires_daily_model_without_partial_update(values):
    async def run():
        config = Config(inject={"enabled": False})
        runtime = RuntimeService(None, config, None)
        before = copy.deepcopy(dict(config))
        revision = runtime.detailed_settings()["revision"]
        with pytest.raises(ValueError, match="small_provider_id"):
            await runtime.save_detailed_settings(
                {"inject.enabled": True, **values}, revision
            )
        assert config == before
        assert not hasattr(config, "saved")
        assert runtime.detailed_settings()["revision"] == revision

    asyncio.run(run())


def test_models_and_details_share_revision_and_merge_preserved_fields():
    async def run():
        config = Config(assessment={"small_provider_id": "daily", "cooldown_sec": 60})
        runtime = RuntimeService(None, config, None)
        revision = runtime.detailed_settings()["revision"]
        new_revision = await runtime.save_assessment_settings(
            {"assessment_mode": "single"}, 5, revision
        )
        assert new_revision == runtime.detailed_settings()["revision"] != revision
        assert config["assessment"]["small_provider_id"] == "daily"
        assert config["assessment"]["cooldown_sec"] == 60
        with pytest.raises(SettingsRevisionError, match="设置已变更"):
            await runtime.save_detailed_settings({"inject.enabled": True}, revision)
        saved = await runtime.save_detailed_settings(
            {"assessment.enabled": True}, new_revision
        )
        with pytest.raises(SettingsRevisionError, match="设置已变更"):
            await runtime.save_assessment_settings({}, 6, new_revision)
        assert saved["revision"] == runtime.detailed_settings()["revision"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        {"assessment.cooldown_sec": 150},
        {"assessment.max_concurrent_assessments": 3},
        {"assessment.large_timeout_sec": 45},
        {"assessment.per_bot_daily_large_cap": 40},
        {"assessment.window_sec": 240},
        {"assessment.window_max_calls": 4},
        {"assessment.per_user_daily_small_cap": 25},
        {"assessment.context_chars": 3000},
        {"relation_daily_caps.familiarity_up": 6},
        {"familiarity_growth.under10": 1.6},
        {"decay.trust_half_life": 200},
        {"ignored_bot_ids": ["10001"]},
        {"inject.memory_days": 8},
    ],
)
def test_host_configuration_changes_invalidate_both_editors(change):
    async def run():
        config = Config(assessment={}, inject={})
        runtime = RuntimeService(None, config, None)
        revision = runtime.detailed_settings()["revision"]
        for path, value in change.items():
            target = config
            parts = path.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        before = copy.deepcopy(dict(config))
        with pytest.raises(SettingsRevisionError, match="设置已变更"):
            await runtime.save_detailed_settings({"inject.enabled": True}, revision)
        with pytest.raises(SettingsRevisionError, match="设置已变更"):
            await runtime.save_assessment_settings({}, 5, revision)
        assert config == before

    asyncio.run(run())


@pytest.mark.parametrize(
    "values,limit",
    [
        ({}, 0),
        ({}, 33),
        ({}, True),
        ({}, 1.5),
        ({}, None),
        (None, 2),
        ({"unknown": 1}, 2),
        ({"enabled": True}, 2),
        ({"cooldown_sec": 0}, 2),
        ({"small_timeout_sec": float("nan")}, 2),
        ({"per_bot_daily_large_cap": True}, 2),
    ],
)
def test_models_invalid_patch_does_not_mutate_config(values, limit):
    async def run():
        config = Config()
        runtime = RuntimeService(None, config, None)
        revision = runtime.detailed_settings()["revision"]
        with pytest.raises((ValueError, TypeError)):
            await runtime.save_assessment_settings(values, limit, revision)
        assert config == {}
        assert not hasattr(config, "saved")

    asyncio.run(run())


@pytest.mark.parametrize("initial", [{}, {"assessment": {"cooldown_sec": 60}}])
def test_models_failed_persistence_restores_exact_config_and_revision(initial):
    async def run():
        config = Config(copy.deepcopy(initial))
        config.fail = True
        runtime = RuntimeService(None, config, None)
        revision = runtime.detailed_settings()["revision"]
        with pytest.raises(OSError):
            await runtime.save_assessment_settings({"cooldown_sec": 90}, 4, revision)
        assert config == initial
        assert runtime.detailed_settings()["revision"] == revision

    asyncio.run(run())


def test_concurrent_editors_reject_second_save_after_lock_acquisition():
    async def run():
        config = Config()
        runtime = RuntimeService(None, config, None)
        revision = runtime.detailed_settings()["revision"]
        async with runtime._lock:
            details = asyncio.create_task(
                runtime.save_detailed_settings({"inject.enabled": True}, revision)
            )
            models = asyncio.create_task(
                runtime.save_assessment_settings({"cooldown_sec": 90}, 5, revision)
            )
            await asyncio.sleep(0)
            assert not details.done() and not models.done()
        result = await asyncio.gather(details, models, return_exceptions=True)
        assert isinstance(result[0], dict)
        assert isinstance(result[1], SettingsRevisionError)
        assert config == {"inject": {"enabled": True}}

    asyncio.run(run())


def test_framework_defaults_custom_values_and_single_assessment_entry():
    data = snapshot({})
    values = data["values"]
    expected = {
        "relation_mode": "galgame",
        "relation_daily_caps.familiarity_up": 5,
        "relation_daily_caps.familiarity_down": 0,
        "relation_daily_caps.affinity_up": 10,
        "relation_daily_caps.affinity_down": 8,
        "relation_daily_caps.trust_up": 2,
        "relation_daily_caps.trust_down": 5,
        "decay.enabled": True,
        "decay.half_life_base": 60,
        "decay.trust_enabled": True,
        "decay.trust_half_life": 180,
    }
    for key, value in expected.items():
        assert values[key] == value
    assert [
        values["familiarity_growth." + key]
        for key in ("under10", "under30", "under55", "under80", "under95", "at95")
    ] == [1.5, 1.2, 0.8, 0.5, 0.3, 0.15]
    assert {key for key in values if key.startswith("assessment.")} == {
        "assessment.enabled",
        "assessment.assessment_mode",
        "assessment.small_provider_id",
        "assessment.large_provider_id",
    }
    custom = snapshot(
        {
            "relation_mode": "realistic",
            "relation_daily_caps": {"affinity_up": 12},
            "familiarity_growth": {"under10": 1.1},
            "decay": {"trust_enabled": False, "half_life_base": 75},
        }
    )["values"]
    assert custom["relation_mode"] == "realistic"
    assert custom["relation_daily_caps.affinity_up"] == 12
    assert custom["familiarity_growth.under10"] == 1.1
    assert custom["decay.half_life_base"] == 75
    assert custom["decay.trust_enabled"] is False


@pytest.mark.parametrize(
    "path,value",
    [
        ("relation_daily_caps.affinity_up", -1),
        ("relation_daily_caps.trust_down", True),
        ("familiarity_growth.at95", float("inf")),
        ("decay.trust_half_life", 0),
    ],
)
def test_framework_invalid_values(path, value):
    with pytest.raises(ValueError):
        validate_patch({path: value})


def test_saved_relation_settings_do_not_replace_loaded_runtime_until_reload():
    async def run():
        config = Config(
            relation_mode="realistic",
            relation_daily_caps={"affinity_up": 12},
            master_ids="8001",
            assessment={"small_provider_id": "chosen"},
        )
        runtime = RuntimeService(None, config, None)
        initial = runtime.detailed_settings()
        await runtime.save_detailed_settings(
            {"relation_mode": "galgame", "relation_daily_caps.affinity_up": 15},
            initial["revision"],
        )
        saved = runtime.detailed_settings()
        assert saved["values"]["relation_mode"] == "galgame"
        assert saved["values"]["relation_daily_caps.affinity_up"] == 15
        assert saved["active_values"]["relation_mode"] == "realistic"
        assert saved["active_values"]["relation_daily_caps.affinity_up"] == 12
        assert config["assessment"]["small_provider_id"] == "chosen"
        assert config["master_ids"] == "8001"
        reloaded = RuntimeService(None, config, None).detailed_settings()
        assert reloaded["active_values"] == reloaded["values"]

    asyncio.run(run())


def test_assessment_schema_and_saved_defaults_match_running_configuration():
    from dataclasses import asdict
    from astrbot_plugin_bond.services.assessment_service import AssessmentConfig

    schema = json.loads(
        (Path(__file__).parents[1] / "_conf_schema.json").read_text(encoding="utf-8")
    )["assessment"]["items"]
    defaults = asdict(AssessmentConfig())
    keys = (
        "window_sec",
        "window_max_calls",
        "per_user_daily_small_cap",
        "per_user_daily_large_cap",
        "per_bot_daily_small_cap",
        "per_bot_daily_large_cap",
        "context_turns",
        "context_chars",
        "context_days",
        "context_events",
    )
    for key in keys:
        assert schema[key]["default"] == defaults[key]
        assert schema[key]["invisible"] is True
    assert schema["cooldown_sec"]["invisible"] is True

    async def run():
        config = Config(
            assessment={
                "enabled": True,
                "small_provider_id": "chosen",
                "large_provider_id": "review",
                "assessment_mode": "single",
                "window_sec": 240,
                "context_chars": 3000,
            }
        )
        runtime = RuntimeService(None, config, None)
        configured, limit = runtime.assessment_settings()
        assert configured["window_sec"] == 240
        assert configured["context_chars"] == 3000
        assert limit == 2
        await runtime.save_assessment_settings(
            {key: defaults[key] for key in keys},
            2,
            runtime.detailed_settings()["revision"],
        )
        assert config["assessment"]["small_provider_id"] == "chosen"
        assert config["assessment"]["large_provider_id"] == "review"
        assert config["assessment"]["enabled"] is True
        assert config["assessment"]["assessment_mode"] == "single"
        assert config["assessment"]["context_chars"] == 4000
        before = copy.deepcopy(config)
        config.fail = True
        with pytest.raises(OSError):
            await runtime.save_assessment_settings(
                {"window_sec": 600}, 3, runtime.detailed_settings()["revision"]
            )
        assert config == before

    asyncio.run(run())


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    "preset,expected",
    [
        ("galgame", (0.3, 1.2, 0.15, 0.9, 2.5, 36.0, 0.7)),
        ("realistic", (0.6, 1.8, 0.35, 0.6, 1.8, 72.0, 0.35)),
    ],
)
def test_legacy_economy_preset_snapshot_matches_base_parameters(
    enabled, preset, expected
):
    config = {"economy": {"preset": preset, "enabled": enabled}}
    before = copy.deepcopy(config)
    data = snapshot(config)
    paths = (
        "noise_floor",
        "negative_weight",
        "same_day_decay",
        "level_mult.youhao",
        "repair_threshold",
        "repair_hours",
        "repair_factor",
    )
    assert tuple(data["values"]["economy." + path] for path in paths) == expected
    assert data["values"]["economy.enabled"] is enabled
    definitions = {field["path"]: field for field in data["fields"]}
    assert definitions["economy.negative_weight"]["default"] == 1.0
    assert definitions["economy.level_mult.youhao"]["default"] == 0.75
    assert definitions["economy.repair_threshold"]["default"] == 0.0
    assert config == before


@pytest.mark.parametrize("level_key", ["youhao", "友好"])
def test_legacy_economy_snapshot_preserves_explicit_overrides(level_key):
    config = {
        "economy": {
            "preset": "galgame",
            "enabled": False,
            "negative_weight": 1.7,
            "repair_threshold": 0,
            "level_mult": {level_key: 0.83},
        }
    }
    before = copy.deepcopy(config)
    values = snapshot(config)["values"]
    assert values["economy.negative_weight"] == 1.7
    assert values["economy.repair_threshold"] == 0
    assert values["economy.level_mult.youhao"] == 0.83
    assert values["economy.level_mult.qinmi"] == 0.75
    assert values["economy.enabled"] is False
    assert config == before


def test_legacy_economy_restore_defaults_saves_recommended_parameters():
    from astrbot_plugin_bond.core.level_economy import EconomyConfig

    async def run():
        config = Config(
            economy={"preset": "galgame"},
            assessment={"small_provider_id": "chosen"},
            master_ids="8001",
        )
        runtime = RuntimeService(None, config, None)
        initial = runtime.detailed_settings()
        patch = {
            field["path"]: field["default"]
            for field in initial["fields"]
            if field["path"].startswith("economy.")
            and field["default"] != initial["values"][field["path"]]
        }
        assert patch["economy.negative_weight"] == 1.0
        assert patch["economy.level_mult.youhao"] == 0.75
        await runtime.save_detailed_settings(patch, initial["revision"])
        expected = EconomyConfig.from_config({})
        assert EconomyConfig.from_config(config["economy"]) == expected
        saved = runtime.detailed_settings()
        assert saved["values"]["economy.negative_weight"] == 1.0
        assert saved["active_values"]["economy.negative_weight"] == 1.2
        reloaded = RuntimeService(None, config, None).detailed_settings()
        assert reloaded["active_values"] == saved["values"]
        assert config["economy"]["preset"] == "galgame"
        assert config["assessment"]["small_provider_id"] == "chosen"
        assert config["master_ids"] == "8001"

    asyncio.run(run())
