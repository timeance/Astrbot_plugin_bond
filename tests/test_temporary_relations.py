"""临时关系的到期、隔离、写入边界与后台任务回归。"""

import asyncio
import sqlite3
import time
from types import SimpleNamespace

import pytest
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.services.impression_service import ImpressionService
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter
from test_assessment import setup, interaction, Provider

VALUES = dict(familiarity=90, affinity=65, trust=-40)


def service(db, **kw):
    return FavorService(db, LevelTable.from_config(None), **kw)


@pytest.mark.parametrize("mode", ["per_group", "global"])
def test_persistence_isolation_titles_and_cancel(tmp_path, mode):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        db = await router.get("10001", mode)
        favor = service(db, scope_mode=mode)
        await favor.set_relation(
            "123", "456", familiarity=30, affinity=20, trust=10, event_id="seed"
        )
        await favor.observe_member("123", "456")
        formal_title = await favor.relation_title("123", "456")
        await favor.set_temporary("123", "456", VALUES, 60, "admin")
        assert (await favor.get_relation("123", "456")).trust == -40
        assert await favor.relation_title("123", "456") != formal_title
        assert (await favor.ranking("123"))[0].favor == 65
        assert (await favor.standings("123"))[0]["trust"] == -40
        assert (await favor.formal_relation("123", "456"))["affinity"] == 20
        assert (await favor.temporary_state("124", "456"))["active"] == (
            mode == "global"
        )
        for bot, other in (
            ("20002", mode),
            ("10001", "global" if mode == "per_group" else "per_group"),
        ):
            assert not (
                await service(
                    await router.get(bot, other), scope_mode=other
                ).temporary_state("123", "456")
            )["active"]
        await router.close()
        router = BotScopeStorageRouter(tmp_path)
        favor = service(await router.get("10001", mode), scope_mode=mode)
        assert (await favor.temporary_state("123", "456"))["active"]
        await favor.set_temporary("123", "456", None, None, "admin")
        assert (await favor.get_relation("123", "456")).affinity == 20
        assert await favor.relation_title("123", "456") == formal_title
        assert len(await favor.query_logs("123", "456")) == 1
        assert [r["action"] for r in await favor.temporary_audit("123", "456")] == [
            "cancelled",
            "created",
        ]
        await router.close()

    asyncio.run(run())


def test_expiry_update_freezes_decay_without_replaying(tmp_path, monkeypatch):
    now = [1000000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])

    async def run():
        router = BotScopeStorageRouter(tmp_path)
        db = await router.get("10001", "per_group")
        favor = service(db, decay_enabled=True, half_life_base=1)
        await favor.set_relation(
            "123", "456", familiarity=30, affinity=80, trust=10, event_id="seed"
        )
        now[0] += 86400
        before = await favor.formal_relation("123", "456")
        await favor.set_temporary("123", "456", VALUES, 86400, "admin")
        now[0] += 43200
        assert await favor.formal_relation("123", "456") == before
        await favor.set_temporary(
            "123", "456", dict(VALUES, affinity=-30), 86400, "admin"
        )
        assert (await favor.get("123", "456")).favor == -30
        now[0] += 86400
        assert not (await favor.temporary_state("123", "456"))["active"]
        assert await favor.formal_relation("123", "456") == before
        assert (await favor.get("123", "456")).favor == before["affinity"]
        stamp = (await db.get("123", "456")).updated_at
        assert not (await favor.temporary_state("123", "456"))["active"]
        assert (await db.get("123", "456")).updated_at == stamp
        now[0] += 86400
        assert (await favor.get("123", "456")).favor < before["affinity"]
        assert len(await favor.query_logs("123", "456")) == 1
        assert [r["action"] for r in await favor.temporary_audit("123", "456")] == [
            "expired",
            "updated",
            "created",
        ]
        await router.close()

    asyncio.run(run())


def test_all_score_writes_and_old_generations_are_blocked(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        db = await router.get("10001", "per_group")
        favor = service(db)
        await favor.set_relation(
            "123", "456", familiarity=30, affinity=20, trust=10, event_id="seed"
        )
        log = (await favor.query_logs("123", "456"))[0]
        await favor.set_temporary("123", "456", VALUES, 60, "admin")
        writes = [
            lambda: favor.set_relation(
                "123", "456", familiarity=0, affinity=0, trust=0, event_id="edit"
            ),
            lambda: favor.set_favor("123", "456", 0),
            lambda: favor.apply_relation_event(
                "123", "456", "thanks", event_id="event"
            ),
            lambda: db.set_value("123", "456", 0),
            lambda: db.apply_delta("123", "456", 1, 100),
            lambda: db.reset("123", "456"),
            lambda: favor.undo_log(log["id"]),
        ]
        for write in writes:
            with pytest.raises(ValueError, match="临时"):
                await write()
        assert (await db.get("123", "456")).favor == 20
        await favor.set_temporary("123", "456", None, None, "admin")
        with pytest.raises(ValueError, match="版本"):
            await favor.apply_relation_event(
                "123", "456", "thanks", event_id="stale", expected_revision=0
            )
        await favor.apply_relation_event(
            "123", "456", "thanks", event_id="new", expected_revision=2
        )
        assert (await favor.get("123", "456")).favor > 20
        await router.close()

    asyncio.run(run())


@pytest.mark.parametrize("duration", [True, 0, 59, 2592001, float("inf"), float("nan")])
def test_invalid_duration_has_no_effect(tmp_path, duration):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        favor = service(await router.get("10001", "per_group"))
        with pytest.raises(ValueError):
            await favor.set_temporary("123", "456", VALUES, duration, "admin")
        assert not (await favor.temporary_state("123", "456"))["active"]
        await router.close()

    asyncio.run(run())


def test_override_and_audit_are_atomic(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        db = await router.get("10001", "per_group")
        favor = service(db)
        db._c().execute(
            "CREATE TRIGGER reject_override BEFORE INSERT ON override_audit BEGIN SELECT RAISE(ABORT, 'fixture'); END"
        )
        db._c().commit()
        with pytest.raises(sqlite3.IntegrityError):
            await favor.set_temporary("123", "456", VALUES, 60, "admin")
        assert not (await favor.temporary_state("123", "456"))["active"]
        assert await db.get("123", "456") is None
        await router.close()

    asyncio.run(run())


@pytest.mark.parametrize("end", ["cancel", "expiry"])
def test_assessment_pauses_and_inflight_result_never_commits(
    tmp_path, monkeypatch, end
):
    now = [1000000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])

    async def run():
        pause = asyncio.Event()
        db, favor, assessment, providers = await setup(tmp_path, Provider(pause=pause))
        task = asyncio.create_task(assessment.assess(interaction(), directed=True))
        await asyncio.sleep(0.01)
        assert providers["small"].calls == 1
        await favor.set_temporary("789", "456", VALUES, 60, "admin")
        assert (
            await assessment.assess(interaction(message="paused"), directed=True)
            == "temporary"
        )
        assert providers["small"].calls == 1
        if end == "cancel":
            await favor.set_temporary("789", "456", None, None, "admin")
        else:
            now[0] += 60
        pause.set()
        assert await task == "temporary"
        assert (await favor.get("789", "456")).favor == 0
        assert await favor.query_logs("789", "456") == []
        assert (
            await assessment.assess(interaction(message="paused"), directed=True)
            == "duplicate_or_cooldown"
        )
        await db.close()

    asyncio.run(run())


def test_impression_paused_and_old_generation_rejected(tmp_path, monkeypatch):
    import sys
    from types import ModuleType

    api = ModuleType("astrbot.api")
    api.logger = SimpleNamespace(info=lambda *a: None, warning=lambda *a: None)
    monkeypatch.setitem(sys.modules, "astrbot.api", api)

    async def run():
        db, favor, _, _ = await setup(tmp_path)
        await favor.set_temporary("789", "456", VALUES, 60, "admin")
        impressions = ImpressionService(db, summarizer=object())
        assert not (await impressions.refresh_now("789", "456"))[0]
        await favor.set_temporary("789", "456", None, None, "admin")
        with pytest.raises(ValueError, match="版本"):
            await db.commit_impression(
                "789", "456", "过期印象", [], [1], expected_revision=0
            )
        assert (await favor.get("789", "456")).impression == ""
        await db.close()

    asyncio.run(run())


def test_queued_message_from_temporary_period_is_not_evaluated_later(tmp_path):
    async def run():
        db, favor, assessment, providers = await setup(tmp_path)
        await favor.set_temporary("789", "456", VALUES, 60, "admin")
        admission = await favor.temporary_state("789", "456")
        await favor.set_temporary("789", "456", None, None, "admin")
        assert (
            await assessment.assess(
                interaction(), directed=True, admission_state=admission
            )
            == "temporary"
        )
        assert providers["small"].calls == 0
        await db.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "values",
    [
        dict(VALUES, familiarity=-1),
        dict(VALUES, affinity=101),
        dict(VALUES, trust=True),
        dict(VALUES, trust=float("nan")),
    ],
)
def test_invalid_temporary_vector_has_no_effect(tmp_path, values):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        favor = service(await router.get("10001", "per_group"))
        with pytest.raises(ValueError):
            await favor.set_temporary("123", "456", values, 60, "admin")
        assert not (await favor.temporary_state("123", "456"))["active"]
        await router.close()

    asyncio.run(run())
