"""模式仅统一缩放正向规则和正向额度，不改基础参数。"""

import pytest

from astrbot_plugin_bond.core.level_economy import (
    EconomyConfig,
    FamiliarityConfig,
    apply,
    familiarity_multiplier,
)
from astrbot_plugin_bond.core.relation_policy import RelationPolicy
from astrbot_plugin_bond.core.relation_profiles import (
    DEFAULT_DAILY_CAPS,
    build_relation_profile,
    positive_mode_multiplier,
)


@pytest.mark.parametrize("mode,factor", [("realistic", 1), ("galgame", 2)])
def test_profile_keeps_base_rules_and_only_scales_positive_caps(mode, factor):
    baseline = RelationPolicy()
    policy, caps = build_relation_profile(mode)
    assert policy.snapshot()["rules"] == baseline.snapshot()["rules"]
    assert policy.snapshot()["event_caps"] == baseline.snapshot()["event_caps"]
    assert policy.positive_multiplier == factor
    assert caps == {
        key: (positive * factor, negative)
        for key, (positive, negative) in DEFAULT_DAILY_CAPS.items()
    }
    assert caps["familiarity"] == (5 * factor, 0)
    assert caps["affinity"] == (10 * factor, 8)
    assert caps["trust"] == (2 * factor, 8)
    for event in ("thanks", "friendly", "apology", "insult", "manipulation"):
        assert policy.deltas(event)["trust"] == 0
    for event in ("insult", "manipulation"):
        assert policy.deltas(event) == baseline.deltas(event)


def test_omitted_mode_defaults_to_galgame():
    policy, caps = build_relation_profile()
    assert policy.positive_multiplier == 2
    assert caps == dict(familiarity=(10, 0), affinity=(20, 8), trust=(4, 8))


@pytest.mark.parametrize("mode", ["", "Galgame", "unknown", None, [], {}])
def test_unknown_mode_is_rejected(mode):
    with pytest.raises(ValueError, match="未知关系增长模式"):
        build_relation_profile(mode)


def test_synthetic_base_rules_caps_and_daily_limits_remain_authoritative():
    # 显式合成 T 规则仅验证底层定制数学，不代表自动评分证据或用户设置。
    rules = {"friendly": dict(familiarity=5, affinity=3, trust=0.2)}
    event_caps = {"friendly": dict(familiarity=0.4, affinity=0.6, trust=0.3)}
    daily = dict(familiarity=(0.7, 0), affinity=(3, 4), trust=(0.4, 0.8))
    policy, caps = build_relation_profile(
        "galgame", rules=rules, event_caps=event_caps, caps=daily
    )
    assert policy.deltas("friendly") == dict(familiarity=0.8, affinity=1.2, trust=0.4)
    assert caps == dict(familiarity=(1.4, 0), affinity=(6, 4), trust=(0.8, 0.8))
    assert rules["friendly"]["affinity"] == 3
    assert daily["affinity"] == (3, 4)
    assert event_caps["friendly"]["affinity"] == 0.6


def test_profile_instances_and_snapshots_do_not_share_mutable_state():
    policy, caps = build_relation_profile()
    caps["trust"] = (100, 100)
    policy.snapshot()["rules"]["friendly"]["trust"] = 100
    second, fresh = build_relation_profile()
    assert fresh["trust"] == (4, 8)
    assert policy.snapshot() == second.snapshot()
    assert DEFAULT_DAILY_CAPS["trust"] == (2, 8)


@pytest.mark.parametrize(
    "daily",
    [
        {"wrong": (1, 1)},
        {"trust": (1,)},
        {"trust": (-1, 2)},
        {"trust": (True, 0)},
        {"trust": (float("inf"), 0)},
    ],
)
def test_invalid_custom_daily_caps_are_rejected(daily):
    with pytest.raises(ValueError):
        build_relation_profile(caps=daily)


@pytest.mark.parametrize(
    "current_f,expected",
    [(0, 0.3), (10, 0.24), (30, 0.16), (55, 0.1), (80, 0.06), (95, 0.03)],
)
def test_transaction_pipeline_preserves_raw_f_and_applies_mode_once(
    current_f, expected
):
    policy, _ = build_relation_profile()
    deltas = policy.base_deltas("normal_question")
    mult = familiarity_multiplier(current_f, FamiliarityConfig())
    deltas["familiarity"] *= mult
    deltas["affinity"] = apply(
        deltas["affinity"], "陌生", cfg=EconomyConfig(), rounded=False
    ).delta
    deltas = policy.scale_positive(deltas, rounded=False)
    result = policy.limit_deltas(
        "normal_question", deltas, familiarity_multiplier=mult, rounded=False
    )
    assert result["familiarity"] == pytest.approx(expected)
    assert result["affinity"] == result["trust"] == 0


def test_synthetic_positive_trust_gets_mode_once_without_a_curve():
    # 显式合成 T 请求只验证倍率，不代表 friendly 自动增信。
    policy, _ = build_relation_profile(rules={"friendly": {"trust": 0.13}})
    assert policy.deltas("friendly", rounded=False)["trust"] == pytest.approx(0.26)


def test_intensity_cannot_bypass_curve_scaled_final_caps():
    policy, _ = build_relation_profile(rules={"friendly": {"familiarity": 100}})
    deltas = policy.base_deltas("friendly", 100)
    assert deltas["familiarity"] == 1
    deltas["familiarity"] *= 1.5
    result = policy.limit_deltas(
        "friendly",
        policy.scale_positive(deltas, rounded=False),
        familiarity_multiplier=1.5,
        rounded=False,
    )
    assert result["familiarity"] == 3


def test_uncertain_and_zero_intensity_never_generate_fractional_rewards():
    policy, _ = build_relation_profile()
    assert policy.deltas("uncertain", rounded=False) == dict(
        familiarity=0, affinity=0, trust=0
    )
    assert policy.deltas("friendly", 0, rounded=False) == dict(
        familiarity=0, affinity=0, trust=0
    )


@pytest.mark.parametrize("mode,expected", [("realistic", 1.0), ("galgame", 2.0)])
def test_public_mode_helper_matches_policy(mode, expected):
    assert positive_mode_multiplier(mode) == expected
    assert build_relation_profile(mode)[0].positive_multiplier == expected


@pytest.mark.parametrize(
    "current,level_mult",
    [(-20, 1), (29.9, 1), (30, 0.75), (55, 0.55), (80, 0.35), (95, 0.2)],
)
@pytest.mark.parametrize(
    "positive_today,day_mult", [(0, 1), (1, 0.75), (2, 0.5), (3, 0.25)]
)
def test_affinity_curve_then_mode_keeps_full_double_precision(
    current, level_mult, positive_today, day_mult
):
    from astrbot_plugin_bond.core.levels import LevelTable

    cfg = EconomyConfig()
    level = LevelTable.from_config(None).level_of(current).name
    outcomes = []
    for mode in ("realistic", "galgame"):
        policy, _ = build_relation_profile(mode)
        raw = policy.base_deltas("friendly")
        raw["affinity"] = apply(
            raw["affinity"], level, positive_today, cfg, rounded=False
        ).delta
        final = policy.limit_deltas(
            "friendly", policy.scale_positive(raw, rounded=False), rounded=False
        )
        outcomes.append(final["affinity"])
    assert outcomes[0] == pytest.approx(0.8 * level_mult * day_mult)
    assert outcomes[1] == pytest.approx(outcomes[0] * 2)


def test_custom_negative_affinity_weight_caps_without_changing_trust():
    policy, _ = build_relation_profile()
    raw = policy.base_deltas("insult")
    raw["affinity"] = apply(
        raw["affinity"], "陌生", cfg=EconomyConfig(negative_weight=10), rounded=False
    ).delta
    result = policy.limit_deltas(
        "insult", policy.scale_positive(raw, rounded=False), rounded=False
    )
    assert result["affinity"] == -2
    assert result["trust"] == 0
