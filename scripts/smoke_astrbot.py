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
    context = _context(Context, config)
    plugin = BondPlugin(context, config)
    await plugin.initialize()
    data_dir = StarTools.get_data_dir("astrbot_plugin_bond")
    assert data_dir == runtime_dir / "data/plugin_data/astrbot_plugin_bond"
    assert not (data_dir / "bond.db").exists(), "旧单库不得在装配期创建"

    routes = context.registered_web_apis
    route_names = {route.rsplit("/", 1)[-1]: methods for route, _, methods, _ in routes}
    assert len(routes) == 16
    assert set(route_names) == {
        "logs",
        "livingmemory",
        "relation",
        "profile",
        "prompts",
        "models",
        "assessment_stats",
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
    assert route_names["logs"] == ["GET"]
    assert route_names["groups"] == ["GET"]
    assert route_names["users"] == ["GET"]
    assert route_names["member"] == ["GET"]
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
            "livingmemory",
            "relation",
            "profile",
            "prompts",
            "models",
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

        # 可选插件生命周期只改变探测状态，不改变本地权威数据。
        local_before = await old_deps.favor.get_relation(OLD_GROUP, OLD_USER)
        logs_before = await old_deps.favor.query_logs(OLD_GROUP, OLD_USER)
        profile_before = (await old_deps.favor.get(OLD_GROUP, OLD_USER)).impression
        lookup_count = [0]
        current_memory_plugin = [SimpleNamespace(activated=True, star_cls=object())]

        def find_memory_plugin(name):
            lookup_count[0] += 1
            value = current_memory_plugin[0]
            if isinstance(value, Exception):
                raise value
            return value

        original_lookup = context.get_registered_star
        context.get_registered_star = find_memory_plugin
        memory_url = f"/astrbot_plugin_bond/livingmemory?bot_id={BOT_IDS[0]}&scope_mode=per_group"
        try:
            status, body = await call(
                page.handle_livingmemory, memory_url, username="dashboard-admin"
            )
            assert (
                status == 200
                and body["state"]["status"] == "standalone"
                and lookup_count[0] == 0
            )
            memory_config = dict(enabled=True, read_enabled=True, write_enabled=True)
            status, body = await call(
                page.handle_livingmemory,
                memory_url,
                "POST",
                dict(target, config=memory_config),
                "dashboard-admin",
            )
            assert status == 200 and body["state"]["status"] == "unsupported_scope"
            assert (
                not body["state"]["read"]["available"]
                and not body["state"]["write"]["available"]
            )
            for instance, expected in (
                (SimpleNamespace(activated=False, star_cls=object()), "disabled"),
                (None, "missing"),
                (RuntimeError("registry fixture"), "error"),
                (
                    SimpleNamespace(activated=True, star_cls=object()),
                    "unsupported_scope",
                ),
            ):
                current_memory_plugin[0] = instance
                status, body = await call(
                    page.handle_livingmemory, memory_url, username="dashboard-admin"
                )
                assert status == 200 and body["state"]["status"] == expected
            lookups = lookup_count[0]
            status, body = await call(
                page.handle_livingmemory,
                memory_url,
                "POST",
                dict(
                    target,
                    config=dict(enabled=False, read_enabled=True, write_enabled=False),
                ),
                "dashboard-admin",
            )
            assert status == 200 and body["state"]["status"] == "standalone"
            assert lookup_count[0] == lookups
            status, body = await call(
                page.handle_livingmemory,
                memory_url,
                "POST",
                dict(target, config=dict(enabled="true")),
                "dashboard-admin",
            )
            assert status == 400
        finally:
            context.get_registered_star = original_lookup
        assert await old_deps.favor.get_relation(OLD_GROUP, OLD_USER) == local_before
        assert await old_deps.favor.query_logs(OLD_GROUP, OLD_USER) == logs_before
        assert (
            await old_deps.favor.get(OLD_GROUP, OLD_USER)
        ).impression == profile_before

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
    config2 = AstrBotConfig(str(config_path), schema=schema)
    context2 = _context(Context, config2)
    plugin2 = BondPlugin(context2, config2)
    await plugin2.initialize()
    try:
        assert plugin2._runtime.current_mode(BOT_IDS[0]) == "global"
        assert plugin2._storage.known_bot_ids() == sorted(BOT_IDS)
        assert len(context2.registered_web_apis) == 16
    finally:
        await plugin2.terminate()
    assert plugin2._storage._closed is True

    print(
        f"PASS AstrBot {astrbot.__version__}: 2 Bot/4 SQLite、无 bond.db、"
        "scope 持久化、冻结 listener、16 路 WebUI、认证、备份恢复、卸载。"
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
