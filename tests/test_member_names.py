import asyncio
from types import SimpleNamespace
from astrbot_plugin_bond.core.member_names import event_names
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter


def test_platform_names_are_separate():
    event = SimpleNamespace(
        message_obj=SimpleNamespace(
            raw_message={"sender": {"nickname": "QQ昵称", "card": "群名片"}}
        )
    )
    assert event_names(event) == ("QQ昵称", "群名片")
    event.message_obj.raw_message["sender"]["card"] = ""
    assert event_names(event) == ("QQ昵称", "")


def test_names_follow_query_group_and_clear_card_without_changing_scores(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        for mode in ("per_group", "global"):
            backend = await router.get("10001", mode)
            service = FavorService(
                backend, LevelTable.from_config(None), scope_mode=mode
            )
            for group, card in (("20002", "甲群名片"), ("30003", "乙群名片")):
                await service.observe_member(group, "40004")
                await service.set_favor(group, "40004", 25)
                await service.update_member_names(group, "40004", "QQ昵称", card)
            assert all(row["nickname"] == "QQ昵称" for row in await service.standings())
            assert (await service.standings("20002"))[0]["nickname"] == "甲群名片"
            await service.update_member_names("20002", "40004", "新昵称", "")
            assert (await service.standings("20002"))[0]["nickname"] == "新昵称"
            assert (await service.standings("30003"))[0]["nickname"] == "乙群名片"
            assert all(row["favor"] == 25 for row in await service.standings())
        await router.close()

    asyncio.run(run())


def test_observing_member_initializes_configured_default_and_ranking_name(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        for mode in ("per_group", "global"):
            backend = await router.get("10001", mode)
            service = FavorService(
                backend, LevelTable.from_config(None), scope_mode=mode, default_favor=17
            )
            await service.observe_member("20002", "40004")
            assert (await service.standings("20002"))[0]["favor"] == 17
            await service.update_member_names("20002", "40004", "QQ昵称", "群名片")
            assert (await service.ranking("20002"))[0].nickname == "群名片"
            await service.set_favor("20002", "40004", 40)
            await service.observe_member("20002", "40004")
            assert (await service.get("20002", "40004")).favor == 40
        await router.close()

    asyncio.run(run())
