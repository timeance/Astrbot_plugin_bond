"""三维关系从服务入口验证持久化、限幅、撤销及事务失败。"""

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter


def svc(db, **kw):
    return FavorService(db, LevelTable.from_config(None), **kw)


def test_relation_persistence_scope_and_mixed_state(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        a = await router.get("10001", "per_group")
        service = svc(a)
        await service.set_relation(
            "123", "456", familiarity=90, affinity=65, trust=-40, event_id="admin:1"
        )
        state = await service.get_relation("123", "456")
        assert (state.familiarity, state.affinity, state.trust) == (90, 65, -40)
        assert "戒备" in await service.relation_title("123", "456")
        assert (await a.ranking("123"))[0].trust == -40
        assert (await a.list_favor("123"))[0].familiarity == 90
        assert (await a.list_favor())[0].trust == -40
        assert (await service.get_relation("124", "456")).familiarity == 0
        for bot, mode in (("10001", "global"), ("20002", "per_group")):
            assert (
                await svc(await router.get(bot, mode)).get_relation("123", "456")
            ).affinity == 0
        await router.close()
        router = BotScopeStorageRouter(tmp_path)
        service = svc(await router.get("10001", "per_group"))
        assert (await service.get_relation("123", "456")).trust == -40
        await router.close()

    asyncio.run(run())


def test_relation_dedup_cooldown_and_independent_caps(tmp_path):
    async def run():
        db = SQLiteBackend(tmp_path / "x.db")
        await db.init()
        service = svc(db)
        first = await service.apply_relation_event("g", "u", "thanks", event_id="m1")
        assert any(first["delta"].values())
        assert (await service.apply_relation_event("g", "u", "thanks", event_id="m1"))[
            "skipped"
        ] == "duplicate"
        assert (await service.apply_relation_event("g", "u", "thanks", event_id="m2"))[
            "skipped"
        ] == "cooldown"
        for i in range(30):
            await service.apply_relation_event(
                "g", "u", "thanks", event_id=f"more:{i}", cooldown_sec=0
            )
        state = await service.get_relation("g", "u")
        assert (
            0 <= state.familiarity <= 2
            and 0 <= state.affinity <= 3
            and 0 <= state.trust <= 1
        )
        # 管理员设置不消耗自然额度，熟悉度与信任可与亲近度相反。
        await service.set_relation(
            "g", "u", familiarity=90, affinity=-60, trust=70, event_id="a1"
        )
        assert (await service.get_relation("g", "u")).trust == 70
        await db.close()

    asyncio.run(run())


def test_relation_undo_and_redo_use_actual_clamped_vector(tmp_path):
    async def run():
        db = SQLiteBackend(tmp_path / "x.db")
        await db.init()
        service = svc(db)
        await service.set_relation(
            "g", "u", familiarity=90, affinity=65, trust=-30, event_id="admin"
        )
        original = (await service.query_logs("g", "u"))[0]
        preview = await service.undo_preview(original["id"])
        await service.undo_log(original["id"])
        state = await service.get_relation("g", "u")
        assert preview["relation_after"] == dict(
            familiarity=state.familiarity, affinity=state.affinity, trust=state.trust
        )
        assert (state.familiarity, state.affinity, state.trust) == (0, 0, 0)
        undo = (await service.query_logs("g", "u"))[0]
        await service.undo_log(undo["id"])
        state = await service.get_relation("g", "u")
        assert (state.familiarity, state.affinity, state.trust) == (90, 65, -30)
        with pytest.raises(ValueError):
            await service.undo_log(original["id"])
        # 旧标量入口只影响亲近度，不能覆盖另外两维。
        await service.change("g", "u", 1)
        state = await service.get_relation("g", "u")
        assert (state.familiarity, state.trust) == (90, -30)
        await db.close()

    asyncio.run(run())


def test_relation_log_failure_rolls_back_every_table(tmp_path):
    async def run():
        db = SQLiteBackend(tmp_path / "x.db")
        await db.init()
        service = svc(db)
        db._c().execute(
            "CREATE TRIGGER reject_relation BEFORE INSERT ON favor_log BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
        db._c().commit()
        with pytest.raises(sqlite3.IntegrityError):
            await service.apply_relation_event("g", "u", "thanks", event_id="same")
        assert await db.get("g", "u") is None
        assert await db.last_event_at("g", "u", "relation") is None
        assert await db.daily_gain("g", "u", service._today().isoformat()) == 0
        db._c().execute("DROP TRIGGER reject_relation")
        db._c().commit()
        assert (
            await service.apply_relation_event("g", "u", "thanks", event_id="same")
        )["skipped"] is None
        await db.close()

    asyncio.run(run())


def test_relation_concurrent_event_is_applied_once(tmp_path):
    db = SQLiteBackend(tmp_path / "x.db")
    asyncio.run(db.init())
    service = svc(db)

    def write(_):
        return asyncio.run(
            service.apply_relation_event(
                "g", "u", "thanks", event_id="same", cooldown_sec=0
            )
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(write, range(8)))
    assert sum(r["skipped"] is None for r in results) == 1
    assert len(asyncio.run(service.query_logs("g", "u"))) == 1
    asyncio.run(db.close())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "100", 101, -101])
def test_invalid_admin_relation_leaves_no_rows(tmp_path, value):
    async def run():
        db = SQLiteBackend(tmp_path / "x.db")
        await db.init()
        with pytest.raises((ValueError, TypeError)):
            await svc(db).set_relation(
                "g", "u", familiarity=0, affinity=value, trust=0, event_id="bad"
            )
        assert await db.get("g", "u") is None
        assert await db.query_logs("g", "u") == []
        await db.close()

    asyncio.run(run())


def test_legacy_extended_range_keeps_preview_and_title_readable(tmp_path):
    async def run():
        db = SQLiteBackend(tmp_path / "x.db")
        await db.init()
        service = svc(db, max_favor=200)
        await service.set_favor("g", "u", 150)
        log = (await service.query_logs("g", "u"))[0]
        assert (await service.get("g", "u")).favor == 150
        assert await service.relation_title("g", "u")
        assert (await service.undo_preview(log["id"]))["after"] == 0
        assert (await db.get("g", "u")).favor == 150
        await db.close()

    asyncio.run(run())


def test_economy_cannot_exceed_single_event_cap(tmp_path):
    from astrbot_plugin_bond.core.level_economy import EconomyConfig

    async def run():
        db = SQLiteBackend(tmp_path / "x.db")
        await db.init()
        result = await svc(db, economy=EconomyConfig()).apply_relation_event(
            "g", "u", "insult", intensity=10, event_id="insult"
        )
        assert result["delta"]["affinity"] == -2
        await db.close()

    asyncio.run(run())


def test_three_dimensional_events_preserve_out_of_range_legacy_score(tmp_path):
    async def run():
        db = SQLiteBackend(tmp_path / "x.db")
        await db.init()
        service = svc(db, max_favor=200)
        await service.set_favor("g", "u", 150)
        with pytest.raises(ValueError, match="历史亲近度"):
            await service.apply_relation_event("g", "u", "thanks", event_id="new")
        assert (await db.get("g", "u")).favor == 150
        await db.close()

    asyncio.run(run())


def test_three_dimensional_new_member_uses_configured_half_life(tmp_path):
    async def run():
        db = SQLiteBackend(tmp_path / "x.db")
        await db.init()
        service = svc(db, decay_enabled=True, half_life_base=5)
        await service.apply_relation_event("g", "u", "thanks", event_id="new")
        assert (await db.get("g", "u")).half_life == 6.5
        await db.close()

    asyncio.run(run())


def test_default_change_preserves_legacy_trust_and_undo_on_reopen(tmp_path):
    from astrbot_plugin_bond.core.relation_policy import RelationPolicy

    async def run():
        db = SQLiteBackend(tmp_path / "upgrade.db")
        await db.init()
        old = svc(db, relation_policy=RelationPolicy(rules={"thanks": {"trust": 0.2}}))
        await old.apply_relation_event(
            "g", "u", "thanks", event_id="old", cooldown_sec=0
        )
        original = (await old.query_logs("g", "u"))[0]
        await db.close()
        await db.init()
        current = svc(db)
        assert (await current.get_relation("g", "u")).trust == 0.2
        fresh = await current.apply_relation_event(
            "g", "u", "apology", event_id="new", cooldown_sec=0
        )
        assert fresh["delta"]["trust"] == 0
        await current.undo_log(original["id"])
        assert (await current.get_relation("g", "u")).trust == 0
        # 换回旧规则不会迁移或重算已经保存的关系。
        await db.close()
        await db.init()
        assert (await old.get_relation("g", "u")).trust == 0
        await db.close()

    asyncio.run(run())
