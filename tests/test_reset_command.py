"""好感重置入口仅在明确指定范围时调用写服务。"""

import ast
import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def commands(monkeypatch):
    module = ModuleType("astrbot.api.event")
    module.AstrMessageEvent = object
    monkeypatch.setitem(sys.modules, "astrbot.api.event", module)
    from astrbot_plugin_bond.api import commands

    return commands


@pytest.mark.parametrize("mode", ["per_group", "global"])
@pytest.mark.parametrize(
    "target", ["", " \t ", "全部", "全群 80001", " 80001 ", "全群"]
)
def test_reset_handler_scope_and_target(commands, mode, target):
    service = SimpleNamespace(scope_mode=mode, reset=AsyncMock())
    event = SimpleNamespace(get_group_id=lambda: "70001")

    result = asyncio.run(commands.handle_reset(service, event, target))

    if target == " 80001 ":
        service.reset.assert_awaited_once_with("70001", "80001")
        space = "此 Bot 的全局空间" if mode == "global" else "本群"
        assert result == f"已重置 QQ 80001 在{space}的好感度。"
    elif target == "全群" and mode == "per_group":
        service.reset.assert_awaited_once_with("70001", None)
        assert result == "已重置本群全部好感度数据。"
    else:
        service.reset.assert_not_called()
        if target == "全群":
            assert "无法清空" in result
        else:
            assert result.startswith("用法：/好感重置")


@pytest.fixture
def reset_entry(commands):
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    group_helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_group_entry_id"
    )
    plugin = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BondPlugin"
    )
    method = next(
        node
        for node in plugin.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_cmd_reset"
    )
    method.decorator_list.clear()
    module = ast.Module(body=[group_helper, method], type_ignores=[])
    namespace = {"cmd": commands, "AstrMessageEvent": object}
    exec(compile(module, "main.py", "exec"), namespace)
    return namespace["_cmd_reset"]


@pytest.mark.parametrize(
    ("args", "group_id", "shows_usage"),
    [
        ((), "70001", True),
        ((" \t ",), "70001", True),
        (("全群", "80001"), "70001", True),
        ((), "70002", False),
        ((), "", False),
    ],
)
def test_reset_entry_skips_storage_for_missing_or_ambiguous_target(
    reset_entry, args, group_id, shows_usage
):
    runtime = SimpleNamespace(
        group_enabled=lambda gid: gid == "70001", for_event=AsyncMock()
    )
    plugin = SimpleNamespace(_runtime=runtime)
    event = SimpleNamespace(get_group_id=lambda: group_id, plain_result=str)

    async def run():
        return [item async for item in reset_entry(plugin, event, *args)]

    replies = asyncio.run(run())
    assert bool(replies) is shows_usage
    if shows_usage:
        assert replies[0].startswith("用法：/好感重置")
    runtime.for_event.assert_not_called()
