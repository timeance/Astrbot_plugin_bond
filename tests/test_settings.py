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
    assert "baseline" not in schema["decay"]["items"]
    for key in ("small_provider_id", "large_provider_id"):
        assert schema["assessment"]["items"][key]["_special"] == "select_provider"
    data = snapshot({})
    for field in data["fields"]:
        validate_patch({field["path"]: data["values"][field["path"]]})
    assert data["values"]["inject.enabled"] is True
    assert data["values"]["relation_title.enabled"] is False
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
        "assessment.small_provider_id",
        "assessment.large_provider_id",
        "assessment.assessment_mode",
        "group_whitelist",
        "default_scope_mode",
        "master_ids",
        "passive_familiarity",
        "relation_mode",
        "ignored_bot_ids",
        "decay.grace_days",
        "decay.maintenance_strategy",
        "timezone",
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
    personal = [f["path"] for f in definitions if f["group"] == "titles"]
    assert personal[:2] == ["inject.enabled", "relation_title.enabled"]
    assert "relation_title.mode" not in by_path
    assert all(
        f["title"] == "个性化功能" for f in definitions if f["group"] == "titles"
    )
    assert by_path["passive_familiarity"]["label"] == "眼熟模式"
    assert by_path["passive_familiarity"]["hint"] == "群员活跃时被动增加熟悉度。"


def test_host_visibility_and_webui_wording_agree():
    schema = json.loads(
        (Path(__file__).parents[1] / "_conf_schema.json").read_text(encoding="utf-8")
    )
    visible = []

    def visit(nodes, prefix="", hidden=False):
        for key, node in nodes.items():
            path = f"{prefix}.{key}" if prefix else key
            assert node["description"]
            assert "：" not in node["description"]
            assert "。" not in node["description"]
            hidden_here = hidden or node.get("invisible", False)
            if node["type"] == "object":
                visit(node["items"], path, hidden_here)
            elif not hidden_here:
                visible.append(path)

    visit(schema)
    definitions = fields()
    assert set(visible) == {
        f["path"]
        for f in definitions
        if (
            f["group"] == "basic"
            and f["path"]
            not in {"decay.grace_days", "decay.maintenance_strategy", "timezone"}
        )
        or f["path"].startswith("command.")
        or f["group"] == "titles"
    }
    for field in definitions:
        node = schema
        for part in field["path"].split("."):
            node = node.get("items", node)[part]
        assert field["label"] == node["description"]
        assert field["hint"] == node["hint"]
    for key in (
        "relation_daily_caps",
        "familiarity_growth",
        "decay",
        "economy",
        "impression",
        "judge",
        "diagnostics",
    ):
        assert schema[key]["invisible"] is True
    assert schema["decay"]["items"]["grace_days"]["invisible"] is True
    assert schema["decay"]["items"]["grace_days"]["type"] == "float"


def test_relation_fields_split_dimensions_and_keep_shared_settings_in_basic():
    schema = json.loads(
        (Path(__file__).parents[1] / "_conf_schema.json").read_text(encoding="utf-8")
    )
    definitions = fields()
    groups = {
        key: [f["path"] for f in definitions if f["group"] == key]
        for key in ("basic", "familiarity", "affinity", "trust")
    }
    assert {"decay.grace_days", "decay.maintenance_strategy", "timezone"} <= set(
        groups["basic"]
    )
    paths = groups["familiarity"] + groups["affinity"] + groups["trust"]
    expected = set()

    def collect(path, node):
        if node["type"] == "object":
            for key, child in node["items"].items():
                collect(f"{path}.{key}", child)
        else:
            expected.add(path)

    for key in (
        "relation_daily_caps",
        "passive_familiarity_rules",
        "familiarity_growth",
        "decay",
        "economy",
    ):
        collect(key, schema[key])
    expected -= {"economy.preset", "decay.grace_days", "decay.maintenance_strategy"}
    assert set(paths) == expected
    assert groups["familiarity"][:7] == [
        "relation_daily_caps.familiarity_up",
        "relation_daily_caps.familiarity_down",
        "passive_familiarity_rules.amount",
        "passive_familiarity_rules.ceiling",
        "passive_familiarity_rules.cooldown_minutes",
        "passive_familiarity_rules.window_minutes",
        "passive_familiarity_rules.member_limit",
    ]
    assert all("trust" in path for path in groups["trust"])
    assert "economy.enabled" in groups["affinity"]
    assert "decay.maintenance_strategy" in groups["basic"]
    assert not any(f["group"] == "rules" for f in definitions)


@pytest.mark.parametrize("days", [0, 0.5, 5.0, 30])
def test_grace_days_and_existing_impression_interval_are_preserved(days):
    config = {"decay": {"grace_days": days}, "impression": {"interval": 13}}
    before = copy.deepcopy(config)
    validate_patch({"decay.grace_days": days})
    values = snapshot(config)["values"]
    assert values["decay.grace_days"] == days
    assert values["impression.interval"] == 13
    assert snapshot({})["values"]["impression.interval"] == 8
    assert config == before


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
        {"decay.grace_days": 2.5},
        {"decay.trust_half_life": 200},
        {"decay.familiarity_enabled": False},
        {"decay.familiarity_half_life": 90},
        {"decay.familiarity_baseline": 20},
        {"decay.affinity_baseline": 15},
        {"decay.trust_baseline": 25},
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
                runtime.save_detailed_settings({"inject.enabled": False}, revision)
            )
            models = asyncio.create_task(
                runtime.save_assessment_settings({"cooldown_sec": 90}, 5, revision)
            )
            await asyncio.sleep(0)
            assert not details.done() and not models.done()
        result = await asyncio.gather(details, models, return_exceptions=True)
        assert isinstance(result[0], dict)
        assert isinstance(result[1], SettingsRevisionError)
        assert config == {"inject": {"enabled": False}}

    asyncio.run(run())


def test_framework_defaults_custom_values_and_single_assessment_entry():
    data = snapshot({})
    values = data["values"]
    expected = {
        "relation_mode": "galgame",
        "decay.grace_days": 5.0,
        "impression.interval": 8,
        "relation_daily_caps.familiarity_up": 5,
        "relation_daily_caps.familiarity_down": 0,
        "relation_daily_caps.affinity_up": 10,
        "relation_daily_caps.affinity_down": 8,
        "relation_daily_caps.trust_up": 2,
        "relation_daily_caps.trust_down": 8,
        "decay.familiarity_enabled": True,
        "decay.familiarity_half_life": 30,
        "decay.enabled": True,
        "decay.half_life_base": 30,
        "decay.trust_enabled": True,
        "decay.trust_half_life": 60,
        "decay.familiarity_baseline": 10,
        "decay.affinity_baseline": 0,
        "decay.trust_baseline": 20,
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
            "decay": {
                "familiarity_enabled": False,
                "familiarity_half_life": 90,
                "trust_enabled": False,
                "half_life_base": 75,
            },
        }
    )["values"]
    assert custom["relation_mode"] == "realistic"
    assert custom["relation_daily_caps.affinity_up"] == 12
    assert custom["familiarity_growth.under10"] == 1.1
    assert custom["decay.half_life_base"] == 75
    assert custom["decay.trust_enabled"] is False
    assert custom["decay.familiarity_enabled"] is False
    assert custom["decay.familiarity_half_life"] == 90


@pytest.mark.parametrize(
    "path,value",
    [
        ("decay.grace_days", -0.1),
        ("decay.grace_days", True),
        ("decay.grace_days", "5"),
        ("decay.grace_days", float("nan")),
        ("decay.grace_days", float("inf")),
        ("relation_daily_caps.affinity_up", -1),
        ("relation_daily_caps.trust_down", True),
        ("familiarity_growth.at95", float("inf")),
        ("decay.trust_half_life", 0),
        ("decay.familiarity_half_life", 0),
        ("decay.familiarity_half_life", 4.9),
        ("decay.familiarity_half_life", True),
        ("decay.familiarity_half_life", float("inf")),
        ("decay.familiarity_enabled", "true"),
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
            decay={"familiarity_enabled": False, "familiarity_half_life": 90},
            impression={"interval": 13},
            master_ids="8001",
            assessment={"small_provider_id": "chosen"},
        )
        runtime = RuntimeService(None, config, None)
        initial = runtime.detailed_settings()
        await runtime.save_detailed_settings(
            {
                "relation_mode": "galgame",
                "relation_daily_caps.affinity_up": 15,
                "decay.grace_days": 2.5,
                "decay.familiarity_enabled": True,
                "decay.familiarity_half_life": 60,
                "decay.familiarity_baseline": 20,
                "decay.affinity_baseline": 15,
                "decay.trust_baseline": 20,
            },
            initial["revision"],
        )
        saved = runtime.detailed_settings()
        assert saved["values"]["relation_mode"] == "galgame"
        assert saved["values"]["relation_daily_caps.affinity_up"] == 15
        assert saved["active_values"]["relation_mode"] == "realistic"
        assert saved["active_values"]["relation_daily_caps.affinity_up"] == 12
        assert saved["values"]["decay.grace_days"] == 2.5
        assert saved["active_values"]["decay.grace_days"] == 5.0
        assert config["impression"]["interval"] == 13
        assert saved["values"]["decay.familiarity_enabled"] is True
        assert saved["values"]["decay.familiarity_half_life"] == 60
        assert saved["active_values"]["decay.familiarity_enabled"] is False
        assert saved["active_values"]["decay.familiarity_half_life"] == 90
        for dimension, baseline in (
            ("familiarity", 20),
            ("affinity", 15),
            ("trust", 20),
        ):
            path = f"decay.{dimension}_baseline"
            assert saved["values"][path] == baseline
            assert saved["active_values"][path] == initial["active_values"][path]
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


@pytest.mark.parametrize("dimension", ["familiarity", "affinity", "trust"])
def test_independent_baseline_bounds_and_legacy_value_ignored(dimension):
    path = f"decay.{dimension}_baseline"
    legacy = {"decay": {"baseline": 77}}
    before = copy.deepcopy(legacy)
    data = snapshot(legacy)
    field = next(field for field in data["fields"] if field["path"] == path)
    expected = {"familiarity": 10, "affinity": 0, "trust": 20}[dimension]
    assert field["group"] == dimension and field["default"] == expected
    assert data["values"][path] == expected
    assert data["values"] == snapshot({})["values"]
    assert "decay.baseline" not in data["values"]
    for value in (0, 20.5, 100):
        validate_patch({path: value})
        assert (
            snapshot({"decay": {"baseline": 77, f"{dimension}_baseline": value}})[
                "values"
            ][path]
            == value
        )
    assert legacy == before


@pytest.mark.parametrize("dimension", ["familiarity", "affinity", "trust"])
@pytest.mark.parametrize("value", [-0.1, 100.1, True, float("nan")])
def test_independent_baseline_invalid_values(dimension, value):
    with pytest.raises(ValueError):
        validate_patch({f"decay.{dimension}_baseline": value})


def test_query_settings_share_host_values_and_reject_unknown_impression_mode():
    config = {"command": {"ranking_access": "master", "impression_mode": "大模型总结"}}
    data = snapshot(config)
    query_fields = [f["path"] for f in data["fields"] if f["group"] == "commands"]
    assert query_fields == [
        "command.ranking_access",
        "command.ranking_image",
        "command.ranking_limit",
        "command.text_wake_enabled",
        "command.text_wake_ranking",
        "command.impression_mode",
        "render.font_path",
    ]
    assert data["values"]["command.ranking_access"] == "master"
    assert data["values"]["command.impression_mode"] == "大模型总结"
    assert snapshot({})["values"]["command.impression_mode"] == "关闭"
    for mode in ("关闭", "输出关系称谓", "大模型总结"):
        validate_patch({"command.impression_mode": mode})
    with pytest.raises(ValueError):
        validate_patch({"command.impression_mode": "unknown"})


def test_diagnostic_levels_capacity_defaults_and_validation():
    data = snapshot({})
    values = data["values"]
    assert all(
        f["title"] == "系统诊断" for f in data["fields"] if f["group"] == "diagnostics"
    )
    assert values["diagnostics.enabled"] is False
    assert values["diagnostics.detail_level"] == "assessment"
    assert values["diagnostics.max_size_mb"] == 100
    for level in ("assessment", "raw", "evaluation", "full"):
        validate_patch({"diagnostics.detail_level": level})
    for size in (1, 100, 10000):
        validate_patch({"diagnostics.max_size_mb": size})
    for invalid in (0, -1, 10001, True, "100", 1.5):
        with pytest.raises(ValueError):
            validate_patch({"diagnostics.max_size_mb": invalid})
    with pytest.raises(ValueError):
        validate_patch({"diagnostics.detail_level": "unknown"})


@pytest.mark.parametrize("enabled", [False, True])
def test_flat_passive_switch_migrates_saved_choice_once(enabled):
    from astrbot_plugin_bond.core.config_migrations import migrate_saved_defaults

    config = {"trust_config_version": 1, "passive_familiarity": {"enabled": enabled}}
    assert migrate_saved_defaults(config) == ["passive_familiarity"]
    assert config["passive_familiarity"] is enabled
    assert migrate_saved_defaults(config) == []
    assert snapshot(config)["values"]["passive_familiarity"] is enabled
    schema = json.loads(
        (Path(__file__).parents[1] / "_conf_schema.json").read_text(encoding="utf-8")
    )
    assert schema["passive_familiarity"]["type"] == "bool"
    assert schema["passive_familiarity"]["default"] is True
    assert (
        list(schema).index("passive_familiarity")
        == list(schema).index("master_ids") + 1
    )
    assert schema["passive_familiarity_rules"]["invisible"] is True


@pytest.mark.parametrize(
    "key,value",
    [
        ("amount", -0.1),
        ("amount", 100.1),
        ("amount", True),
        ("ceiling", 100.1),
        ("ceiling", float("nan")),
        ("cooldown_minutes", 0),
        ("cooldown_minutes", 1.5),
        ("window_minutes", 0),
        ("window_minutes", True),
        ("member_limit", True),
        ("member_limit", False),
        ("member_limit", 3.0),
        ("member_limit", -1),
        ("member_limit", 1000001),
    ],
)
def test_invalid_passive_parameters_rejected_without_saving(key, value):
    with pytest.raises(ValueError):
        validate_patch({"passive_familiarity_rules." + key: value})


@pytest.mark.parametrize("member_limit", [0, 3, 1000000])
def test_passive_parameters_save_reload_and_report_pending_runtime_values(member_limit):
    async def run():
        config = Config(passive_familiarity=False)
        runtime = RuntimeService(None, config, None)
        patch = {
            "passive_familiarity_rules.amount": 0.8,
            "passive_familiarity_rules.ceiling": 45,
            "passive_familiarity_rules.cooldown_minutes": 5,
            "passive_familiarity_rules.window_minutes": 15,
            "passive_familiarity_rules.member_limit": member_limit,
        }
        initial = runtime.detailed_settings()
        assert initial["values"]["passive_familiarity_rules.window_minutes"] == 10
        assert initial["values"]["passive_familiarity_rules.member_limit"] == 3
        saved = await runtime.save_detailed_settings(patch, initial["revision"])
        assert config["passive_familiarity"] is False
        assert saved["active_values"] == initial["values"]
        assert {key: saved["values"][key] for key in patch} == patch
        reloaded = RuntimeService(None, config, None).detailed_settings()
        assert reloaded["active_values"] == saved["values"]
        before = copy.deepcopy(dict(config))
        with pytest.raises(ValueError):
            await runtime.save_detailed_settings(
                {"passive_familiarity_rules.ceiling": 101}, reloaded["revision"]
            )
        assert config == before

    asyncio.run(run())


@pytest.mark.parametrize("mode,enabled", [("fixed", False), ("llm", True)])
@pytest.mark.parametrize("cooldown,limit", [(6, 10), (8, 14), (24, 14), (48, 0)])
def test_legacy_title_mode_migrates_once_and_preserves_custom_values(
    mode, enabled, cooldown, limit
):
    from astrbot_plugin_bond.core.config_migrations import migrate_saved_defaults
    from astrbot_plugin_bond.core.relation_titles import TitleConfig

    config = {
        "trust_config_version": 1,
        "inject": {"enabled": False},
        "relation_title": {
            "mode": mode,
            "enabled": not enabled,
            "provider_id": "chosen-title-model",
            "refresh_cooldown_hours": cooldown,
            "daily_generation_limit": limit,
            "generation_timeout_seconds": 45,
        },
    }
    changed = migrate_saved_defaults(config)
    title = config["relation_title"]
    assert title["enabled"] is enabled
    assert title["mode"] == ""
    assert title["refresh_cooldown_hours"] == max(12, cooldown)
    assert title["daily_generation_limit"] == (30 if limit == 10 else limit)
    assert title["provider_id"] == "chosen-title-model"
    assert title["generation_timeout_seconds"] == 45
    assert config["inject"]["enabled"] is False
    assert changed[:2] == ["relation_title.enabled", "relation_title.mode"]
    assert TitleConfig.from_config(title).enabled is enabled
    assert migrate_saved_defaults(config) == []
    title.update(refresh_cooldown_hours=24, daily_generation_limit=10)
    assert migrate_saved_defaults(config) == []
    assert title["refresh_cooldown_hours"] == 24
    assert title["daily_generation_limit"] == 10


@pytest.mark.parametrize("mode", [None, ""])
@pytest.mark.parametrize(
    "cooldown,limit,expected",
    [
        (1, 100, (12, 30)),
        (6, 10, (12, 10)),
        (721, -1, (12, 30)),
        (12, 30, (12, 30)),
        (24, 14, (24, 14)),
        (720, 0, (720, 0)),
    ],
)
def test_title_boundaries_migrate_without_legacy_mode_and_report_changes(
    mode, cooldown, limit, expected
):
    from astrbot_plugin_bond.core.config_migrations import migrate_saved_defaults

    title = {
        "enabled": False,
        "provider_id": "chosen",
        "refresh_cooldown_hours": cooldown,
        "daily_generation_limit": limit,
    }
    if mode is not None:
        title["mode"] = mode
    config = {"trust_config_version": 1, "relation_title": title}
    changed = migrate_saved_defaults(config)
    assert changed == [
        f"relation_title.{key}"
        for key, before, after in zip(
            ("refresh_cooldown_hours", "daily_generation_limit"),
            (cooldown, limit),
            expected,
        )
        if before != after
    ]
    assert title["refresh_cooldown_hours"] == expected[0]
    assert title["daily_generation_limit"] == expected[1]
    assert title["enabled"] is False
    assert title["provider_id"] == "chosen"
    assert migrate_saved_defaults(config) == []


@pytest.mark.parametrize("legacy", [False, True])
def test_title_migration_failed_save_rolls_back_and_can_retry(tmp_path, legacy):
    from astrbot_plugin_bond.services.config_upgrade import upgrade_config

    config = Config(
        trust_config_version=1,
        inject={"enabled": False},
        relation_title={
            "mode": "llm" if legacy else "",
            "enabled": False,
            "refresh_cooldown_hours": 6 if legacy else 1,
            "daily_generation_limit": 10 if legacy else 100,
        },
    )
    before = copy.deepcopy(dict(config))
    config.fail = True
    with pytest.raises(OSError):
        upgrade_config(config, tmp_path)
    assert config == before
    config.fail = False
    changed = upgrade_config(config, tmp_path)
    assert "relation_title.refresh_cooldown_hours" in changed
    assert "relation_title.daily_generation_limit" in changed
    assert config["relation_title"] == {
        "mode": "",
        "enabled": legacy,
        "refresh_cooldown_hours": 12,
        "daily_generation_limit": 30,
    }
    assert config.saved == config
    assert upgrade_config(config, tmp_path) == []


@pytest.mark.parametrize("cooldown,limit", [(12, 30), (24, 10), (720, 0)])
def test_title_boundary_settings_save_exact_values_and_roll_back(cooldown, limit):
    async def run():
        config = Config(relation_title={"mode": "", "enabled": False})
        runtime = RuntimeService(None, config, None)
        patch = {
            "relation_title.refresh_cooldown_hours": cooldown,
            "relation_title.daily_generation_limit": limit,
        }
        before = copy.deepcopy(dict(config))
        revision = runtime.detailed_settings()["revision"]
        config.fail = True
        with pytest.raises(OSError):
            await runtime.save_detailed_settings(patch, revision)
        assert config == before
        assert runtime.detailed_settings()["revision"] == revision
        config.fail = False
        saved = await runtime.save_detailed_settings(patch, revision)
        assert {key: saved["values"][key] for key in patch} == patch
        assert config["relation_title"]["refresh_cooldown_hours"] == cooldown
        assert config["relation_title"]["daily_generation_limit"] == limit
        assert config.saved == config

    asyncio.run(run())


@pytest.mark.parametrize(
    "patch",
    [
        {"relation_title.refresh_cooldown_hours": 1},
        {"relation_title.refresh_cooldown_hours": 721},
        {"relation_title.daily_generation_limit": 31},
        {"relation_title.daily_generation_limit": -1},
    ],
)
def test_title_out_of_range_save_is_rejected_without_mutation(patch):
    async def run():
        config = Config(relation_title={"mode": "", "enabled": False})
        runtime = RuntimeService(None, config, None)
        before = copy.deepcopy(dict(config))
        revision = runtime.detailed_settings()["revision"]
        with pytest.raises(ValueError, match="范围|上限"):
            await runtime.save_detailed_settings(
                {"inject.enabled": False, **patch}, revision
            )
        assert config == before
        assert runtime.detailed_settings()["revision"] == revision
        assert not hasattr(config, "saved")

    asyncio.run(run())


def test_personalization_defaults_have_one_switch_and_share_host_storage():
    from dataclasses import asdict
    from astrbot_plugin_bond.core.relation_titles import TitleConfig

    schema = json.loads(
        (Path(__file__).parents[1] / "_conf_schema.json").read_text(encoding="utf-8")
    )
    assert schema["inject"]["description"] == "个性化功能"
    assert list(schema).index("relation_title") == list(schema).index("inject") + 1
    title = schema["relation_title"]["items"]
    assert title["mode"]["invisible"] is True
    assert title["mode"]["default"] == ""
    assert "options" not in title["mode"]
    for key, value in asdict(TitleConfig()).items():
        assert title[key]["default"] == value
    assert snapshot({"inject": {"enabled": False}})["values"]["inject.enabled"] is False

    async def run():
        config = Config(
            relation_title={"enabled": False, "mode": "", "provider_id": "chosen"}
        )
        runtime = RuntimeService(None, config, None)
        patch = {"inject.enabled": False, "relation_title.enabled": True}
        revision = runtime.detailed_settings()["revision"]
        before = copy.deepcopy(dict(config))
        config.fail = True
        with pytest.raises(OSError):
            await runtime.save_detailed_settings(patch, revision)
        assert config == before
        assert runtime.detailed_settings()["revision"] == revision
        config.fail = False
        saved = await runtime.save_detailed_settings(patch, revision)
        assert config["inject"]["enabled"] is False
        assert config["relation_title"] == {
            "enabled": True,
            "mode": "",
            "provider_id": "chosen",
        }
        assert saved["active_values"]["relation_title.enabled"] is False
        assert saved["values"]["relation_title.enabled"] is True
        assert (
            RuntimeService(None, config, None).detailed_settings()["active_values"][
                "relation_title.enabled"
            ]
            is True
        )
        assert config.saved == config

    asyncio.run(run())


@pytest.mark.parametrize(
    "patch",
    [
        {"relation_title.mode": "llm"},
        {"relation_title.mode": ""},
        {"relation_title.enabled": "false"},
        {"relation_title.enabled": 0},
        {"relation_title.refresh_cooldown_hours": 0},
        {"relation_title.refresh_cooldown_hours": 11.9},
        {"relation_title.refresh_cooldown_hours": 721},
        {"relation_title.daily_generation_limit": True},
        {"relation_title.daily_generation_limit": -1},
        {"relation_title.daily_generation_limit": 1.5},
        {"relation_title.daily_generation_limit": 31},
        {"relation_title.daily_generation_limit": 1001},
        {"relation_title.generation_timeout_seconds": 0},
    ],
)
def test_invalid_title_settings_are_rejected(patch):
    with pytest.raises(ValueError):
        validate_patch(patch)


@pytest.mark.parametrize(
    "inject,enabled",
    [(None, True), ({}, True), ({"enabled": False}, False), ({"enabled": True}, True)],
)
def test_production_assembly_preserves_injection_choice(inject, enabled):
    import ast

    tree = ast.parse(
        (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    )
    factory = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_build_deps"
    )
    deps = next(
        node
        for node in ast.walk(factory)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Deps"
    )
    expression = next(
        item.value for item in deps.keywords if item.arg == "inject_enabled"
    )
    actual = eval(
        compile(ast.Expression(expression), "<production injection config>", "eval"),
        {"inject_cfg": inject or {}},
    )
    assert actual is enabled
    assert snapshot({"inject": inject})["values"]["inject.enabled"] is enabled
