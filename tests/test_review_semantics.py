"""PR-3 review 修复的行为回归测试。"""

import ast
import asyncio
import copy
import sys
from types import ModuleType
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.services.impression_service import ImpressionService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend


@pytest.fixture
def backend(tmp_path):
    storage = SQLiteBackend(tmp_path / "review.db")
    asyncio.run(storage.init())
    yield storage
    asyncio.run(storage.close())


def test_impression_interval_consumes_the_full_scheduled_batch(backend, monkeypatch):
    api_module = ModuleType("astrbot.api")
    api_module.logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )
    astrbot_module = ModuleType("astrbot")
    astrbot_module.api = api_module
    monkeypatch.setitem(sys.modules, "astrbot", astrbot_module)
    monkeypatch.setitem(sys.modules, "astrbot.api", api_module)

    async def run():
        prompts = []

        class Judge:
            async def resolve_display_name(self, umo):
                return "测试"

            async def resolve_summary_provider(self):
                return self

            async def text_chat(self, *, prompt):
                prompts.append(prompt)
                return SimpleNamespace(completion_text="印象:批次完整\n标签:无")

        service = ImpressionService(backend, summarizer=Judge(), interval=25)
        for index in range(25):
            await backend.add_log(
                "g",
                "u",
                1,
                index,
                index + 1,
                f"理由{index}",
                "judge",
                index,
                message=f"样本{index}",
            )

        await service.maybe_refresh("g", "u")
        await asyncio.gather(*tuple(service._tasks))

        assert len(prompts) == 1
        assert "样本0" in prompts[0] and "样本24" in prompts[0]
        assert await backend.impression_logs("g", "u", 25) == []
        assert (
            backend._c()
            .execute(
                "SELECT log_id FROM impression_progress WHERE group_id='g' AND user_id='u'"
            )
            .fetchone()[0]
            == 25
        )

        await service.maybe_refresh("g", "u")
        assert len(prompts) == 1

    asyncio.run(run())


def test_undo_uses_relation_bounds_for_three_dimensional_logs_and_config_for_legacy(
    backend,
):
    async def run():
        service = FavorService(
            backend,
            LevelTable.from_config(None),
            max_favor=200,
            min_favor=-200,
            daily_cap_up=100,
            daily_cap_down=100,
        )

        await service.set_relation(
            "g", "relation", familiarity=0, affinity=99, trust=0, event_id="seed"
        )
        await service.apply_relation_event(
            "g", "relation", "insult", event_id="insult", cooldown_sec=0
        )
        await service.set_favor("g", "relation", 100)
        relation_log = next(
            row
            for row in await service.query_logs("g", "relation")
            if row["event_id"] == "insult"
        )
        preview = await service.undo_preview(relation_log["id"])
        assert preview["after"] == 100
        assert (await service.undo_log(relation_log["id"])).favor == 100

        await service.set_favor("g", "legacy", 150)
        await service.change("g", "legacy", -1, source="api")
        await service.set_favor("g", "legacy", 150)
        legacy_log = next(
            row
            for row in await service.query_logs("g", "legacy")
            if row["source"] == "api"
        )
        preview = await service.undo_preview(legacy_log["id"])
        assert preview["after"] == 151
        assert (await service.undo_log(legacy_log["id"])).favor == 151

    asyncio.run(run())


@pytest.mark.parametrize("method_name", ["_cmd_rank", "_tool_rank"])
def test_main_ranking_entries_reject_private_event_before_runtime(method_name):
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    group_helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_group_entry_id"
    )
    method = next(
        node
        for node in plugin.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == method_name
    )
    group_helper = copy.deepcopy(group_helper)
    method = copy.deepcopy(method)
    method.decorator_list = []
    for arg in (*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs):
        arg.annotation = None
    method.args.vararg = None
    method.args.kwarg = None
    method.returns = None
    module = ast.Module(body=[group_helper, method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"_RANKING_GROUP_REQUIRED": "请在群聊中查询本群排行。"}
    exec(compile(module, "main.py", "exec"), namespace)

    method_fn = namespace[method_name]

    class UnreachableRuntime:
        async def for_event(self, event):
            raise AssertionError("私聊排行不应触达 RuntimeService.for_event")

    class Event:
        def get_group_id(self):
            return ""

        def plain_result(self, payload):
            return ("plain", payload)

    Plugin = type("Plugin", (), {method_name: method_fn})
    plugin = Plugin()
    plugin._runtime = UnreachableRuntime()
    plugin._ranking_limit = 10
    plugin._render_font = ""
    plugin._render_rows = 12

    async def run():
        if method_name == "_cmd_rank":
            assert [item async for item in plugin._cmd_rank(Event())] == [
                ("plain", "请在群聊中查询本群排行。")
            ]
        else:
            assert await plugin._tool_rank(Event()) == "请在群聊中查询本群排行。"

    asyncio.run(run())
