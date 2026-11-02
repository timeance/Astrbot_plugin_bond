"""PR-2 三维关系纯规则层测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_bond.core.relation_policy import (  # noqa: E402
    EventType,
    RelationPolicy,
    RelationResolver,
    RelationState,
)


def test_state_is_frozen_rounded_and_bounded() -> None:
    state = RelationState(10.04, 20.06, -30.05)

    assert state == RelationState(10.0, 20.1, -30.1)
    with pytest.raises((AttributeError, TypeError)):
        state.affinity = 30.0  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"familiarity": -0.1},
        {"familiarity": 100.1},
        {"affinity": -100.1},
        {"trust": 100.1},
        {"trust": True},
        {"affinity": "1"},
        {"trust": float("nan")},
        {"trust": float("inf")},
    ],
)
def test_state_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RelationState(**kwargs)


def test_default_policy_keeps_neutral_events_out_of_affinity_and_trust() -> None:
    policy = RelationPolicy()

    for event_type in (
        EventType.NORMAL_QUESTION,
        EventType.CRITICISM,
        EventType.UNCERTAIN,
    ):
        delta = policy.deltas(event_type.value)
        assert delta["affinity"] == 0.0
        assert delta["trust"] == 0.0

    assert policy.deltas("uncertain") == {
        "familiarity": 0.0,
        "affinity": 0.0,
        "trust": 0.0,
    }


def test_policy_scales_intensity_and_applies_per_dimension_cap() -> None:
    policy = RelationPolicy(
        rules={"friendly": {"familiarity": 4.0, "affinity": 3.0, "trust": -5.0}},
        event_caps={"friendly": {"familiarity": 0.5, "affinity": 1.0, "trust": 2.0}},
    )

    assert policy.deltas("friendly", 0.5) == {
        "familiarity": 0.5,
        "affinity": 1.0,
        "trust": -2.0,
    }


def test_policy_rejects_invalid_event_and_intensity() -> None:
    policy = RelationPolicy()

    with pytest.raises(ValueError):
        policy.deltas("unknown")
    with pytest.raises(ValueError):
        policy.deltas("friendly", -1)
    with pytest.raises(ValueError):
        policy.deltas("friendly", True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        policy.deltas("friendly", float("inf"))


def test_policy_handles_negative_events_without_total_score() -> None:
    policy = RelationPolicy()

    insult = policy.deltas("insult")
    manipulation = policy.deltas("manipulation")
    apology = policy.deltas("apology")

    assert insult["affinity"] < 0 and insult["trust"] < 0
    assert manipulation["trust"] < insult["trust"]
    assert apology["affinity"] > 0 and apology["trust"] == 0
    assert set(insult) == {"familiarity", "affinity", "trust"}


def test_resolver_keeps_relationship_dimensions_composable() -> None:
    resolver = RelationResolver()

    assert resolver.resolve(RelationState()) == "陌生"
    assert resolver.resolve(RelationState(90, 65, -60)) == "亲近却戒备"
    assert resolver.resolve(RelationState(90, 65, 60)) == "亲密挚友"


def test_resolver_accepts_configurable_thresholds_and_labels() -> None:
    resolver = RelationResolver(
        thresholds={
            "familiarity": {"eyeball": 5, "familiar": 20},
            "affinity": {"close": 30},
            "trust": {"trusted": 30},
        },
        labels={"familiar": "熟人", "close": "亲近感", "trusted": "可信"},
    )

    assert resolver.resolve(RelationState(20, 30, 30)) == "熟人、亲近感且可信"
    assert resolver.resolve_labels(RelationState(20, 30, 30)) == (
        "熟人",
        "亲近感",
        "可信",
    )


@pytest.mark.parametrize("value", [-0.01, 100.01])
def test_state_validates_bounds_before_rounding(value):
    with pytest.raises(ValueError):
        RelationState(familiarity=value)


def test_large_finite_intensity_still_obeys_cap():
    result = RelationPolicy(rules={"friendly": {"affinity": 10}}).deltas(
        "friendly", 1e308
    )
    assert result["affinity"] == 2


@pytest.mark.parametrize("event", ["thanks", "friendly", "apology"])
def test_politeness_has_no_default_trust_evidence_and_custom_rule_wins(event):
    assert RelationPolicy().deltas(event)["trust"] == 0
    assert RelationPolicy(rules={event: {"affinity": 0.4}}).deltas(event)["trust"] == 0
    assert RelationPolicy(rules={event: {"trust": 0.7}}).deltas(event)["trust"] == 0.7
    assert RelationPolicy(rules={event: {"trust": 9}}).deltas(event)["trust"] == 2
