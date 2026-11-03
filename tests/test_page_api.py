"""成员、流水与配置的离线 API 合同，不启动宿主或 HTTP 服务。"""

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from astrbot_plugin_bond.core.settings import SettingsRevisionError


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


@pytest.fixture
def configuration_api(page_module, monkeypatch):
    import copy

    from astrbot_plugin_bond.services.runtime_service import RuntimeService
    from astrbot_plugin_bond.services.assessment_service import (
        AssessmentConfig,
        AssessmentCapacity,
    )

    class Config(dict):
        fail = False

        def save_config(self):
            if self.fail:
                raise OSError("disk full")
            self.saved = copy.deepcopy(dict(self))

    config = Config()
    active = SimpleNamespace(config=AssessmentConfig(), capacity=AssessmentCapacity(2))
    runtime = RuntimeService(SimpleNamespace(known_bot_ids=lambda: []), config, None)
    runtime.select = AsyncMock(return_value=SimpleNamespace(assessment=active))
    page = page_module.PageApi(runtime)
    params = {}
    monkeypatch.setattr(page, "_params", AsyncMock(return_value=params))
    request = SimpleNamespace(method="GET")
    monkeypatch.setattr(page_module, "request", request)
    context = SimpleNamespace(get_all_providers=Mock(return_value=[]))
    page.register(context)
    return SimpleNamespace(
        page=page,
        runtime=runtime,
        config=config,
        params=params,
        request=request,
        context=context,
        active=active,
    )


def test_models_saved_concurrency_does_not_replace_active_limit(configuration_api):
    api = configuration_api
    api.runtime._observed_bot_ids.add("10001")
    api.params.update(bot_id="10001", scope_mode="per_group")

    async def run():
        before, status = await api.page.handle_models()
        assert status == 200 and before["active_max_concurrent_assessments"] == 2
        assert before["revision"] == api.runtime.detailed_settings()["revision"]
        api.request.method = "POST"
        api.params.update(
            config={
                "enabled": True,
                "small_provider_id": "daily",
                "assessment_mode": "single",
            },
            max_concurrent_assessments=5,
            revision=before["revision"],
        )
        saved, status = await api.page.handle_models()
        assert status == 200 and saved["restart_required"]
        assert saved["revision"] != before["revision"]
        api.request.method = "GET"
        after, status = await api.page.handle_models()
        assert status == 200
        assert after["configured"]["enabled"] is True
        assert after["configured"]["assessment_mode"] == "single"
        assert after["active"]["enabled"] is False
        assert after["max_concurrent_assessments"] == 5
        assert (
            after["active_max_concurrent_assessments"] == api.active.capacity.limit == 2
        )
        assert after["revision"] == saved["revision"]

    asyncio.run(run())


def test_settings_without_bot_read_and_save_public_provider_metadata(configuration_api):
    api = configuration_api
    provider = SimpleNamespace(
        meta=Mock(
            return_value=SimpleNamespace(
                id="daily", model="example-model", key="private"
            )
        )
    )
    api.context.get_all_providers.return_value = [provider]

    async def run():
        before, status = await api.page.handle_settings()
        assert status == 200
        assert before["providers"] == [{"id": "daily", "model": "example-model"}]
        assert before["restart_required"] is False
        assert before["values"]["assessment.enabled"] is False
        api.request.method = "POST"
        api.params.update(
            values={
                "assessment.enabled": True,
                "assessment.small_provider_id": "daily",
                "inject.enabled": True,
                "ignored_bot_ids": ["20002"],
            },
            revision=before["revision"],
        )
        after, status = await api.page.handle_settings()
        assert status == 200 and after["restart_required"]
        assert after["values"]["assessment.enabled"] is True
        assert after["values"]["ignored_bot_ids"] == ["20002"]
        assert after["revision"] == api.runtime.detailed_settings()["revision"]
        assert after["revision"] != before["revision"]
        assert api.config.saved == api.config
        api.runtime.select.assert_not_awaited()

    asyncio.run(run())


def test_settings_preserve_unavailable_provider_with_empty_list(configuration_api):
    api = configuration_api
    api.config["assessment"] = {"enabled": True, "small_provider_id": "unavailable"}
    payload, status = asyncio.run(api.page.handle_settings())
    assert status == 200 and payload["providers"] == []
    assert payload["values"]["assessment.small_provider_id"] == "unavailable"
    api.runtime.select.assert_not_awaited()


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("endpoint", ["settings", "models"])
def test_configuration_requires_host_login(
    page_module, monkeypatch, configuration_api, method, endpoint
):
    api = configuration_api
    api.request.method = method
    monkeypatch.setattr(page_module, "g", SimpleNamespace())
    payload, status = asyncio.run(getattr(api.page, "handle_" + endpoint)())
    assert status == 401 and not payload["success"]
    api.page._params.assert_not_awaited()
    api.context.get_all_providers.assert_not_called()
    api.runtime.select.assert_not_awaited()
    assert api.config == {}


@pytest.mark.parametrize("endpoint", ["settings", "models"])
@pytest.mark.parametrize("revision", [None, "stale"])
def test_configuration_save_rejects_missing_or_stale_revision(
    configuration_api, endpoint, revision
):
    api = configuration_api
    api.runtime._observed_bot_ids.add("10001")
    api.request.method = "POST"
    api.params.update(
        bot_id="10001",
        scope_mode="global",
        values={"inject.enabled": True},
        config={"cooldown_sec": 90},
        max_concurrent_assessments=5,
    )
    if revision is not None:
        api.params["revision"] = revision
    payload, status = asyncio.run(getattr(api.page, "handle_" + endpoint)())
    assert status == 409
    assert payload == {"success": False, "error": "设置已变更，请刷新后重试"}
    assert api.config == {}
    assert not hasattr(api.config, "saved")


@pytest.mark.parametrize("endpoint", ["settings", "models"])
@pytest.mark.parametrize("error_type", [SettingsRevisionError, ValueError])
def test_configuration_errors_use_fixed_messages(
    configuration_api, endpoint, error_type
):
    api = configuration_api
    api.runtime._observed_bot_ids.add("10001")
    api.request.method = "POST"
    api.params.update(bot_id="10001", scope_mode="per_group")
    method = (
        "save_detailed_settings"
        if endpoint == "settings"
        else "save_assessment_settings"
    )
    setattr(api.runtime, method, AsyncMock(side_effect=error_type("private details")))
    payload, status = asyncio.run(getattr(api.page, "handle_" + endpoint)())
    if error_type is SettingsRevisionError:
        expected_status, expected_error = 409, "设置已变更，请刷新后重试"
    else:
        expected_status = 400
        expected_error = (
            "设置无效或已被修改，请刷新页面并检查输入"
            if endpoint == "settings"
            else "请求参数无效"
        )
    assert status == expected_status
    assert payload == {"success": False, "error": expected_error}


@pytest.mark.parametrize("endpoint", ["settings", "models"])
def test_configuration_validation_and_persistence_errors(configuration_api, endpoint):
    api = configuration_api
    api.runtime._observed_bot_ids.add("10001")
    api.request.method = "POST"
    api.params.update(
        bot_id="10001",
        scope_mode="per_group",
        values={"assessment.enabled": True},
        config={"enabled": True},
        max_concurrent_assessments=5,
        revision=api.runtime.detailed_settings()["revision"],
    )

    async def run():
        handler = getattr(api.page, "handle_" + endpoint)
        payload, status = await handler()
        assert status == 400 and not payload["success"]
        assert api.config == {}
        api.params["values"]["assessment.small_provider_id"] = "daily"
        api.params["config"]["small_provider_id"] = "daily"
        api.config.fail = True
        payload, status = await handler()
        assert status == 500 and not payload["success"]
        assert api.config == {}
        assert api.runtime.detailed_settings()["revision"] == api.params["revision"]

    asyncio.run(run())


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_models_still_require_valid_bot_scope(configuration_api, method):
    api = configuration_api
    api.request.method = method
    payload, status = asyncio.run(api.page.handle_models())
    assert status == 400 and not payload["success"]
    api.runtime.select.assert_not_awaited()
