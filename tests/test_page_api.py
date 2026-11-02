"""成员分页与流水维度的离线 API 合同，不启动宿主或 HTTP 服务。"""

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def page_module(monkeypatch):
    api = ModuleType("astrbot.api")
    api.logger = logging.getLogger("bond-page-test")
    monkeypatch.setitem(sys.modules, "astrbot.api", api)
    spec = importlib.util.spec_from_file_location(
        "astrbot_plugin_bond.api.page_api",
        Path(__file__).parents[1] / "api/page_api.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "jsonify", None)
    monkeypatch.setattr(module, "g", SimpleNamespace(username="fixture-admin"))
    return module


def make_api(module, monkeypatch, params, favor):
    runtime = SimpleNamespace(
        known_bot_ids=lambda: ["10001"],
        select=AsyncMock(return_value=SimpleNamespace(favor=favor)),
    )
    page = module.PageApi(runtime)

    async def get_params():
        return {"bot_id": "10001", "scope_mode": "per_group", **params}

    monkeypatch.setattr(page, "_params", get_params)
    return page, runtime


def test_users_defaults_and_page_metadata(page_module, monkeypatch):
    result = dict(
        users=[{"user_id": "8001"}],
        count=1,
        total=81,
        matched_count=90,
        collapsed_count=9,
        zero_count=9,
        page=2,
        page_size=50,
        pages=2,
    )
    favor = SimpleNamespace(standings_page=AsyncMock(return_value=result))
    page, _ = make_api(page_module, monkeypatch, {"page": "2"}, favor)
    payload, status = asyncio.run(page.handle_users())
    assert status == 200 and payload == {"success": True, **result}
    favor.standings_page.assert_awaited_once_with(
        None, page=2, page_size=50, search="", include_zero=False
    )


@pytest.mark.parametrize(
    "params",
    [
        {"page": "0"},
        {"page": -1},
        {"page": True},
        {"page_size": "31"},
        {"page_size": 50.0},
        {"include_zero": "yes"},
        {"include_zero": 1},
        {"search": "abc"},
        {"search": "１"},
        {"search": "1" * 33},
    ],
)
def test_users_invalid_arguments(page_module, monkeypatch, params):
    favor = SimpleNamespace(standings_page=AsyncMock())
    page, _ = make_api(page_module, monkeypatch, params, favor)
    payload, status = asyncio.run(page.handle_users())
    assert status == 400 and not payload["success"]
    favor.standings_page.assert_not_awaited()


def test_users_expansion_search_and_scope_forwarded(page_module, monkeypatch):
    favor = SimpleNamespace(standings_page=AsyncMock(return_value={"users": []}))
    page, runtime = make_api(
        page_module,
        monkeypatch,
        {
            "scope_mode": "global",
            "group_id": "123",
            "search": "008",
            "page_size": "200",
            "include_zero": "1",
        },
        favor,
    )
    assert asyncio.run(page.handle_users())[1] == 200
    runtime.select.assert_awaited_once_with("10001", "global")
    favor.standings_page.assert_awaited_once_with(
        "123", page=1, page_size=200, search="008", include_zero=True
    )


def test_users_require_host_authentication(page_module, monkeypatch):
    favor = SimpleNamespace(standings_page=AsyncMock())
    page, runtime = make_api(page_module, monkeypatch, {}, favor)
    monkeypatch.setattr(page_module, "g", SimpleNamespace())
    assert asyncio.run(page.handle_users())[1] == 401
    runtime.select.assert_not_awaited()


def test_logs_forward_dimension_source_and_limit(page_module, monkeypatch):
    record = {"delta": 0, "relation_delta": {"familiarity": 1}}
    favor = SimpleNamespace(query_logs=AsyncMock(return_value=[record]))
    page, _ = make_api(
        page_module,
        monkeypatch,
        {
            "dimension": "familiarity",
            "source": "judge",
            "limit": "30",
            "group_id": "123",
        },
        favor,
    )
    payload, status = asyncio.run(page.handle_logs())
    assert status == 200 and payload["logs"] == [record]
    favor.query_logs.assert_awaited_once_with(
        "123", None, 30, 0, fuzzy=False, dimension="familiarity", source="judge"
    )


@pytest.mark.parametrize(
    "params", [{"dimension": "F"}, {"source": "admin"}, {"dimension": ["trust"]}]
)
def test_logs_reject_invalid_filters(page_module, monkeypatch, params):
    favor = SimpleNamespace(query_logs=AsyncMock())
    page, _ = make_api(page_module, monkeypatch, params, favor)
    assert asyncio.run(page.handle_logs())[1] == 400
    favor.query_logs.assert_not_awaited()


def test_models_saved_concurrency_does_not_replace_active_limit(
    page_module, monkeypatch
):
    from dataclasses import asdict
    from astrbot_plugin_bond.services.assessment_service import (
        AssessmentConfig,
        AssessmentCapacity,
    )

    configured = {"limit": 2}
    active = SimpleNamespace(config=AssessmentConfig(), capacity=AssessmentCapacity(2))
    runtime = SimpleNamespace(
        known_bot_ids=lambda: ["10001"],
        select=AsyncMock(return_value=SimpleNamespace(assessment=active)),
        assessment_settings=lambda: (asdict(AssessmentConfig()), configured["limit"]),
    )

    async def save(values, limit):
        configured["limit"] = limit

    runtime.save_assessment_settings = save
    page = page_module.PageApi(runtime)

    async def params():
        return {
            "bot_id": "10001",
            "scope_mode": "per_group",
            "config": {},
            "max_concurrent_assessments": 5,
        }

    monkeypatch.setattr(page, "_params", params)
    request = SimpleNamespace(method="GET")
    monkeypatch.setattr(page_module, "request", request)

    async def run():
        before, status = await page.handle_models()
        assert status == 200 and before["active_max_concurrent_assessments"] == 2
        request.method = "POST"
        assert (await page.handle_models())[0]["restart_required"]
        request.method = "GET"
        after, status = await page.handle_models()
        assert status == 200
        assert after["max_concurrent_assessments"] == 5
        assert after["active_max_concurrent_assessments"] == active.capacity.limit == 2

    asyncio.run(run())
