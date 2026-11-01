import asyncio
from types import SimpleNamespace
from astrbot_plugin_bond.services.bot_discovery import BotDiscovery
from astrbot_plugin_bond.services.runtime_service import RuntimeService
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter


def adapter(connections, *, name="aiocqhttp", identifier="adapter-not-account"):
    return SimpleNamespace(
        meta=lambda: SimpleNamespace(name=name, id=identifier),
        get_client=lambda: SimpleNamespace(_wsr_api_clients=connections),
    )


def test_multiple_accounts_one_adapter_without_chat_and_dynamic_disconnect():
    connections = {
        "10001": object(),
        "20002": object(),
        "*": object(),
        "bad": object(),
        "030": object(),
    }
    context = SimpleNamespace(
        platform_manager=SimpleNamespace(
            platform_insts=[
                adapter(connections),
                adapter({"30003": object()}, name="unsupported"),
            ]
        )
    )
    discovery = BotDiscovery(context)
    assert discovery.account_ids() == {"10001", "20002"}
    del connections["20002"]
    assert discovery.account_ids() == {"10001"}
    connections["40004"] = object()
    assert discovery.account_ids() == {"10001", "40004"}


def test_two_adapters_deduplicate_and_skip_initializing_adapter():
    broken = SimpleNamespace(meta=lambda: (_ for _ in ()).throw(RuntimeError()))
    discovery = BotDiscovery(
        SimpleNamespace(
            platform_manager=SimpleNamespace(
                platform_insts=[
                    adapter({"10001": 1}),
                    broken,
                    adapter({"20002": 1, "10001": 1}),
                ]
            )
        )
    )
    assert discovery.account_ids() == {"10001", "20002"}
    assert BotDiscovery(SimpleNamespace()).account_ids() == set()


def test_connected_accounts_and_existing_data_share_target_allowlist(tmp_path):
    async def run():
        connections = {"10001": 1, "20002": 1}
        storage = BotScopeStorageRouter(tmp_path)
        context = SimpleNamespace(
            platform_manager=SimpleNamespace(platform_insts=[adapter(connections)])
        )

        class Config(dict):
            def save_config(self):
                pass

        runtime = RuntimeService(
            storage,
            Config(),
            lambda backend, mode: backend,
            bot_discovery=BotDiscovery(context),
        )
        assert len(await runtime.describe()) == 2
        assert storage.known_bot_ids() == []  # 发现账号本身不创建数据库。
        await runtime.switch_mode("20002", "global")
        await runtime.select("20002")
        assert runtime.current_mode("20002") == "global"
        connections.clear()
        assert runtime.known_bot_ids() == ["20002"]
        await storage.close()

    asyncio.run(run())


def test_private_and_excluded_group_events_register_bots_without_creating_data(
    tmp_path,
):
    async def run():
        storage = BotScopeStorageRouter(tmp_path)
        runtime = RuntimeService(
            storage, {"group_whitelist": ["30003"]}, lambda b, m: b
        )
        for group, bot in (("", "10001"), ("40004", "20002")):
            event = SimpleNamespace(
                get_group_id=lambda: group,
                get_self_id=lambda: bot,
                get_sender_id=lambda: "50005",
            )
            assert await runtime.for_event(event) is None
        assert runtime.known_bot_ids() == ["10001", "20002"]
        assert storage.known_bot_ids() == []
        allowed = SimpleNamespace(
            get_group_id=lambda: "30003",
            get_self_id=lambda: "20002",
            get_sender_id=lambda: "50005",
        )
        assert await runtime.for_event(allowed) is not None
        assert storage.known_bot_ids() == ["20002"]
        await storage.close()

    asyncio.run(run())


def test_default_mode_and_whitelist_reload_keep_bot_overrides_and_existing_data(
    tmp_path,
):
    async def run():
        class Config(dict):
            def save_config(self):
                pass

        storage = BotScopeStorageRouter(tmp_path)
        config = Config(
            default_scope_mode="global",
            group_whitelist=[],
            bot_scope_modes='{"10001":"per_group"}',
        )
        runtime = RuntimeService(storage, config, lambda b, m: b)
        assert runtime.current_mode("10001") == "per_group"
        assert runtime.current_mode("20002") == "global"
        assert runtime.group_enabled("30003") and runtime.group_enabled("40004")
        previous = await runtime.select("10001")
        revision = runtime.detailed_settings()["revision"]
        await runtime.save_detailed_settings(
            {
                "group_whitelist": ["30003"],
                "default_scope_mode": "per_group",
                "master_ids": "50005",
            },
            revision,
        )
        assert runtime.group_enabled("40004")  # 保存后重载统一生效。
        assert runtime.current_mode("20002") == "global"
        reloaded = RuntimeService(storage, config, lambda b, m: b)
        assert not reloaded.group_enabled("40004")
        assert reloaded.current_mode("10001") == "per_group"
        assert reloaded.current_mode("20002") == "per_group"
        assert await reloaded.select("10001") is previous
        assert config["master_ids"] == "50005"
        await storage.close()

    asyncio.run(run())
