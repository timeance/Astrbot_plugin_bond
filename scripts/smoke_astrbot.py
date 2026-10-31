"""在独立真实框架环境检查开发基线；不连接平台或模型，不替代宿主端到端验收。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path


async def check(plugin_dir: Path, runtime_dir: Path) -> None:
    import astrbot
    from astrbot.api import AstrBotConfig
    from astrbot.api.star import Context, StarTools
    from quart import Quart

    from astrbot_plugin_bond.main import BondPlugin

    schema = json.loads((plugin_dir / "_conf_schema.json").read_text(encoding="utf-8"))
    config = AstrBotConfig(str(runtime_dir / "bond_config.json"), schema=schema)
    # 无真实管理器、Provider 或网络连接；使用框架实际 Context 注册页面 API。
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
    for cfg in (config, {}):
        plugin = BondPlugin(context, cfg)
        await plugin.initialize()
        try:
            data_dir = StarTools.get_data_dir("astrbot_plugin_bond")
            assert data_dir == runtime_dir / "data/plugin_data/astrbot_plugin_bond"
            assert (data_dir / "bond.db").is_file()
            assert (
                plugin._storage._c().execute("PRAGMA user_version").fetchone()[0] == 9
            )
            assert not plugin._judge._enabled
            assert not plugin._judge._follow_maisoul
            assert not plugin._deps.inject_enabled
            assert not plugin._deps.emotion_coupling
            assert plugin._judge.bot_name == "当前助手"
            routes = context.registered_web_apis
            assert len(routes) == 6
            assert all(
                route.startswith("/astrbot_plugin_bond/") for route, *_ in routes
            )
            assert all(
                methods == ["POST"]
                for route, _, methods, _ in routes
                if route.endswith(("/undo", "/refresh_impression"))
            )
            app = Quart(__name__)
            async with app.test_request_context("/astrbot_plugin_bond/users"):
                response = await plugin._page_api.handle_users()
                body = await response.get_json()
                assert body["success"] is True and body["users"] == []
            assert not (data_dir.parent / "astrbot_plugin_xinxian").exists()
            assert not plugin._registry._tasks
        finally:
            await plugin.terminate()
        assert plugin._storage._conn is None
    print(
        f"PASS AstrBot {astrbot.__version__}: 元数据装配、完整/部分配置安全默认、"
        "独立数据目录、v9 迁移、6 个页面 API 注册、空库查询、卸载关闭连接。"
    )
    print("LIMIT: 未启动完整宿主/登录页面，未连接真实 Bot/Provider/Angel Heart。")


if __name__ == "__main__":
    plugin_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(plugin_dir.parent))
    with tempfile.TemporaryDirectory(prefix="bond-smoke-") as task_dir:
        # 框架导入可能初始化路径；在导入前将所有运行数据约束到临时目录。
        os.environ["ASTRBOT_ROOT"] = task_dir
        asyncio.run(check(plugin_dir, Path(task_dir)))
