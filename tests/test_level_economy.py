"""熟悉度曲线与亲近度数值管线；不测试模型语义能力。"""

import pytest

from astrbot_plugin_bond.core.level_economy import (
    EconomyConfig,
    FamiliarityConfig,
    apply,
    familiarity_multiplier,
)
from astrbot_plugin_bond.core.levels import LevelTable


@pytest.mark.parametrize(
    "current,expected",
    [
        (0, 1.5),
        (9.9, 1.5),
        (9.999, 1.5),
        (10, 1.2),
        (29.9, 1.2),
        (30, 0.8),
        (54.9, 0.8),
        (55, 0.5),
        (79.9, 0.5),
        (80, 0.3),
        (94.9, 0.3),
        (95, 0.15),
        (100, 0.15),
    ],
)
def test_familiarity_curve_boundaries_use_own_raw_value(current, expected):
    assert familiarity_multiplier(current) == expected


def test_custom_familiarity_multipliers_preserve_omitted_defaults():
    cfg = FamiliarityConfig.from_config(dict(under10=2.1, at95=0.025))
    assert familiarity_multiplier(1, cfg) == 2.1
    assert familiarity_multiplier(30, cfg) == 0.8
    assert familiarity_multiplier(100, cfg) == 0.025


@pytest.mark.parametrize("value", [-1, True, float("inf"), float("nan"), "1"])
def test_invalid_familiarity_multiplier_is_rejected(value):
    with pytest.raises(ValueError):
        FamiliarityConfig.from_config(dict(under10=value))


@pytest.mark.parametrize("value", [-0.01, 100.01, True, float("nan"), float("inf")])
def test_invalid_familiarity_state_is_rejected(value):
    with pytest.raises(ValueError):
        familiarity_multiplier(value)


@pytest.mark.parametrize(
    "current,mult",
    [
        (-100, 1),
        (-0.1, 1),
        (0, 1),
        (29.9, 1),
        (30, 0.75),
        (54.9, 0.75),
        (55, 0.55),
        (79.9, 0.55),
        (80, 0.35),
        (94.9, 0.35),
        (95, 0.2),
        (100, 0.2),
    ],
)
def test_existing_affinity_curve_boundaries(current, mult):
    level = LevelTable.from_config(None).level_of(current).name
    assert apply(1, level, cfg=EconomyConfig(), rounded=False).delta == mult


@pytest.mark.parametrize(
    "count,mult", [(0, 1), (1, 0.75), (2, 0.5), (3, 0.25), (4, 0.25), (100, 0.25)]
)
def test_affinity_same_day_decay_floor(count, mult):
    assert (
        apply(1, "陌生", positive_today=count, cfg=EconomyConfig(), rounded=False).delta
        == mult
    )
    assert (
        apply(
            -1, "挚爱", positive_today=count, cfg=EconomyConfig(), rounded=False
        ).delta
        == -1
    )


def test_unrounded_apply_keeps_precision_through_all_stages():
    cfg = EconomyConfig(noise_floor=0, repair_factor=0.5)
    raw = apply(0.5, "友好", positive_today=1, cfg=cfg, repair=True, rounded=False)
    assert raw.delta == pytest.approx(0.5 * 0.75 * 0.75 * 0.5)
    assert raw.multiplied and raw.decayed and raw.repairing
    # 旧调用保持逐环节一位小数，不影响尚未迁移的标量 API。
    assert apply(0.5, "友好", positive_today=1, cfg=cfg, repair=True).delta == 0.1
    assert apply(0.025, "陌生", cfg=None, rounded=False).delta == 0.025
    assert apply(0.025, "陌生", cfg=None).delta == 0


@pytest.mark.parametrize(
    "preset,weight,decay",
    [
        ("default", 1, 0.25),
        ("realistic", 1.8, 0.35),
        ("galgame", 1.2, 0.15),
        ("unknown", 1, 0.25),
    ],
)
def test_legacy_preset_supplies_saved_base_defaults(preset, weight, decay):
    cfg = EconomyConfig.from_config(dict(preset=preset))
    assert cfg.negative_weight == weight
    assert cfg.same_day_decay == decay


def test_explicit_economy_settings_override_legacy_preset_names():
    cfg = EconomyConfig.from_config(
        dict(
            preset="galgame",
            noise_floor=0,
            negative_weight=1.7,
            same_day_decay=0.1,
            level_mult={"youhao": 0.6},
            repair_threshold=1,
            repair_factor=0.4,
        )
    )
    assert cfg.noise_floor == 0
    assert cfg.negative_weight == 1.7
    assert cfg.same_day_decay == 0.1
    assert cfg.repair_threshold == 1
    assert cfg.repair_factor == 0.4
    assert apply(-1, "陌生", cfg=cfg, rounded=False).delta == -1.7
    assert apply(
        1, "友好", cfg=cfg, positive_today=1, repair=True, rounded=False
    ).delta == pytest.approx(0.6 * 0.9 * 0.4)


def test_noise_floor_and_disabled_economy_keep_contract():
    assert EconomyConfig.from_config({"enabled": False}) is None
    assert apply(0.49, "陌生", cfg=EconomyConfig(), rounded=False).floored
    assert apply(0, "陌生", cfg=EconomyConfig(), rounded=False).delta == 0
