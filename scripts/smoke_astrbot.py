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
    assert len(routes) == 10
    assert set(route_names) == {
        "logs",
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

        class GateProvider:
            async def judge(self, event, text, **kwargs):
                seen["identity"] = (
                    event.get_group_id(),
                    event.get_sender_id(),
                    event.get_self_id(),
                    event.unified_msg_origin,
                    event.get_platform_name(),
                    event.message_id,
                )
                entered.set()
                await release.wait()
                return SimpleNamespace(delta=1.0, reason="smoke", attitude="友好")

        old_deps.judge.judge = GateProvider().judge

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
                    message=[], message_id=self.message_id
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
        assert seen["identity"] == (
            OLD_GROUP,
            OLD_USER,
            BOT_IDS[0],
            "mock:old",
            "mock",
            "message-old",
        )
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
        assert len(context2.registered_web_apis) == 10
    finally:
        await plugin2.terminate()
    assert plugin2._storage._closed is True

    print(
        f"PASS AstrBot {astrbot.__version__}: 2 Bot/4 SQLite、无 bond.db、"
        "scope 持久化、冻结 listener、10 路 WebUI、认证、备份恢复、卸载。"
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
