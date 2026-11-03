"""真实 AstrBot 4.28.0 装配 smoke；不连接平台、网络模型或真实 Provider。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

BOT_IDS = ("10000001", "10000002")
OLD_GROUP = "7001"
OLD_USER = "8001"


def _context(Context, config):
    context = Context(
        event_queue=asyncio.Queue(),
        config=config,
        db=None,
        provider_manager=None,
        platform_manager=None,
        conversation_manager=None,
        message_history_manager=None,
        persona_manager=None,
        astrbot_config_mgr=None,
        knowledge_base_manager=None,
        cron_manager=None,
    )
    context.registered_web_apis = []
    return context


async def _drain(async_generator) -> None:
    async for _ in async_generator:
        pass


async def _response_parts(value):
    if isinstance(value, tuple):
        response, status = value[0], value[1]
    else:
        response, status = value, getattr(value, "status_code", 200)
    return status, await response.get_json()


async def check(plugin_dir: Path, runtime_dir: Path) -> None:
    import astrbot
    from astrbot.api import AstrBotConfig
    from astrbot.api.star import Context, StarTools
    from quart import Quart, g

    from astrbot_plugin_bond.main import BondPlugin

    schema = json.loads((plugin_dir / "_conf_schema.json").read_text(encoding="utf-8"))
    config_path = runtime_dir / "bond_config.json"
    config = AstrBotConfig(str(config_path), schema=schema)
    # 隐藏字段仍由 AstrBot 的 schema 加载、保存和重载，不能被清理。
    config["livingmemory"] = {"enabled": True, "read_enabled": True}
    config["group_whitelist"] = [OLD_GROUP]
    config["master_ids"] = OLD_USER
    config["decay"]["baseline"] = 42  # 存量键留档，装配固定采用中性基线0。
    config["inject"]["enabled"] = True
    config["judge"]["roster"] = "synthetic-preserved"
    config["inject"]["memory_days"] = 9
    config.save_config()
    config = AstrBotConfig(str(config_path), schema=schema)
    assert "livingmemory" not in config
    assert config["judge"]["roster"] == "synthetic-preserved"
    assert config["inject"]["memory_days"] == 9
    context = _context(Context, config)
    from aiocqhttp import CQHttp

    client = CQHttp(use_ws_reverse=True)
    client._wsr_api_clients.update({bot_id: object() for bot_id in BOT_IDS})
    # 真实 CQHttp 连接映射，两个账号共享同一个宿主适配器；不建立外部连接。
    context.platform_manager = SimpleNamespace(
        platform_insts=[
            SimpleNamespace(
                meta=lambda: SimpleNamespace(name="aiocqhttp", id="synthetic-adapter"),
                get_client=lambda: client,
            )
        ]
    )

    # 模拟旧版 GET-only 成员路由；升级必须清除，不能命中旧处理器。
    async def stale_member():
        return {"success": False, "error": "stale route"}, 400

    context.register_web_api(
        "/astrbot_plugin_bond/member", stale_member, ["GET"], "old"
    )
    context.register_web_api("/other_plugin/member", stale_member, ["GET"], "unrelated")
    # 两个独立 aiocqhttp 适配器，各自连接一个真实账号。
    second_client = CQHttp(use_ws_reverse=True)
    client._wsr_api_clients.pop(BOT_IDS[1])
    second_client._wsr_api_clients[BOT_IDS[1]] = object()
    context.platform_manager.platform_insts.append(
        SimpleNamespace(
            meta=lambda: SimpleNamespace(name="aiocqhttp", id="second-adapter"),
            get_client=lambda: second_client,
        )
    )
    plugin = BondPlugin(context, config)
    assert all(
        handler is not stale_member
        for route, handler, _, _ in context.registered_web_apis
        if route.startswith("/astrbot_plugin_bond/")
    )
    assert any(
        route == "/other_plugin/member" for route, *_ in context.registered_web_apis
    )
    context.registered_web_apis[:] = [
        entry
        for entry in context.registered_web_apis
        if entry[0] != "/other_plugin/member"
    ]
    await plugin.initialize()
    assert plugin._runtime.known_bot_ids() == sorted(BOT_IDS)
    assert plugin._storage.known_bot_ids() == []
    # 私聊中的真实账号也能登记，但不初始化关系数据库。
    private = SimpleNamespace(
        get_self_id=lambda: "10000003",
        get_group_id=lambda: "",
        get_sender_id=lambda: OLD_USER,
    )
    await _drain(plugin._on_group_msg(private))
    assert "10000003" in plugin._runtime.known_bot_ids()
    assert plugin._storage.known_bot_ids() == []
    assert len(await plugin._runtime.describe()) == 3
    data_dir = StarTools.get_data_dir("astrbot_plugin_bond")
    assert data_dir == runtime_dir / "data/plugin_data/astrbot_plugin_bond"
    assert not (data_dir / "bond.db").exists(), "旧单库不得在装配期创建"

    routes = context.registered_web_apis
    route_names = {route.rsplit("/", 1)[-1]: methods for route, _, methods, _ in routes}
    assert len(routes) == 17
    assert set(route_names) == {
        "logs",
        "relation",
        "profile",
        "prompts",
        "models",
        "settings",
        "assessment_stats",
        "diagnostics",
        "groups",
        "users",
        "undo",
        "member",
        "refresh_impression",
        "bots",
        "mode",
        "backup",
        "restore",
    }
    assert not hasattr(plugin._page_api, "handle_livingmemory")
    assert route_names["logs"] == ["GET"]
    assert route_names["groups"] == ["GET"]
    assert route_names["users"] == ["GET"]
    assert route_names["member"] == ["GET", "POST"]
    from astrbot.dashboard.api.plugins import _match_registered_web_api

    assert (
        _match_registered_web_api(routes, "astrbot_plugin_bond/member", "GET")[
            0
        ].__self__
        is plugin._page_api
    )
    assert route_names["bots"] == ["GET"]
    assert all(
        route_names[name] == ["POST"]
        for name in ("undo", "refresh_impression", "mode", "backup", "restore")
    )

    try:
        for bot_id in BOT_IDS:
            await plugin._runtime.select(bot_id, "per_group")
        db_paths = sorted((data_dir / "bots").glob("*/*.db"))
        assert len(db_paths) == 4
        assert {path.parent.name for path in db_paths} == set(BOT_IDS)
        assert {path.name for path in db_paths} == {"group.db", "global.db"}
        assert not (data_dir / "bond.db").exists()
        assert plugin._storage.known_bot_ids() == sorted(BOT_IDS)

        old_deps = await plugin._runtime.select(BOT_IDS[0], "per_group")
        # 通过真实 main 装配确认新安装默认，而非仅检查配置快照。
        assert old_deps.favor._decay == (30.0, 1.0, 120.0, 0.0)
        assert old_deps.favor._economy.negative_weight == 1.0
        assert old_deps.favor._economy.repair_threshold == 0.0
        entered = asyncio.Event()
        release = asyncio.Event()
        seen = {}

        from astrbot_plugin_bond.services.assessment_service import AssessmentConfig

        old_deps.assessment.config = AssessmentConfig(
            enabled=True, small_provider_id="smoke"
        )

        class GateProvider:
            async def text_chat(self, prompt):
                seen["payload"] = json.loads(prompt.split("输入：", 1)[1])
                entered.set()
                await release.wait()
                return SimpleNamespace(
                    completion_text=json.dumps(
                        {
                            "event_type": "friendly",
                            "confidence": 0.95,
                            "evidence": "冻结身份测试",
                        }
                    ),
                    usage=None,
                )

        old_deps.assessment.context = SimpleNamespace(
            get_provider_by_id=lambda _: GateProvider()
        )

        class FakeEvent:
            def __init__(self):
                self.group = OLD_GROUP
                self.user = OLD_USER
                self.bot = BOT_IDS[0]
                self.umo = "mock:old"
                self.platform = "mock"
                self.message_id = "message-old"
                self.message_str = "冻结身份测试"
                self.message_obj = SimpleNamespace(
                    message=[At(qq=BOT_IDS[0])], message_id=self.message_id
                )

            def plain_result(self, text):
                from astrbot.api.event import AstrMessageEvent

                return AstrMessageEvent.plain_result(self, text)

            def image_result(self, path):
                from astrbot.api.event import AstrMessageEvent

                return AstrMessageEvent.image_result(self, path)

            def get_group_id(self):
                return self.group

            def get_sender_id(self):
                return self.user

            def get_self_id(self):
                return self.bot

            def get_sender_name(self):
                return "smoke-user"

            def get_platform_name(self):
                return self.platform

            @property
            def unified_msg_origin(self):
                return self.umo

        from astrbot.api.message_components import At

        excluded = FakeEvent()
        excluded.group = "60006"
        for handler in (
            plugin._on_group_msg,
            plugin._cmd_self_relation,
            plugin._cmd_rank,
            plugin._cmd_set,
            plugin._cmd_reset,
            plugin._cmd_set_rel,
            plugin._cmd_set_tags,
            plugin._cmd_refresh_imp,
        ):
            assert [item async for item in handler(excluded)] == []
        request = SimpleNamespace(system_prompt="当前人格", prompt="输入", contexts=[])
        await plugin._on_llm_req(excluded, request)
        assert request.system_prompt == "当前人格"
        for handler in (plugin._tool_query, plugin._tool_rank):
            result = json.loads(await handler(excluded))
            assert result["status"] == "unavailable"
            assert result["facts"] == {"reason": "本群未启用关系功能。"}
        assert not plugin._registry._tasks and not entered.is_set()
        for bot_id in BOT_IDS:
            assert "60006" not in {
                row["group_id"]
                for row in await (
                    await plugin._runtime.select(bot_id)
                ).favor.distinct_groups()
            }
        configured_request = SimpleNamespace(
            system_prompt="当前人格", prompt="输入", contexts=[]
        )
        await plugin._on_llm_req(FakeEvent(), configured_request)
        assert configured_request.system_prompt.startswith("当前人格\n\n")
        assert (
            "维护者在插件配置中将当前用户设为主人" in configured_request.system_prompt
        )
        other = FakeEvent()
        other.user = "8002"
        other_request = SimpleNamespace(
            system_prompt="当前人格", prompt="输入", contexts=[]
        )
        await plugin._on_llm_req(other, other_request)
        assert "维护者在插件配置中将当前用户设为主人" not in other_request.system_prompt

        # 真实装配入口返回资料；不让固定测试响应冒充人格表达验收。
        query = json.loads(await plugin._tool_query(FakeEvent()))
        assert query["status"] == "ok" and query["subject"] == "当前 Bot"
        assert query["facts"] == {
            "object": "当前发言者",
            "relation_title": await old_deps.favor.relation_title(OLD_GROUP, OLD_USER),
        }
        assert (
            json.loads(await plugin._tool_query(FakeEvent(), "8002"))["status"]
            == "unavailable"
        )
        private = FakeEvent()
        private.group = ""
        for handler in (plugin._tool_query, plugin._tool_rank):
            assert json.loads(await handler(private))["status"] == "unavailable"
        rank_context = json.loads(await plugin._tool_rank(FakeEvent()))
        assert rank_context["subject"] == "当前 Bot"
        assert rank_context["facts"]["objects"] == "当前群成员"
        assert len(rank_context["facts"]["ranking"]) <= 10
        # 新排行权限由三种真实装配入口统一检查，不因群管理员或模型工具绕过。
        from unittest.mock import AsyncMock, patch

        plugin._ranking_access = "master"
        denied = FakeEvent()
        denied.user = "8002"
        with patch.object(old_deps.favor, "ranking", new=AsyncMock()) as queried:
            with patch.object(
                plugin._ranking_avatars, "load", new=AsyncMock()
            ) as fetched:
                assert (
                    json.loads(await plugin._tool_rank(denied))["status"]
                    == "unavailable"
                )
                assert len([item async for item in plugin._cmd_rank(denied)]) == 1
                plugin._text_wake_enabled = True
                denied.message_str = "好感排行"
                denied._bond_cmd_done = False
                with patch(
                    "astrbot_plugin_bond.main.on_group_message", new=AsyncMock()
                ):
                    denied_replies = [
                        item async for item in plugin._on_group_msg(denied)
                    ]
                    assert len(denied_replies) == 1
                    assert (
                        denied_replies[0].chain[0].text
                        == "本群排行仅允许配置的主人查询。"
                    )
                queried.assert_not_called()
                fetched.assert_not_called()
        await old_deps.favor.observe_member(OLD_GROUP, OLD_USER)
        with patch.object(
            plugin._ranking_avatars, "load", new=AsyncMock(return_value=[None])
        ) as fetched:
            # 渲染确实生成本地PNG；头像为合成占位，不连接真实平台。
            replies = [item async for item in plugin._cmd_rank(FakeEvent())]
            assert len(replies) == 1
            fetched.assert_awaited_once()
            assert any(
                type(component).__name__ == "Image" for component in replies[0].chain
            )
        plugin._ranking_access = "all"
        plugin._text_wake_enabled = False

        event = FakeEvent()
        dispatch = asyncio.create_task(_drain(plugin._on_group_msg(event)))
        await asyncio.wait_for(entered.wait(), timeout=2)
        event.group = "9001"
        event.user = "9002"
        event.bot = BOT_IDS[1]
        event.umo = "mock:mutated"
        event.platform = "mutated"
        event.message_id = "message-mutated"
        event.message_obj.message_id = "message-mutated"
        await plugin._runtime.switch_mode(BOT_IDS[0], "global")
        global_deps = await plugin._runtime.select(BOT_IDS[0], "global")
        judge_task = next(
            task
            for task, name in plugin._registry._tasks.items()
            if name.startswith("judge:")
        )
        release.set()
        await asyncio.wait_for(dispatch, timeout=2)
        await asyncio.wait_for(judge_task, timeout=2)
        assert seen["payload"] == {
            "target_bot": BOT_IDS[0],
            "sender": OLD_USER,
            "text": "冻结身份测试",
        }
        old_record = await old_deps.favor.get(OLD_GROUP, OLD_USER)
        assert old_record is not None and old_record.favor > 0
        mutated_record = await old_deps.favor.get("9001", "9002")
        assert mutated_record.favor == 0
        assert await old_deps.favor.query_logs("9001", "9002", limit=10) == []
        global_record = await global_deps.favor.get(OLD_GROUP, OLD_USER)
        assert global_record.favor == 0
        assert await global_deps.favor.query_logs(OLD_GROUP, OLD_USER, limit=10) == []
        assert plugin._runtime.current_mode(BOT_IDS[0]) == "global"
        assert json.loads(config.get("bot_scope_modes"))[BOT_IDS[0]] == "global"

        # 文字唤醒普通 hook 的异常必须被宿主边界吞掉；不连接真实模型。
        from astrbot_plugin_bond.api import commands as command_api

        original_text_wake = command_api.try_text_wake

        async def broken_text_wake(*args, **kwargs):
            raise RuntimeError("smoke text wake failure")

        command_api.try_text_wake = broken_text_wake
        plugin._text_wake_enabled = True
        try:
            await asyncio.wait_for(_drain(plugin._on_group_msg(FakeEvent())), timeout=2)
        finally:
            command_api.try_text_wake = original_text_wake
            plugin._text_wake_enabled = False

        second_event = FakeEvent()
        second_event.bot = BOT_IDS[1]
        second_event.user = "8008"
        second_event.message_obj.raw_message = {
            "sender": {"nickname": "测试昵称", "card": "测试名片"}
        }
        await _drain(plugin._on_group_msg(second_event))
        await asyncio.sleep(0.05)
        second_deps = await plugin._runtime.select(BOT_IDS[1])
        second_rows = await second_deps.favor.standings(OLD_GROUP)
        assert any(
            row["user_id"] == "8008" and row["nickname"] == "测试名片"
            for row in second_rows
        ), second_rows
        assert await second_deps.favor.member_name(None, "8008") == "测试昵称"

        app = Quart(__name__)
        page = plugin._page_api

        async def call(handler, path, method="GET", body=None, username=None):
            kwargs = {"method": method}
            if body is not None:
                kwargs["json"] = body
            async with app.test_request_context(path, **kwargs):
                if username is not None:
                    g.username = username
                return await _response_parts(await handler())

        target = {
            "bot_id": BOT_IDS[0],
            "scope_mode": "per_group",
            "group_id": OLD_GROUP,
            "user_id": OLD_USER,
        }
        for endpoint in (
            "relation",
            "profile",
            "prompts",
            "models",
            "settings",
            "undo",
            "refresh_impression",
            "mode",
            "backup",
            "restore",
        ):
            status, body = await call(
                getattr(page, "handle_" + endpoint),
                "/astrbot_plugin_bond/" + endpoint,
                "POST",
                {},
                None,
            )
            assert status == 401, (endpoint, body)

        status, body = await call(
            page.handle_relation,
            "/astrbot_plugin_bond/relation",
            "POST",
            dict(
                target,
                familiarity=80,
                affinity=41.7,
                trust=-30,
                reason="smoke adjustment",
            ),
            "dashboard-admin",
        )
        assert status == 200 and body["relation"]["trust"] == -30
        # 临时修改复用受保护的关系入口；正式值与审计独立保留。
        temporary = dict(
            target,
            action="temporary",
            familiarity=90,
            affinity=-60,
            trust=70,
            duration_sec=60,
        )
        status, _ = await call(
            page.handle_relation, "/astrbot_plugin_bond/relation", "POST", temporary
        )
        assert status == 401
        status, body = await call(
            page.handle_relation,
            "/astrbot_plugin_bond/relation",
            "POST",
            temporary,
            "dashboard-admin",
        )
        assert status == 200, body
        assert (await old_deps.favor.get(OLD_GROUP, OLD_USER)).favor == -60
        assert (await old_deps.favor.formal_relation(OLD_GROUP, OLD_USER))[
            "affinity"
        ] == 41.7
        audit = await old_deps.favor.temporary_audit(OLD_GROUP, OLD_USER)
        assert audit[0]["actor"] == "dashboard-admin"
        status, _ = await call(
            page.handle_relation,
            "/astrbot_plugin_bond/relation",
            "POST",
            dict(target, familiarity=0, affinity=0, trust=0),
            "dashboard-admin",
        )
        assert status == 400
        status, _ = await call(
            page.handle_relation,
            "/astrbot_plugin_bond/relation",
            "POST",
            dict(target, action="cancel_temporary"),
            "dashboard-admin",
        )
        assert status == 200
        assert (await old_deps.favor.get(OLD_GROUP, OLD_USER)).favor == 41.7
        adjustment = (await old_deps.favor.query_logs(OLD_GROUP, OLD_USER))[0]
        assert "dashboard-admin" in adjustment["reason"]
        status, body = await call(
            page.handle_relation,
            "/astrbot_plugin_bond/relation",
            "POST",
            dict(target, familiarity=101, affinity=0, trust=0),
            "dashboard-admin",
        )
        assert status == 400
        status, body = await call(
            page.handle_profile,
            "/astrbot_plugin_bond/profile",
            "POST",
            dict(target, impression="本地印象", tags=["直率"]),
            "dashboard-admin",
        )
        assert status == 200
        profile_log = (await old_deps.favor.query_logs(OLD_GROUP, OLD_USER))[0]
        assert profile_log["source"] == "admin_profile"
        status, body = await call(
            page.handle_undo,
            "/astrbot_plugin_bond/undo",
            "POST",
            dict(target, id=profile_log["id"]),
            "dashboard-admin",
        )
        assert status == 400
        status, body = await call(
            page.handle_undo,
            "/astrbot_plugin_bond/undo",
            "POST",
            dict(target, id=adjustment["id"]),
            "dashboard-admin",
        )
        assert status == 200
        assert (
            "dashboard-admin"
            in (await old_deps.favor.query_logs(OLD_GROUP, OLD_USER))[0]["reason"]
        )

        prompt_target = dict(target, kind="behavior", level="group")
        status, body = await call(
            page.handle_prompts,
            "/astrbot_plugin_bond/prompts",
            "POST",
            dict(
                prompt_target,
                action="preview",
                mode="custom",
                template="关系：{relationship}",
            ),
            "dashboard-admin",
        )
        assert status == 200 and "熟悉的群友" in body["preview"]
        status, body = await call(
            page.handle_prompts,
            "/astrbot_plugin_bond/prompts",
            "POST",
            dict(
                prompt_target,
                action="save",
                mode="custom",
                template="关系：{relationship}",
            ),
            "dashboard-admin",
        )
        assert status == 200 and body["settings"]["effective"]["source"] == "group"
        status, body = await call(
            page.handle_prompts,
            "/astrbot_plugin_bond/prompts",
            "POST",
            dict(prompt_target, action="save", mode="custom", template="{unknown}"),
            "dashboard-admin",
        )
        assert status == 400
        status, body = await call(
            page.handle_prompts,
            "/astrbot_plugin_bond/prompts",
            "POST",
            dict(target, action="reset"),
            "dashboard-admin",
        )
        assert status == 400

        from astrbot_plugin_bond.api import llm_tools

        public_event = SimpleNamespace(
            get_group_id=lambda: OLD_GROUP, get_sender_id=lambda: OLD_USER
        )
        assert "只能查询" in await llm_tools.tool_query_favor(
            old_deps.favor, public_event, "999"
        )
        public = await llm_tools.tool_query_favor(old_deps.favor, public_event)
        assert "分值" not in public and "41.7" not in public and "小千" not in public
        for index in range(12):
            await old_deps.favor.set_relation(
                OLD_GROUP,
                str(9000 + index),
                familiarity=50,
                affinity=51.7,
                trust=-20,
                event_id=f"rank:{index}",
            )
        ranking = await llm_tools.tool_query_ranking(old_deps.favor, public_event, 100)
        assert len(ranking.splitlines()) == 11 and "51.7" not in ranking
        assert "小千" not in ranking
        status, settings = await call(
            page.handle_settings,
            f"/astrbot_plugin_bond/settings?bot_id={BOT_IDS[0]}&scope_mode=per_group",
            username="dashboard-admin",
        )
        assert status == 200 and settings["values"]["inject.memory_days"] == 9
        status, saved = await call(
            page.handle_settings,
            "/astrbot_plugin_bond/settings",
            "POST",
            dict(
                target, values={"inject.memory_days": 10}, revision=settings["revision"]
            ),
            "dashboard-admin",
        )
        assert status == 200 and saved["restart_required"]
        assert config["inject"]["memory_days"] == 10
        assert plugin._dependency_config["inject"]["memory_days"] == 9
        status, rejected = await call(
            page.handle_settings,
            "/astrbot_plugin_bond/settings",
            "POST",
            dict(target, values={"bot_scope_modes": "{}"}, revision=saved["revision"]),
            "dashboard-admin",
        )
        assert status == 400 and not rejected["success"]
        status, body = await call(
            page.handle_models,
            f"/astrbot_plugin_bond/models?bot_id={BOT_IDS[0]}&scope_mode=per_group",
            username="dashboard-admin",
        )
        assert status == 200 and "active" in body and "configured" in body
        configured = dict(body["configured"], small_provider_id="new-provider")
        status, body = await call(
            page.handle_models,
            "/astrbot_plugin_bond/models",
            "POST",
            dict(target, config=configured, max_concurrent_assessments=3),
            "dashboard-admin",
        )
        assert status == 200 and body["restart_required"]
        assert old_deps.assessment.config.small_provider_id == "smoke"
        assert (
            await plugin._runtime.select(BOT_IDS[1], "global")
        ).assessment.config.small_provider_id == ""

        for username, expected in ((None, 401), ("dashboard-admin", 200)):
            status, body = await call(
                page.handle_assessment_stats,
                f"/astrbot_plugin_bond/assessment_stats?bot_id={BOT_IDS[0]}&scope_mode=per_group",
                username=username,
            )
            assert status == expected
            if expected == 200:
                assert body["stats"]["events"]["accepted"] == 1
            status, body = await call(
                page.handle_diagnostics,
                f"/astrbot_plugin_bond/diagnostics?bot_id={BOT_IDS[0]}&scope_mode=per_group",
                username=username,
            )
            assert status == expected
            if expected == 200:
                assert body["diagnostics"]["enabled"] is False

        status, body = await call(
            page.handle_users,
            f"/astrbot_plugin_bond/users?bot_id={BOT_IDS[0]}&scope_mode=global",
        )
        assert status == 401 and body == {"error": "未授权", "success": False}
        status, body = await call(
            page.handle_users,
            f"/astrbot_plugin_bond/users?bot_id={BOT_IDS[0]}&scope_mode=global",
            username="dashboard-admin",
        )
        assert status == 200 and body["success"] is True
        status, body = await call(
            page.handle_mode,
            "/astrbot_plugin_bond/mode",
            method="POST",
            body={"bot_id": BOT_IDS[1], "scope_mode": "global"},
        )
        assert status == 401 and body["error"] == "未授权"
        status, body = await call(
            page.handle_undo,
            "/astrbot_plugin_bond/undo",
            method="POST",
            body={"bot_id": BOT_IDS[0], "scope_mode": "global", "id": 1.9},
            username="dashboard-admin",
        )
        assert status == 400 and body["error"] == "请求参数无效"

        await global_deps.favor.set_favor(OLD_GROUP, "7000123", 10)
        from urllib.parse import quote

        for fragment, fuzzy, expected in (
            ("000", 1, 200),
            ("001", 1, 200),
            ("000", 0, 400),
            ("１２", 1, 400),
            ("%", 1, 400),
            ("1" * 33, 1, 400),
        ):
            status, body = await call(
                page.handle_logs,
                f"/astrbot_plugin_bond/logs?bot_id={BOT_IDS[0]}&scope_mode=global&user_id={quote(fragment)}&fuzzy={fuzzy}",
                username="dashboard-admin",
            )
            assert status == expected, (fragment, status, body)
            if fragment == "001":
                assert any(r["user_id"] == "7000123" for r in body["logs"])

        await global_deps.favor.set_relation(
            OLD_GROUP,
            "7000124",
            familiarity=90,
            affinity=65,
            trust=-40,
            event_id="admin:smoke",
        )
        state = await global_deps.favor.get_relation(OLD_GROUP, "7000124")
        assert (state.familiarity, state.affinity, state.trust) == (90, 65, -40)
        status, body = await call(
            page.handle_member,
            f"/astrbot_plugin_bond/member?bot_id={BOT_IDS[0]}&scope_mode=global&group_id={OLD_GROUP}&user_id=7000124",
            username="dashboard-admin",
        )
        assert status == 200 and body["member"]["trust"] == -40
        assert "戒备" in body["member"]["relation_title"]

        for suffix in ("", "&group_id="):
            status, body = await call(
                page.handle_member,
                f"/astrbot_plugin_bond/member?bot_id={BOT_IDS[0]}&scope_mode=global&user_id=7000124{suffix}",
                username="dashboard-admin",
            )
            assert status == 200, (suffix, status, body)

        # 备份/恢复走页面 API，且目标始终显式携带 Bot 与 scope。
        await old_deps.favor.set_favor(OLD_GROUP, OLD_USER, 12)
        status, body = await call(
            page.handle_backup,
            "/astrbot_plugin_bond/backup",
            method="POST",
            body={"bot_id": BOT_IDS[0], "scope_mode": "per_group"},
            username="dashboard-admin",
        )
        assert status == 200 and body["success"] is True
        backup_name = body["backup_name"]
        await old_deps.favor.set_favor(OLD_GROUP, OLD_USER, 20)
        status, body = await call(
            page.handle_restore,
            "/astrbot_plugin_bond/restore",
            method="POST",
            body={
                "bot_id": BOT_IDS[0],
                "scope_mode": "per_group",
                "backup_name": backup_name,
            },
            username="dashboard-admin",
        )
        assert status == 200 and body["success"] is True
        restored = await old_deps.favor.get(OLD_GROUP, OLD_USER)
        assert restored is not None and restored.favor == 12
        assert (data_dir / "backups" / BOT_IDS[0] / backup_name).is_file()
    finally:
        await plugin.terminate()

    assert not hasattr(plugin._storage, "_conn")
    assert plugin._storage._closed is True
    assert not plugin._storage._backends
    assert not plugin._registry._tasks

    # 从同一配置文件重新装配，确认 scope 选择持久化且已知 Bot 仍来自 router。
    prompts_path = data_dir / "prompts.json"
    saved_prompts = prompts_path.read_bytes()
    prompts_path.write_text("{broken", encoding="utf-8")
    config2 = AstrBotConfig(str(config_path), schema=schema)
    config2["inject"]["enabled"] = True
    config2["relation_mode"] = "galgame"
    # 存量显式关闭和旧参数不能被升级后的默认值覆盖。
    config2["decay"].update(
        enabled=False, half_life_base=10, half_life_growth=1.3, half_life_max=60
    )
    config2["economy"].update(negative_weight=1.5, repair_threshold=2)
    config2.save_config()
    config2 = AstrBotConfig(str(config_path), schema=schema)
    context2 = _context(Context, config2)
    plugin2 = BondPlugin(context2, config2)
    await plugin2.initialize()
    try:
        assert plugin2._runtime.current_mode(BOT_IDS[0]) == "global"
        assert plugin2._storage.known_bot_ids() == sorted(BOT_IDS)
        assert len(context2.registered_web_apis) == 17
        assert plugin2._prompts.load_error
        deps2 = await plugin2._runtime.select(BOT_IDS[0], "per_group")
        assert deps2.favor._decay is None
        assert deps2.favor._relation_caps == dict(
            familiarity=(3, 0), affinity=(4.5, 6), trust=(1, 4)
        )
        assert deps2.favor._relation_policy.deltas("friendly")["affinity"] == 1.0
        assert plugin2._dependency_config["decay"]["half_life_base"] == 10
        assert plugin2._dependency_config["decay"]["half_life_max"] == 60
        assert plugin2._dependency_config["decay"]["half_life_growth"] == 1.3
        assert deps2.favor._economy.negative_weight == 1.5
        assert deps2.favor._economy.repair_threshold == 2
        assert (await deps2.favor.get(OLD_GROUP, OLD_USER)).favor == 12
        event2 = SimpleNamespace(
            get_group_id=lambda: OLD_GROUP,
            get_sender_id=lambda: OLD_USER,
            get_self_id=lambda: BOT_IDS[0],
        )
        req2 = SimpleNamespace(system_prompt="当前人格", prompt="输入", contexts=[])
        await plugin2._on_llm_req(event2, req2)
        assert req2.system_prompt == "当前人格" and req2.prompt == "输入"
        prompt_url = "/astrbot_plugin_bond/prompts"
        target2 = dict(
            bot_id=BOT_IDS[0], scope_mode="per_group", kind="behavior", level="global"
        )
        status, body = await call(
            plugin2._page_api.handle_prompts,
            prompt_url,
            "POST",
            dict(target2, action="reset"),
            "dashboard-admin",
        )
        assert status == 503 and "修复或恢复" in body["error"]
        status, _ = await call(
            plugin2._page_api.handle_prompts,
            prompt_url,
            "POST",
            dict(target2, action="reset"),
        )
        assert status == 401
        assert prompts_path.read_text(encoding="utf-8") == "{broken"
    finally:
        await plugin2.terminate()
        prompts_path.write_bytes(saved_prompts)
    assert plugin2._storage._closed is True

    print(
        f"PASS AstrBot {astrbot.__version__}: 2 Bot/4 SQLite、无 bond.db、"
        "新旧衰减配置、scope 持久化、冻结 listener、17 路 WebUI、群白名单、主人标记、人格中立查询资料、私聊 Bot 发现、认证、备份恢复、损坏提示词降级、卸载。"
    )
    print(
        "LIMIT: 未启动完整 AstrBot dashboard HTTP server；认证通过 Quart g.username"
        "模拟宿主中间件已写入的管理面板用户，不连接真实平台或模型。"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--session-dir", default=os.environ.get("BOND_SMOKE_SESSION_DIR")
    )
    parser.add_argument(
        "--runtime-dir", default=os.environ.get("BOND_SMOKE_RUNTIME_DIR")
    )
    args = parser.parse_args()
    if not args.session_dir or not args.runtime_dir:
        parser.error("必须由调用者提供 --session-dir 与 --runtime-dir")
    session_dir = Path(args.session_dir).resolve()
    runtime_dir = Path(args.runtime_dir).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    if runtime_dir.exists() and any(runtime_dir.iterdir()):
        raise SystemExit(f"runtime-dir 必须为空: {runtime_dir}")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = str(session_dir)
    os.environ["TMP"] = str(session_dir)
    os.environ["ASTRBOT_ROOT"] = str(runtime_dir)
    plugin_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(plugin_dir.parent))
    asyncio.run(check(plugin_dir, runtime_dir))


if __name__ == "__main__":
    main()
