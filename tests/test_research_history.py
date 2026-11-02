"""历史流水的来源完整性回归；只验证存储事实，不替代行为金标。"""

import asyncio
import sqlite3
from contextlib import closing

import pytest

from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_policy import RelationPolicy
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.migrations import MIGRATIONS, SCHEMA_VERSION
from astrbot_plugin_bond.storage.pragma_version import set_user_version
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend


@pytest.fixture
def history(tmp_path):
    db = SQLiteBackend(tmp_path / "history.db")
    asyncio.run(db.init())
    # 固定旧致谢规则，以验证升级后仍能解释/撤销既有三维流水。
    service = FavorService(
        db,
        LevelTable.from_config(None),
        relation_policy=RelationPolicy(rules={"thanks": {"trust": 0.2}}),
    )
    try:
        yield db, service
    finally:
        asyncio.run(db.close())


def test_a3_pagination_is_a_read_window_not_retention(history, monkeypatch):
    db, _ = history
    service = FavorService(
        db,
        LevelTable.from_config(None),
        relation_caps={
            key: (1000, 1000) for key in ("familiarity", "affinity", "trust")
        },
    )
    # 相同时间戳同时验证 id 次序；205 条跨越服务 20 条和后端 200 条默认窗口。
    monkeypatch.setattr(
        "astrbot_plugin_bond.storage.sqlite_backend.time.time", lambda: 1_800_000_000
    )

    async def run():
        total = 205
        for index in range(total):
            await service.apply_relation_event(
                "g", "u", "normal_question", event_id=f"page:{index}", cooldown_sec=0
            )
        complete = await service.query_logs("g", "u", limit=total + 1)
        assert len(complete) == total
        assert [row["event_id"] for row in complete] == [
            f"page:{index}" for index in reversed(range(total))
        ]
        assert len(await service.query_logs("g", "u")) == 20
        assert len(await db.query_logs("g", "u")) == 200

        pages = []
        for offset in range(0, total, 20):
            pages.extend(await service.query_logs("g", "u", offset=offset))
        # A3 先核对完整来源行及其顺序，不能把短窗口差异报成历史证据丢失。
        assert pages == complete
        assert len({row["id"] for row in pages}) == total
        assert await service.query_logs("g", "u", offset=total) == []
        assert all(row["source"] == "judge" for row in pages)
        assert all(row["relation_delta"]["familiarity"] == 0.1 for row in pages)
        await db.close()
        await db.init()
        assert await service.query_logs("g", "u", limit=total + 1) == complete
        assert await db.get_log(complete[-1]["id"]) == complete[-1]

    asyncio.run(run())


def test_recent_events_filters_without_deleting_history(history, monkeypatch):
    db, service = history
    now = [1_800_000_000.0]
    monkeypatch.setattr(
        "astrbot_plugin_bond.storage.sqlite_backend.time.time", lambda: now[0]
    )

    async def run():
        end = now[0]
        for age in (15, 8, 0):
            now[0] = end - age * 86400
            await service.apply_relation_event(
                "g", "u", "thanks", event_id=f"age:{age}", cooldown_sec=0
            )
        complete = await service.query_logs("g", "u")
        assert [r["event_id"] for r in await service.recent_events("g", "u")] == [
            "age:0"
        ]
        significant = await service.recent_events(
            "g", "u", sig_threshold=0.5, sig_window_mult=2
        )
        assert [r["event_id"] for r in significant] == ["age:0", "age:8"]
        assert await service.recent_events("g", "u", days=0) == complete
        assert await service.recent_events("g", "u", count=0) == []
        assert await service.query_logs("g", "u") == complete
        await db.close()
        await db.init()
        assert await service.query_logs("g", "u") == complete

    asyncio.run(run())


@pytest.mark.parametrize("user_id", ["u", None], ids=["member", "group"])
def test_reset_explicitly_deletes_only_selected_history(history, user_id):
    db, service = history

    async def run():
        for group, user in (("g", "u"), ("g", "other"), ("other", "u")):
            await service.apply_relation_event(
                group, user, "thanks", event_id=f"{group}:{user}", cooldown_sec=0
            )
        target = (await service.query_logs("g", "u"))[0]
        other_member = await service.query_logs("g", "other")
        other_group = await service.query_logs("other", "u")
        await service.reset("g", user_id)
        assert await service.query_logs("g", "u") == []
        assert await db.get_log(target["id"]) is None
        assert await db.get("g", "u") is None
        assert await service.query_logs("g", "other") == (
            other_member if user_id else []
        )
        assert await service.query_logs("other", "u") == other_group
        await db.close()
        await db.init()
        assert await db.get_log(target["id"]) is None
        assert await service.query_logs("other", "u") == other_group

    asyncio.run(run())


def test_undo_redo_preserves_actual_vectors_sources_and_reversed_marks(history):
    db, service = history

    async def run():
        await service.set_relation(
            "g", "u", familiarity=99.9, affinity=99.9, trust=99.9, event_id="seed"
        )
        applied = await service.apply_relation_event(
            "g", "u", "thanks", event_id="clipped", cooldown_sec=0
        )
        actual = dict(familiarity=0.1, affinity=0.1, trust=0.1)
        assert applied["delta"] == actual
        original = (await service.query_logs("g", "u"))[0]
        assert original["source"] == "judge"
        assert original["delta"] == 0.1
        assert original["relation_delta"] == actual

        await service.undo_log(original["id"])
        undone = (await service.query_logs("g", "u"))[0]
        assert (await db.get_log(original["id"]))["reversed"] is True
        assert undone["source"] == "undo"
        assert undone["reversed"] is False
        assert undone["event_id"] is None
        assert undone["reason"] == f"撤销#{original['id']}"
        assert undone["delta"] == -0.1
        assert undone["relation_delta"] == {key: -0.1 for key in actual}
        assert (await service.get_relation("g", "u")).as_dict() == dict.fromkeys(
            actual, 99.9
        )

        await service.undo_log(undone["id"])
        redone = (await service.query_logs("g", "u"))[0]
        assert redone["source"] == "undo"
        assert redone["reason"] == f"撤销#{undone['id']}"
        assert redone["event_id"] is None
        assert redone["delta"] == 0.1
        assert redone["relation_delta"] == actual
        assert redone["reversed"] is False
        assert (await service.get_relation("g", "u")).as_dict() == dict.fromkeys(
            actual, 100.0
        )
        assert (await db.get_log(original["id"]))["reversed"] is True
        assert (await db.get_log(undone["id"]))["reversed"] is True
        complete = await service.query_logs("g", "u")
        assert len(complete) == 4
        assert len({row["id"] for row in complete}) == 4
        recent = await service.recent_events("g", "u", count=10, days=0)
        assert original["id"] not in {row["id"] for row in recent}
        assert undone["id"] not in {row["id"] for row in recent}
        with pytest.raises(ValueError, match="已撤销"):
            await service.undo_log(original["id"])
        assert await service.query_logs("g", "u") == complete

    asyncio.run(run())


def test_manual_edits_remain_distinct_from_judged_behavior(history):
    _, service = history

    async def run():
        await service.apply_relation_event(
            "g", "u", "thanks", event_id="natural", cooldown_sec=0
        )
        await service.set_relation(
            "g", "u", familiarity=30, affinity=20, trust=10, event_id="manual"
        )
        await service.set_favor("g", "u", 25)
        before = await service.get_relation("g", "u")
        await service.set_profile(
            "g", "u", impression="合成资料", tags=["测试"], actor="synthetic-admin"
        )
        logs = await service.query_logs("g", "u")
        assert [row["source"] for row in reversed(logs)] == [
            "judge",
            "admin",
            "admin",
            "admin_profile",
        ]
        vector_edit = next(row for row in logs if row["event_id"] == "manual")
        assert vector_edit["relation_delta"] == dict(
            familiarity=29.8, affinity=19.5, trust=9.8
        )
        scalar_edit = next(
            row for row in logs if row["source"] == "admin" and row["event_id"] is None
        )
        assert scalar_edit["delta"] == 5
        assert scalar_edit["relation_delta"] == {}
        profile = logs[0]
        assert profile["delta"] == 0
        assert profile["relation_delta"] == {}
        assert "synthetic-admin" in profile["reason"]
        assert await service.get_relation("g", "u") == before
        with pytest.raises(ValueError, match="资料审计"):
            await service.undo_preview(profile["id"])
        assert await service.query_logs("g", "u") == logs

    asyncio.run(run())


def test_legacy_missing_vector_stays_missing_after_migration(tmp_path):
    path = tmp_path / "legacy.db"
    # 唯一直接写数据的夹具：真实 v11 结构没有 relation_delta/event_id。
    with closing(sqlite3.connect(path)) as conn:
        for version in range(1, 12):
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
            set_user_version(conn, version)
            conn.commit()
        conn.execute(
            "INSERT INTO favor(group_id,user_id,favor,updated_at) VALUES(?,?,?,?)",
            ("g", "u", 3, 1_700_000_000),
        )
        conn.execute(
            "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,"
            "reason,source,ts,message) VALUES(?,?,?,?,?,?,?,?,?)",
            ("g", "u", 3, 0, 3, "旧版评估", "judge", 1_700_000_000, "合成旧消息"),
        )
        conn.commit()

    async def run():
        db = SQLiteBackend(path)
        await db.init()
        service = FavorService(db, LevelTable.from_config(None))
        try:
            with closing(sqlite3.connect(path)) as conn:
                assert (
                    conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
                )
            legacy = (await service.query_logs("g", "u"))[0]
            assert legacy["source"] == "judge"
            assert legacy["delta"] == 3
            assert legacy["relation_delta"] == {}
            assert legacy["event_id"] is None
            # 缺失向量与显式零向量不同，迁移不能补造三维行为证据。
            await service.apply_relation_event(
                "g", "u", "uncertain", event_id="explicit-zero", cooldown_sec=0
            )
            current = (await service.query_logs("g", "u"))[0]
            assert current["relation_delta"] == dict(
                familiarity=0.0, affinity=0.0, trust=0.0
            )
            assert current["relation_delta"] != legacy["relation_delta"]
            assert await db.get_log(legacy["id"]) == legacy
            await db.close()
            await db.init()
            assert await db.get_log(legacy["id"]) == legacy
        finally:
            await db.close()

    asyncio.run(run())
