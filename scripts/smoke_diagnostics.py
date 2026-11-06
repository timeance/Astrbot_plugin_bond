"""真实 AstrBot 4.28.0 诊断装配与宿主认证模拟；仅合成数据、无网络。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from smoke_astrbot import _context, _response_parts


async def check(root, runtime):
    from astrbot.api import AstrBotConfig
    from astrbot.api.star import Context, StarTools
    from quart import Quart, g
    from astrbot_plugin_bond.main import BondPlugin
    from astrbot_plugin_bond.api.listeners import on_llm_request

    schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
    assert schema["diagnostics"]["items"]["enabled"]["default"] is False
    assert schema["diagnostics"]["items"]["detail_level"]["default"] == "assessment"
    # 宿主在装配插件前补全 schema 默认值；升级旧开关不能顺带开启原文采集。
    for level in (None, "raw", "evaluation", "full"):
        old = {"diagnostics": {"enabled": True}}
        if level is not None:
            old["diagnostics"]["detail_level"] = level
        path = runtime / f"legacy-{level or 'missing'}.json"
        path.write_text(json.dumps(old), encoding="utf-8")
        legacy = AstrBotConfig(str(path), schema=schema)
        expected = level or "assessment"
        assert legacy["diagnostics"]["detail_level"] == expected
        assert (
            json.loads(path.read_text(encoding="utf-8-sig"))["diagnostics"][
                "detail_level"
            ]
            == expected
        )
        probe = BondPlugin(_context(Context, legacy), legacy)
        try:
            assert probe._diagnostics.detail_level == expected
            assert probe._diagnostics.wants("chat") == (level is not None)
            assert probe._diagnostics.wants("request") == (level == "full")
        finally:
            await probe.terminate()
        if level is None:
            # 不经宿主补全的调用同样使用保守默认值。
            legacy["diagnostics"].pop("detail_level")
            probe = BondPlugin(_context(Context, legacy), legacy)
            try:
                assert probe._diagnostics.detail_level == "assessment"
                assert not probe._diagnostics.wants("chat")
            finally:
                await probe.terminate()
    config = AstrBotConfig(str(runtime / "config.json"), schema=schema)
    config["diagnostics"]["enabled"] = True
    config["inject"]["enabled"] = True
    config.save_config()
    config = AstrBotConfig(str(runtime / "config.json"), schema=schema)
    plugin = BondPlugin(_context(Context, config), config)
    app = Quart(__name__)
    bots = ("10000001", "10000002")
    group, user = "70000001", "80000001"

    async def call(bot, mode, action="status", method="GET", authenticated=True):
        route = f"/diagnostics?bot_id={bot}&scope_mode={mode}&action={action}"
        async with app.test_request_context(route, method=method):
            if authenticated:
                g.username = "synthetic-admin"
            return await _response_parts(await plugin._page_api.handle_diagnostics())

    try:
        states = {}
        for bot in bots:
            for mode in ("per_group", "global"):
                deps = await plugin._runtime.select(bot, mode)
                await deps.favor.set_relation(
                    group,
                    user,
                    familiarity=85,
                    affinity=60,
                    trust=50,
                    event_id=f"synthetic-{bot}-{mode}",
                )
                req = SimpleNamespace(
                    system_prompt="合成人格", prompt="合成输入", contexts=[]
                )
                await on_llm_request(
                    deps,
                    SimpleNamespace(
                        get_group_id=lambda: group,
                        get_sender_id=lambda: user,
                        get_self_id=lambda: bot,
                        get_platform_name=lambda: "synthetic",
                        message_obj=SimpleNamespace(message_id=f"render-{bot}-{mode}"),
                    ),
                    req,
                )
                assert req.system_prompt.startswith("合成人格")
                states[(bot, mode)] = (
                    await deps.favor.get_relation(group, user)
                ).as_dict()
        await plugin._diagnostics.flush()
        namespaces = set()
        counts = {}
        for bot in bots:
            for mode in ("per_group", "global"):
                status, body = await call(bot, mode, "export")
                assert status == 200 and body["success"]
                data = body["diagnostics"]
                assert data["rows"] and {r["mode"] for r in data["rows"]} == {mode}
                ns = {r["namespace"] for r in data["rows"]}
                assert len(ns) == 1 and not ns.intersection(namespaces)
                namespaces.update(ns)
                counts[(bot, mode)] = len(data["rows"])
                kinds = {r["kind"] for r in data["rows"]}
                assert {"relation", "render"} <= kinds
                serialized = json.dumps(data, ensure_ascii=False)
                assert all(
                    value not in serialized
                    for value in (*bots, group, user, "合成输入", "合成人格")
                )
        for method, action in (("GET", "export"), ("POST", "clear")):
            status, body = await call(bots[0], "per_group", action, method, False)
            assert status == 401 and body["error"] == "请先登录管理面板"
        status, _ = await call("99999999", "per_group", "export")
        assert status == 400
        status, _ = await call(bots[0], "per_group", "clear", "GET")
        assert status == 400
        erase = plugin._diagnostics._erase

        def failed_erase(selector):
            raise OSError("synthetic inaccessible disk")

        plugin._diagnostics._erase = failed_erase
        status, body = await call(bots[0], "per_group", "clear", "POST")
        assert status == 500 and body["success"] is False
        plugin._diagnostics._erase = erase
        status, _ = await call(bots[0], "per_group", "clear", "POST")
        assert status == 200
        for bot in bots:
            for mode in ("per_group", "global"):
                status, body = await call(bot, mode, "export")
                expected = (
                    0 if (bot, mode) == (bots[0], "per_group") else counts[(bot, mode)]
                )
                assert len(body["diagnostics"]["rows"]) == expected
                deps = await plugin._runtime.select(bot, mode)
                assert (await deps.favor.get_relation(group, user)).as_dict() == states[
                    (bot, mode)
                ]
        # 关闭开关并真实重载后仍能管理已有记录；普通关闭路径不生成新文件。
        config["diagnostics"]["enabled"] = False
        config.save_config()
    finally:
        await plugin.terminate()
    plugin = BondPlugin(_context(Context, config), config)
    try:
        status, body = await call(bots[1], "global", "export")
        assert status == 200 and body["diagnostics"]["rows"]
        assert body["diagnostics"]["stats"]["enabled"] is False
        status, _ = await call(bots[1], "global", "clear", "POST")
        assert status == 200
        status, body = await call(bots[1], "global", "export")
        assert body["diagnostics"]["rows"] == []
    finally:
        await plugin.terminate()
    print(
        "PASS diagnostics: 2 Bot/2 scope、旧配置默认档位与显式选择、真实配置重载、脱敏导出、认证拒绝、选择性清除、正式状态不变、关闭后历史管理。"
    )
    print("LIMIT: Quart认证上下文为合成，不是完整HTTP登录/浏览器或真实聊天验收。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime_dir.resolve()
    if runtime.exists() and any(runtime.iterdir()):
        parser.error("必须使用空运行目录")
    runtime.mkdir(parents=True, exist_ok=True)
    os.environ["ASTRBOT_ROOT"] = str(runtime)
    os.environ["TEMP"] = str(runtime)
    os.environ["TMP"] = str(runtime)
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent))
    asyncio.run(check(root, runtime))


if __name__ == "__main__":
    main()
