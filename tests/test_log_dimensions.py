"""流水维度筛选先于条数限制；旧历史未知、撤销仍是完整事件。"""

import asyncio
import json

import pytest
from test_member_pagination import service
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter


@pytest.mark.parametrize("mode", ["per_group", "global"])
def test_dimension_and_source_filter_before_limit_preserves_legacy(tmp_path, mode):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            db = await router.get("10001", mode)
            favor = service(db, mode)
            group = favor.group_key("123")
            records = [
                (0, "judge", 1, {"familiarity": 1, "affinity": 0, "trust": 0}),
                (0, "judge", 2, {"familiarity": 0, "affinity": 0, "trust": -1}),
                (0, "admin", 3, {"familiarity": 1, "affinity": 0, "trust": 1}),
                (2, "judge", 4, {}),
            ] + [
                (1, "judge", ts, {"familiarity": 0, "affinity": 1, "trust": 0})
                for ts in range(5, 50)
            ]
            db._c().executemany(
                "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,source,ts,relation_delta) VALUES(?,?,?,0,?,?,?,?)",
                [
                    (group, "8001", delta, delta, source, ts, json.dumps(vector))
                    for delta, source, ts, vector in records
                ],
            )
            db._c().commit()
            f = await favor.query_logs(
                "123", None, limit=1, dimension="familiarity", source="judge"
            )
            t = await favor.query_logs(
                "123", None, limit=1, dimension="trust", source="judge"
            )
            assert f[0]["ts"] == 1 and t[0]["ts"] == 2
            assert (
                await favor.query_logs("123", None, limit=1, dimension="familiarity")
            )[0]["source"] == "admin"
            a = await favor.query_logs("123", None, limit=100, dimension="affinity")
            assert any(row["ts"] == 4 and row["relation_delta"] == {} for row in a)
            legacy = [
                row
                for row in await favor.query_logs("123", None, limit=100)
                if row["ts"] == 4
            ][0]
            assert (
                "familiarity" not in legacy["relation_delta"]
                and "trust" not in legacy["relation_delta"]
            )
            assert not await favor.query_logs("123", "8002", dimension="trust")
            assert (
                await service(await router.get("20002", mode), mode).query_logs(
                    "123", None, dimension="trust"
                )
            ) == []
        finally:
            await router.close()

    asyncio.run(run())


def test_dimension_view_does_not_change_whole_event_undo(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            favor = service(await router.get("10001", "per_group"))
            await favor.set_relation(
                "123", "8001", familiarity=3, affinity=2, trust=1, event_id="all-three"
            )
            records = await favor.query_logs("123", None, dimension="familiarity")
            assert len(records) == 1
            await favor.undo_log(records[0]["id"], actor="fixture")
            state = await favor.get_relation("123", "8001")
            assert (state.familiarity, state.affinity, state.trust) == (0, 0, 0)
            assert (await favor.query_logs("123", None, dimension="trust"))[0][
                "source"
            ] == "undo"
        finally:
            await router.close()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["per_group", "global"])
def test_passive_batches_paginate_after_grouping_and_filtering(tmp_path, mode):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            db = await router.get("10001", mode)
            favor = service(db, mode)
            stored_group = favor.group_key("123")
            entries = [
                ("8001", "trigger-a", "123", 100, 0.3, "passive_familiarity"),
                ("8002", "trigger-a", "123", 101, 0.2, "passive_familiarity"),
                ("8003", "trigger-a", "123", 105, 0.12345, "passive_familiarity"),
                ("8004", "trigger-a", "123", 110, 0.3, "passive_familiarity"),
                ("8005", "trigger-a", "999", 110, 0.3, "passive_familiarity"),
                ("8006", "trigger-b", "123", 110, 0.3, "passive_familiarity"),
                ("8007", "", "", 110, 0.3, "passive_familiarity"),
                ("8008", "", "", 110, -0.3, "passive_familiarity"),
                ("8009", "direct", "123", 120, 0, "judge"),
            ]
            db._c().executemany(
                "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,source,ts,relation_delta,event_id,origin_group_id) "
                "VALUES(?,?,0,2,2,?,?,?,?,?)",
                [
                    (
                        stored_group,
                        user,
                        source,
                        ts,
                        json.dumps({"familiarity": delta, "affinity": 0, "trust": 0}),
                        event,
                        origin,
                    )
                    for user, event, origin, ts, delta, source in entries
                ],
            )
            db._c().commit()
            raw = await favor.query_logs("123", None, limit=100)
            grouped = await favor.query_logs("123", None, limit=100, group_passive=True)
            assert len(raw) == 9 and len(grouped) == 6
            batch = next(row for row in grouped if len(row.get("members", [])) == 4)
            assert batch["origin_group_id"] == "123"
            assert {row["user_id"] for row in batch["members"]} == {
                "8001",
                "8002",
                "8003",
                "8004",
            }
            assert (
                next(row for row in batch["members"] if row["user_id"] == "8003")[
                    "relation_delta"
                ]["familiarity"]
                == 0.12345
            )
            paged = []
            for offset in range(6):
                page = await favor.query_logs(
                    "123", None, limit=1, offset=offset, group_passive=True
                )
                assert len(page) == 1
                paged.extend(page)
            assert paged == grouped
            assert (
                await favor.query_logs(
                    "123", None, limit=1, offset=6, group_passive=True
                )
                == []
            )
            flattened = [
                member for row in grouped for member in row.get("members", [row])
            ]
            assert sorted(row["id"] for row in flattened) == sorted(
                row["id"] for row in raw
            )
            assert await db.get_log(batch["members"][0]["id"]) == batch["members"][0]
            assert (
                len(
                    await favor.query_logs(
                        "123", None, dimension="familiarity", group_passive=True
                    )
                )
                == 5
            )
            assert (
                len(
                    await favor.query_logs(
                        "123", None, source="judge", group_passive=True
                    )
                )
                == 1
            )
            searched = await favor.query_logs("123", "8003", group_passive=True)
            assert [member["user_id"] for member in searched[0]["members"]] == ["8003"]
            # 另一个存储群即使碰巧共用触发标识和来源群，也不能归为同一批。
            db._c().execute(
                "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,source,ts,event_id,origin_group_id) "
                "VALUES('456','8010',0,0,0,'passive_familiarity',100,'trigger-a','123')"
            )
            db._c().commit()
            all_groups = await db.query_logs(limit=100, group_passive=True)
            assert len(all_groups) == 7
        finally:
            await router.close()

    asyncio.run(run())


def test_grouped_passive_undo_keeps_other_members_and_audit_rows(tmp_path):
    import time

    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            db = await router.get("10001", "global")
            favor = service(db, "global")
            now = time.time()
            for user in ("8001", "8002"):
                assert await favor.apply_passive_familiarity(
                    "123",
                    user,
                    trigger_id="same-trigger",
                    observed_at=now,
                    triggered_at=now,
                )
            batch = (await favor.query_logs("123", None, group_passive=True))[0]
            assert len(batch["members"]) == 2
            target = next(row for row in batch["members"] if row["user_id"] == "8001")
            await favor.undo_log(target["id"], actor="fixture")
            after = await favor.query_logs("123", None, group_passive=True)
            original = next(row for row in after if row.get("members"))
            assert next(row for row in original["members"] if row["user_id"] == "8001")[
                "reversed"
            ]
            assert not next(
                row for row in original["members"] if row["user_id"] == "8002"
            )["reversed"]
            assert (
                await favor.get_relation("123", "8001")
            ).familiarity == pytest.approx(0, abs=0.001)
            assert (
                await favor.get_relation("123", "8002")
            ).familiarity == pytest.approx(0.3, abs=0.001)
            assert len(await favor.query_logs("123", None)) == 3
            assert all(row["origin_group_id"] == "123" for row in after)
        finally:
            await router.close()

    asyncio.run(run())
