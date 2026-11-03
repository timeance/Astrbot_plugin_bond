"""从服务入口验证路由、切换和后台任务捕获，不依赖框架桩。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.taskregistry import TaskRegistry
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.services.impression_service import ImpressionService
from astrbot_plugin_bond.services.runtime_service import RuntimeService
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter
from astrbot_plugin_bond.api.facade import BondFacade


class Config(dict):
    def save_config(self):
        self.saved = dict(self)


def factory(storage, mode):
    return SimpleNamespace(
        favor=FavorService(storage, LevelTable.from_config({}), scope_mode=mode),
        impressions=ImpressionService(
            storage, registry=TaskRegistry(), scope_mode=mode
        ),
    )


def test_switch_keeps_old_task_target_and_restarts(tmp_path):
    async def run():
        config = Config()
        storage = BotScopeStorageRouter(tmp_path)
        runtime = RuntimeService(storage, config, factory)
        event = SimpleNamespace(
            get_self_id=lambda: "10000001",
            get_group_id=lambda: "123456789",
            get_sender_id=lambda: "123456780",
        )
        old_task_bundle = await runtime.for_event(event)
        await old_task_bundle.favor.set_favor("123456789", "123456780", 20)
        await old_task_bundle.impressions.set_tags("123456789", "123456780", ["熟人"])
        await runtime.switch_mode("10000001", "global")
        global_bundle = await runtime.for_event(event)
        assert (await global_bundle.favor.get("123456789", "123456780")).favor == 0
        # 此写入模拟慢 Provider 在切换后返回。
        await old_task_bundle.favor.change("123456789", "123456780", 1)
        assert (await global_bundle.favor.get("123456789", "123456780")).favor == 0
        await global_bundle.favor.set_favor("123456789", "123456780", 70)
        assert (await global_bundle.favor.get("987654321", "123456780")).favor == 70
        other_bot = await runtime.select("20000002")
        assert (await other_bot.favor.get("123456789", "123456780")).favor == 0
        assert runtime.current_mode("20000002") == "per_group"
        await runtime.switch_mode("10000001", "per_group")
        restored = await runtime.for_event(event)
        record = await restored.favor.get("123456789", "123456780")
        assert record.favor == 21 and record.parsed_tags() == ["熟人"]
        assert (await restored.favor.get("987654321", "123456780")).favor == 0
        await storage.close()
        storage = BotScopeStorageRouter(tmp_path)
        restarted = RuntimeService(storage, Config(config.saved), factory)
        assert restarted.current_mode("10000001") == "per_group"
        assert (
            await (await restarted.select("10000001", "global")).favor.get(
                "123456789", "123456780"
            )
        ).favor == 70
        await storage.close()

    asyncio.run(run())


def test_global_ranking_only_observed_members_and_shared_profile(tmp_path):
    async def run():
        storage = BotScopeStorageRouter(tmp_path)
        runtime = RuntimeService(storage, Config(), factory)
        bundle = await runtime.select("10000001", "global")
        favor = bundle.favor
        await favor.set_favor("123456789", "123456780", 40)
        await favor.set_favor("987654321", "123456781", 90)
        await favor.observe_member("123456789", "123456780")
        await favor.observe_member("987654321", "123456781")
        assert [r.user_id for r in await favor.ranking("123456789")] == ["123456780"]
        assert [r["user_id"] for r in await favor.standings("123456789")] == [
            "123456780"
        ]
        await bundle.impressions.set_tags("123456789", "123456780", ["可靠"])
        assert (await favor.get("987654321", "123456780")).parsed_tags() == ["可靠"]
        facade = BondFacade(runtime)
        with pytest.raises(ValueError, match="没有观察"):
            await facade.get_profile("10000001", "global", "123456789", "123456781")
        assert "favor" not in await facade.get_profile(
            "10000001", "global", "123456789", "123456780"
        )
        await storage.close()

    asyncio.run(run())


def test_failed_config_save_does_not_switch_mode(tmp_path):
    class BrokenConfig(Config):
        def save_config(self):
            raise OSError("synthetic failure")

    async def run():
        storage = BotScopeStorageRouter(tmp_path)
        config = BrokenConfig()
        runtime = RuntimeService(storage, config, factory)
        await runtime.select("10000001")
        with pytest.raises(OSError):
            await runtime.switch_mode("10000001", "global")
        assert runtime.current_mode("10000001") == "per_group"
        await storage.close()

    asyncio.run(run())


@pytest.mark.parametrize("modes", [[], {"../1": "global"}, {"10000001": "unknown"}])
def test_bad_persisted_modes_fail_closed(tmp_path, modes):
    runtime = RuntimeService(None, {"bot_scope_modes": json.dumps(modes)}, factory)
    with pytest.raises(ValueError):
        runtime.modes()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_values_never_reach_storage(tmp_path, value):
    async def run():
        storage = BotScopeStorageRouter(tmp_path)
        runtime = RuntimeService(storage, Config(), factory)
        bundle = await runtime.select("10000001")
        with pytest.raises(ValueError):
            await bundle.favor.set_favor("123456789", "123456780", value)
        with pytest.raises(ValueError):
            await bundle.favor.change("123456789", "123456780", value)
        assert await bundle.favor.query_logs("123456789", "123456780") == []
        await storage.close()

    asyncio.run(run())


def test_restore_invalidates_nickname_cache(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        runtime = RuntimeService(router, Config(), factory)
        bundle = await runtime.select("10001")
        backup = await router.backup("10001", "per_group")
        await bundle.favor.touch_nickname("123", "456", "测试")
        assert await bundle.favor.standings("123")
        await runtime.restore("10001", "per_group", backup.name)
        assert not await bundle.favor.standings("123")
        await bundle.favor.touch_nickname("123", "456", "测试")
        assert (await bundle.favor.get("123", "456")).nickname == "测试"
        assert await bundle.favor.standings("123")
        await router.close()

    asyncio.run(run())
