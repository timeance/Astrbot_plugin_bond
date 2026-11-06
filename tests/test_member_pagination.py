"""成员分页使用完整有效态，折叠不删除，所有数据均为合成身份。"""

import asyncio

import pytest
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter


def service(db, mode="per_group", **kwargs):
    return FavorService(db, LevelTable.from_config(None), scope_mode=mode, **kwargs)


@pytest.mark.parametrize("mode", ["per_group", "global"])
def test_zero_folding_search_and_temporary_effective_state(tmp_path, mode):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            db = await router.get("10001", mode)
            favor = service(db, mode)
            for user in ("8001", "8002", "8003", "8004", "8005"):
                await favor.observe_member("123", user)
            for user, values in (
                ("8002", (1, 0, 0)),
                ("8003", (0, -1, 0)),
                ("8004", (0, 0, -1)),
            ):
                await favor.set_relation(
                    "123",
                    user,
                    familiarity=values[0],
                    affinity=values[1],
                    trust=values[2],
                    event_id=user,
                )
            # 正式三维全零但临时信任非零，必须显示。
            await favor.set_temporary(
                "123", "8005", dict(familiarity=0, affinity=0, trust=2), 60, "fixture"
            )
            # 未观察的人工零值成员不折叠。
            await favor.set_relation(
                "123", "8006", familiarity=0, affinity=0, trust=0, event_id="manual"
            )
            group = "123" if mode == "per_group" else None
            result = await favor.standings_page(group)
            assert result["total"] == 5
            assert result["matched_count"] == 6
            assert result["collapsed_count"] == result["zero_count"] == 1
            assert "8005" in {row["user_id"] for row in result["users"]}
            expanded = await favor.standings_page(group, include_zero=True)
            assert expanded["total"] == 6 and expanded["collapsed_count"] == 0
            found = await favor.standings_page(group, search="8001")
            assert found["total"] == 1 and found["users"][0]["user_id"] == "8001"
            assert found["collapsed_count"] == 0
            # 临时到期后回归正式零值，越界页也需修正。
            await favor.set_temporary("123", "8005", None, None, "fixture")
            last = await favor.standings_page(group, page=99)
            assert last["collapsed_count"] == 2 and last["page"] == 1
            assert len(await favor.standings(group, -1)) == 6
        finally:
            await router.close()

    asyncio.run(run())


@pytest.mark.parametrize("page_size", [30, 50, 100, 200])
def test_complete_pagination_beyond_old_thousand_limit(tmp_path, page_size):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            db = await router.get("10001", "per_group")
            # 相同亲近度、同一人跨群的记录必须有稳定的次级排序。
            pairs = [
                (group, str(10000 + i)) for group in ("123", "124") for i in range(556)
            ]
            db._c().executemany(
                "INSERT INTO favor(group_id,user_id,favor,familiarity,updated_at) VALUES(?,?,1,1,0)",
                reversed(pairs),
            )
            db._c().commit()
            favor = service(db)
            result = await favor.standings_page(page_size=page_size)
            assert result["total"] == result["matched_count"] == 1112
            all_rows = []
            for page in range(1, result["pages"] + 1):
                chunk = await favor.standings_page(page=page, page_size=page_size)
                assert chunk["count"] <= page_size
                all_rows.extend(
                    (row["group_id"], row["user_id"]) for row in chunk["users"]
                )
            assert all_rows == sorted(pairs)
            assert len(set(all_rows)) == 1112
            last = await favor.standings_page(page=9999, page_size=page_size)
            assert last["page"] == result["pages"]
            found = await favor.standings_page(search="10555", page_size=page_size)
            assert found["total"] == 2
            empty = await favor.standings_page(search="99999", page=9)
            assert empty["total"] == 0 and empty["page"] == empty["pages"] == 1
        finally:
            await router.close()

    asyncio.run(run())


def test_group_global_and_bot_isolation(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            for mode in ("per_group", "global"):
                favor = service(await router.get("10001", mode), mode)
                for group, user in (("123", "8001"), ("124", "8002")):
                    await favor.observe_member(group, user)
                    await favor.set_favor(group, user, 1)
                result = await favor.standings_page("123")
                assert [row["user_id"] for row in result["users"]] == ["8001"]
                assert (await favor.standings_page())["total"] == 2
                other = service(await router.get("20002", mode), mode)
                assert (await other.standings_page())["matched_count"] == 0
            # 同一用户仅在另一个群被观察，不折叠本群人工零值记录。
            favor = service(await router.get("10001", "per_group"))
            await favor.set_relation(
                "123",
                "8002",
                familiarity=0,
                affinity=0,
                trust=0,
                event_id="manual-zero",
            )
            result = await favor.standings_page("123")
            assert {row["user_id"] for row in result["users"]} == {"8001", "8002"}
        finally:
            await router.close()

    asyncio.run(run())


def test_page_uses_decay_effective_order(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            favor = service(await router.get("10001", "per_group"), decay_enabled=True)
            await favor.observe_member("123", "8001")
            await favor.observe_member("123", "8002")
            await favor.set_favor("123", "8001", 80)
            await favor.set_favor("123", "8002", 20)
            # 仅替换有效值计算，证明排序和零值折叠不按正式分数抢先 LIMIT。
            favor._effective_record = lambda rec, *args: dict(
                affinity=0 if rec.favor == 80 else rec.favor,
                familiarity=rec.familiarity,
                trust=rec.trust,
            )
            result = await favor.standings_page("123")
            assert result["total"] == 1 and result["collapsed_count"] == 1
            assert result["users"][0]["user_id"] == "8002"
        finally:
            await router.close()

    asyncio.run(run())
