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
