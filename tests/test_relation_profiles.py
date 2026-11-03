"""增长模板的纯规则合同及实际 FavorService/SQLite 回归。"""

import asyncio
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.core.decimal import round1
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_policy import DEFAULT_RULES, RelationPolicy
from astrbot_plugin_bond.core.relation_profiles import build_relation_profile
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

START = 1793664000  # 固定合成时钟，每次推进整天，避免时区边界不确定。


def service(store, mode):
    policy, caps = build_relation_profile(mode)
    return FavorService(
        store,
        LevelTable.from_config(None),
        relation_policy=policy,
        relation_caps=caps,
        decay_enabled=False,
        tz_name="Asia/Shanghai",
    )


async def burst(favor, prefix, event="friendly", count=30):
    for index in range(count):
        await favor.apply_relation_event(
            "g", "u", event, event_id=f"{prefix}:{index}", cooldown_sec=0
        )


@pytest.mark.parametrize("mode", ["realistic", "galgame"])
def test_profile_mapping_preserves_caps_trust_and_nonpositive_requests(mode):
    baseline = RelationPolicy()
    policy, caps = build_relation_profile(mode)
    snapshot = policy.snapshot()
    assert snapshot["event_caps"] == baseline.snapshot()["event_caps"]
    assert caps == dict(
        familiarity=(3, 0) if mode == "galgame" else (2, 0),
        affinity=(4.5, 6) if mode == "galgame" else (3, 6),
        trust=(1, 4),
    )
    for event, rule in DEFAULT_RULES.items():
        for dimension, value in rule.items():
            boosted = mode == "galgame" and dimension != "trust" and value > 0
            expected = value * 1.25 if boosted else value
            assert snapshot["rules"][event][dimension] == expected
        for intensity in (0, 1, 2, 100):
            actual = policy.deltas(event, intensity)
            for dimension, value in actual.items():
                request = snapshot["rules"][event].get(dimension, 0) * intensity
                cap = snapshot["event_caps"][event][dimension]
                assert value == round1(max(-cap, min(cap, request)))
            assert actual["trust"] == baseline.deltas(event, intensity)["trust"]
        if event in ("uncertain", "criticism"):
            assert policy.deltas(event)["affinity"] == 0
            assert policy.deltas(event)["trust"] == 0


@pytest.mark.parametrize("mode", ["", "Galgame", "unknown", None, [], {}])
def test_unknown_mode_is_rejected(mode):
    with pytest.raises(ValueError, match="未知关系增长模式"):
        build_relation_profile(mode)


def test_profile_instances_do_not_share_mutable_configuration():
    original = RelationPolicy().snapshot()
    first, caps = build_relation_profile("galgame")
    caps["trust"] = (100, 100)
    first.snapshot()["rules"]["friendly"]["trust"] = 100
    second, fresh_caps = build_relation_profile("galgame")
    assert fresh_caps["trust"] == (1, 4)
    assert second.snapshot() == first.snapshot()
    assert RelationPolicy().snapshot() == original


@pytest.mark.parametrize("mode", ["realistic", "galgame"])
def test_service_burst_cross_day_and_duplicate_persistence(tmp_path, mode):
    async def run():
        store = SQLiteBackend(tmp_path / "group.db")
        await store.init()
        try:
            favor = service(store, mode)
            _, caps = build_relation_profile(mode)
            daily = dict(
                familiarity=caps["familiarity"][0],
                affinity=caps["affinity"][0],
                trust=0,
            )
            with patch("time.time", return_value=START):
                first = await favor.apply_relation_event(
                    "g", "u", "friendly", event_id="first", cooldown_sec=0
                )
                assert first["delta"] == favor._relation_policy.deltas("friendly")
                duplicate = await favor.apply_relation_event(
                    "g", "u", "friendly", event_id="first", cooldown_sec=0
                )
                assert duplicate["skipped"] == "duplicate"
                await burst(favor, "day1")
                assert (await favor.get_relation("g", "u")).as_dict() == daily
            await store.close()
            await store.init()
            favor = service(store, mode)
            with patch("time.time", return_value=START + 86400):
                before = (await favor.get_relation("g", "u")).as_dict()
                assert before == daily
                assert (
                    await favor.apply_relation_event(
                        "g", "u", "friendly", event_id="first", cooldown_sec=0
                    )
                )["skipped"] == "duplicate"
                assert (await favor.get_relation("g", "u")).as_dict() == before
                await burst(favor, "day2")
                assert (await favor.get_relation("g", "u")).as_dict() == {
                    dimension: value * 2 for dimension, value in daily.items()
                }
        finally:
            await store.close()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["realistic", "galgame"])
def test_negative_and_trust_limits_are_identical_in_real_service(tmp_path, mode):
    async def run():
        store = SQLiteBackend(tmp_path / "group.db")
        await store.init()
        try:
            with patch("time.time", return_value=START):
                favor = service(store, mode)
                first = await favor.apply_relation_event(
                    "g", "u", "insult", event_id="insult", cooldown_sec=0
                )
                assert first["delta"] == dict(familiarity=0, affinity=-1, trust=-1.5)
                await burst(favor, "negative", event="insult")
                assert (await favor.get_relation("g", "u")).as_dict() == dict(
                    familiarity=0, affinity=-6, trust=-4
                )
                for event in (
                    "thanks",
                    "friendly",
                    "apology",
                    "criticism",
                    "uncertain",
                ):
                    result = await favor.apply_relation_event(
                        "g", "u", event, event_id=event, cooldown_sec=0
                    )
                    assert result["delta"]["trust"] == 0
                assert (await favor.get_relation("g", "u")).trust == -4
        finally:
            await store.close()

    asyncio.run(run())


def test_switch_profiles_keeps_state_idempotency_and_used_daily_quota(tmp_path):
    async def run():
        store = SQLiteBackend(tmp_path / "group.db")
        await store.init()
        try:
            with patch("time.time", return_value=START):
                realistic = service(store, "realistic")
                await burst(realistic, "realistic")
                before = (await realistic.get_relation("g", "u")).as_dict()
                logs = await store.query_logs("g", "u")
                galgame = service(store, "galgame")
                assert (await galgame.get_relation("g", "u")).as_dict() == before
                assert await store.query_logs("g", "u") == logs
                assert (
                    await galgame.apply_relation_event(
                        "g", "u", "friendly", event_id="realistic:0", cooldown_sec=0
                    )
                )["skipped"] == "duplicate"
                await burst(galgame, "galgame")
                # 只补齐新模板与旧模板的额度差，不能重新获得一整天额度。
                assert (await galgame.get_relation("g", "u")).as_dict() == dict(
                    familiarity=3, affinity=4.5, trust=0
                )
                await burst(service(store, "realistic"), "back")
                await burst(service(store, "galgame"), "again")
                assert (await galgame.get_relation("g", "u")).as_dict() == dict(
                    familiarity=3, affinity=4.5, trust=0
                )
            with patch("time.time", return_value=START + 86400):
                await burst(service(store, "realistic"), "tomorrow")
                assert (await realistic.get_relation("g", "u")).as_dict() == dict(
                    familiarity=5, affinity=7.5, trust=0
                )
        finally:
            await store.close()

    asyncio.run(run())


def test_explicit_custom_policy_still_controls_service(tmp_path):
    async def run():
        store = SQLiteBackend(tmp_path / "group.db")
        await store.init()
        try:
            custom = RelationPolicy(rules={"friendly": {"affinity": 1.7, "trust": 0.4}})
            favor = FavorService(
                store, LevelTable.from_config(None), relation_policy=custom
            )
            with patch("time.time", return_value=START):
                result = await favor.apply_relation_event(
                    "g", "u", "friendly", event_id="custom", cooldown_sec=0
                )
            assert result["delta"] == dict(familiarity=0.3, affinity=1.7, trust=0.4)
        finally:
            await store.close()

    asyncio.run(run())


def test_ten_friendly_events_with_recommended_economy(tmp_path):
    from astrbot_plugin_bond.core.level_economy import EconomyConfig

    async def run():
        results = {}
        for mode in ("realistic", "galgame"):
            store = SQLiteBackend(tmp_path / f"{mode}.db")
            await store.init()
            try:
                policy, caps = build_relation_profile(mode)
                favor = FavorService(
                    store,
                    LevelTable.from_config(None),
                    relation_policy=policy,
                    relation_caps=caps,
                    economy=EconomyConfig(),
                )
                with patch("time.time", return_value=START):
                    await burst(favor, mode, count=10)
                    results[mode] = (await favor.get_relation("g", "u")).as_dict()
            finally:
                await store.close()
        assert results["realistic"] == dict(familiarity=2, affinity=3, trust=0)
        # 后七次1×0.25按既有半数偶舍入为0.2，累计3.7而不是原始请求总和。
        assert results["galgame"] == dict(familiarity=3, affinity=3.7, trust=0)

    asyncio.run(run())
