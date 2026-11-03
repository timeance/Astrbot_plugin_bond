"""新安装推荐设置、旧数据读取、重载与回滚的行为验证。"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from astrbot_plugin_bond.core.level_economy import EconomyConfig
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.settings import snapshot, validate_patch
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend
from astrbot_plugin_bond.scripts.replay_recommended_defaults import replay, START


def favor(store, **options):
    config = dict(
        decay_enabled=True,
        half_life_base=30,
        half_life_growth=1,
        half_life_max=120,
        economy=EconomyConfig.from_config(None),
    )
    return FavorService(store, LevelTable.from_config(None), **dict(config, **options))


def test_new_defaults_and_explicit_custom_settings():
    values = snapshot({})["values"]
    eco = EconomyConfig.from_config(None)
    assert values["economy.negative_weight"] == eco.negative_weight == 1
    assert values["economy.repair_threshold"] == eco.repair_threshold == 0
    assert values["decay.half_life_growth"] == 1
    old = {"negative_weight": 1.5, "repair_threshold": 2}
    assert EconomyConfig.from_config(old).negative_weight == 1.5
    assert EconomyConfig.from_config(old).repair_threshold == 2
    assert snapshot({"economy": old})["values"]["economy.repair_threshold"] == 2
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(
            encoding="utf-8"
        )
    )
    for name in ("assessment", "inject", "diagnostics", "coupling"):
        assert schema[name]["items"]["enabled"]["default"] is False
    for name in ("decay.half_life_base", "decay.half_life_max"):
        with pytest.raises(ValueError):
            validate_patch({name: 4})
        validate_patch({name: 5})


@pytest.mark.parametrize("stored_half_life", [10, 120])
def test_fixed_half_life_changes_existing_reads_and_writes_without_migration(
    tmp_path, stored_half_life
):
    async def run():
        path = tmp_path / "group.db"
        store = SQLiteBackend(path)
        await store.init()
        with patch("time.time", return_value=START):
            old = favor(store, half_life_base=stored_half_life, half_life_growth=1.3)
            await old.set_relation(
                "g", "u", familiarity=40, affinity=20, trust=-20, event_id="seed"
            )
        original = await store.get("g", "u")
        original_logs = await store.query_logs("g", "u")
        await store.close()
        await store.init()
        try:
            with patch("time.time", return_value=START + 30 * 86400):
                # 同一旧记录上调、下调、回滚配置；纯读取不重写原分数与流水。
                for base, expected in ((30, 10), (60, 14.1), (10, 2.5), (30, 10)):
                    service = favor(store, half_life_base=base)
                    state = await service.get_relation("g", "u")
                    assert state.as_dict() == dict(
                        familiarity=40, affinity=expected, trust=-20
                    )
                assert await store.get("g", "u") == original
                assert await store.query_logs("g", "u") == original_logs
                service = favor(store, half_life_base=60)
                result = await service.apply_relation_event(
                    "g", "u", "normal_question", event_id="question"
                )
                assert result["before"]["affinity"] == result["after"]["affinity"]
                assert round(result["after"]["affinity"], 1) == 14.1
                assert (await service.get_relation("g", "u")).affinity == 14.1
                assert (await store.get("g", "u")).half_life == 60
                logs = await store.query_logs("g", "u")
                await service.undo_log(logs[0]["id"])
                assert (await service.get_relation("g", "u")).as_dict() == dict(
                    familiarity=40, affinity=14.1, trust=-20
                )
                assert store._c().execute("PRAGMA user_version").fetchone()[0] == 16
        finally:
            await store.close()

    asyncio.run(run())


def test_custom_consolidation_uses_current_bounds_and_fixed_mode_can_be_disabled(
    tmp_path,
):
    async def run():
        store = SQLiteBackend(tmp_path / "group.db")
        await store.init()
        with patch("time.time", return_value=START):
            await favor(store, half_life_base=120).set_relation(
                "g", "u", familiarity=10, affinity=20, trust=10, event_id="seed"
            )
        try:
            with patch("time.time", return_value=START + 30 * 86400):
                bounded = favor(store, half_life_growth=1.3, half_life_max=30)
                assert (await bounded.get_relation("g", "u")).affinity == 10
                fixed = favor(store, half_life_base=10)
                assert (await fixed.get_relation("g", "u")).affinity == 2.5
                disabled = favor(store, decay_enabled=False)
                assert (await disabled.get_relation("g", "u")).affinity == 20
                await bounded.apply_relation_event(
                    "g", "u", "friendly", event_id="friendly"
                )
                assert (await store.get("g", "u")).half_life == 30
        finally:
            await store.close()

    asyncio.run(run())


def test_config_reload_during_temporary_relation_keeps_pause_and_formal_data(tmp_path):
    async def run():
        store = SQLiteBackend(tmp_path / "group.db")
        await store.init()
        with patch("time.time", return_value=START):
            service = favor(store)
            await service.set_relation(
                "g", "u", familiarity=40, affinity=20, trust=-20, event_id="seed"
            )
        with patch("time.time", return_value=START + 10 * 86400):
            await service.set_temporary(
                "g", "u", dict(familiarity=90, affinity=70, trust=60), 86400, "admin"
            )
        await store.close()
        await store.init()
        reloaded = favor(store, half_life_base=10)
        try:
            with patch("time.time", return_value=START + 10.5 * 86400):
                assert (await reloaded.get_relation("g", "u")).affinity == 70
                assert (await reloaded.formal_relation("g", "u"))["affinity"] == 10
                assert (await store.get("g", "u")).favor == 20
            with patch("time.time", return_value=START + 11 * 86400):
                assert (await reloaded.get_relation("g", "u")).as_dict() == dict(
                    familiarity=40, affinity=10, trust=-20
                )
                assert len(await store.query_logs("g", "u")) == 1
            with patch("time.time", return_value=START + 12 * 86400):
                assert (await reloaded.get_relation("g", "u")).affinity == 9.3
        finally:
            await store.close()

    asyncio.run(run())


def test_disabled_recovery_suppression_does_not_scan_logs(tmp_path):
    async def run():
        store = SQLiteBackend(tmp_path / "group.db")
        await store.init()
        try:
            with patch.object(
                store,
                "query_logs",
                side_effect=AssertionError("unexpected history scan"),
            ):
                assert await favor(store)._repair_factor("g", "u") is None
        finally:
            await store.close()

    asyncio.run(run())


def test_replay_is_deterministic_and_does_not_inflate_trust(tmp_path):
    first = asyncio.run(replay(tmp_path / "first"))
    assert first == asyncio.run(replay(tmp_path / "second"))
    old = first["arms"]["previous_0_3_3"]["scenarios"]
    new = first["arms"]["recommended"]["scenarios"]
    assert old["positive_burst"]["rows"][5]["half_life"] == 120
    assert {row["half_life"] for row in new["positive_burst"]["rows"]} == {60}
    assert new["negative_mistake"]["rows"][0]["delta"]["affinity"] == -1
    assert old["negative_mistake"]["rows"][0]["delta"]["affinity"] == -1.5
    for name in new:
        assert [r["delta"]["trust"] for r in new[name]["rows"]] == [
            r["delta"]["trust"] for r in old[name]["rows"]
        ]
    assert new["neutral_3_days"]["rows"][-1]["state"] == dict(
        familiarity=3.15, affinity=0, trust=0
    )
    assert new["violation_apology_repeat"]["rows"][1]["delta"]["trust"] == 0
    assert new["violation_apology_repeat"]["rows"][-1]["state"]["trust"] == -4


def test_scalar_zero_gain_at_score_cap_cannot_consolidate(tmp_path):
    async def run():
        store = SQLiteBackend(tmp_path / "group.db")
        await store.init()
        service = favor(store, half_life_base=10, half_life_growth=1.3)
        try:
            with patch("time.time", return_value=START):
                await service.set_favor("g", "u", 100)
                before = await store.query_logs("g", "u")
                change = await service.change("g", "u", 1)
                assert change.delta == 0
                assert (await store.get("g", "u")).half_life == 10
                assert await store.query_logs("g", "u") == before
        finally:
            await store.close()

    asyncio.run(run())


def test_capped_scalar_result_uses_temporary_pause_before_rollback(tmp_path):
    async def run():
        store = SQLiteBackend(tmp_path / "group.db")
        await store.init()
        service = favor(store, half_life_base=5, daily_cap_up=0)
        try:
            with patch("time.time", return_value=START):
                await service.set_relation(
                    "g", "u", familiarity=40, affinity=80, trust=10, event_id="seed"
                )
            with patch("time.time", return_value=START + 86400):
                await service.set_temporary(
                    "g",
                    "u",
                    dict(familiarity=90, affinity=70, trust=60),
                    86400,
                    "admin",
                )
            with patch("time.time", return_value=START + 3 * 86400):
                change = await service.change("g", "u", 1)
                assert change.delta == 0
                assert (
                    change.favor_after
                    == (await service.get_relation("g", "u")).affinity
                    == 60.6
                )
                assert len(await store.query_logs("g", "u")) == 1
        finally:
            await store.close()

    asyncio.run(run())


def test_temporary_relation_blocks_both_undo_preview_and_execution(tmp_path):
    async def run():
        store = SQLiteBackend(tmp_path / "group.db")
        await store.init()
        service = favor(store)
        try:
            with patch("time.time", return_value=START):
                await service.set_relation(
                    "g", "u", familiarity=40, affinity=20, trust=10, event_id="seed"
                )
                log = (await store.query_logs("g", "u"))[0]
                await service.set_temporary(
                    "g",
                    "u",
                    dict(familiarity=90, affinity=70, trust=60),
                    86400,
                    "admin",
                )
                for operation in (service.undo_preview, service.undo_log):
                    with pytest.raises(ValueError, match="临时关系生效"):
                        await operation(log["id"])
                assert (await store.get("g", "u")).favor == 20
        finally:
            await store.close()

    asyncio.run(run())
