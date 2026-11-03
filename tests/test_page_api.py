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


@pytest.fixture
def today_api(page_module, monkeypatch, tmp_path):
    from astrbot_plugin_bond.core.levels import LevelTable
    from astrbot_plugin_bond.services.assessment_service import (
        AssessmentCapacity,
        AssessmentConfig,
        AssessmentService,
    )
    from astrbot_plugin_bond.services.favor_service import FavorService
    from astrbot_plugin_bond.services.runtime_service import RuntimeService
    from astrbot_plugin_bond.storage.router import BotScopeStorageRouter

    router = BotScopeStorageRouter(tmp_path / "synthetic-summary")
    capacity = AssessmentCapacity(2)
    cfg = AssessmentConfig(per_bot_daily_small_cap=17, per_bot_daily_large_cap=7)

    def factory(store, mode):
        favor = FavorService(
            store,
            LevelTable.from_config(None),
            scope_mode=mode,
            familiarity_decay_enabled=True,
            tz_name="Asia/Shanghai",
        )

        async def budget_stores():
            return [
                await router.get(store.bot_id, space)
                for space in ("per_group", "global")
            ]

        return SimpleNamespace(
            favor=favor,
            assessment=AssessmentService(
                store, favor, None, cfg, capacity, budget_stores
            ),
        )

    runtime = RuntimeService(router, {}, factory)
    runtime._observed_bot_ids.update({"10001", "20002"})
    page = page_module.PageApi(runtime)
    params = dict(bot_id="10001", scope_mode="per_group")
    monkeypatch.setattr(page, "_params", AsyncMock(side_effect=lambda: dict(params)))
    yield SimpleNamespace(page=page, runtime=runtime, router=router, params=params)
    asyncio.run(router.close())


def test_today_summary_real_ledgers_dates_spaces_distinct_and_runtime_caps(today_api):
    from datetime import datetime, timezone
    from unittest.mock import patch

    now = datetime(2026, 11, 3, 16, 30, tzinfo=timezone.utc).timestamp()
    utc_start, local_start = now - 16.5 * 3600, now - 0.5 * 3600

    async def run():
        per = await today_api.runtime.select("10001", "per_group")
        glob = await today_api.runtime.select("10001", "global")
        other = await today_api.runtime.select("20002", "per_group")

        async def call(bundle, eid, tier, when, status):
            with patch("time.time", return_value=when):
                call_id = await bundle.assessment.storage.reserve_assessment_call(
                    eid, "300", "201", tier, "fixture", 100, 100, window_max_calls=100
                )
                assert call_id is not None
                if status != "pending":
                    await bundle.assessment.storage.finish_assessment_call(
                        call_id, status, None
                    )

        await call(per, "yesterday", "small", utc_start - 1, "ok")
        await call(per, "boundary", "small", utc_start, "ok")
        await call(per, "failed", "small", now, "failed")
        await call(glob, "pending", "small", now, "pending")
        await call(glob, "timeout", "large", now, "timeout")
        await call(other, "other-bot", "small", now, "ok")

        async def change(bundle, user, eid, when=now, group="300", **values):
            with patch("time.time", return_value=when):
                return await bundle.favor.set_relation(
                    group,
                    user,
                    event_id=eid,
                    **dict(dict(familiarity=0, affinity=0, trust=0), **values),
                )

        await change(per, "100", "before-local", when=local_start - 1, affinity=5)
        await change(per, "201", "local-boundary", when=local_start, familiarity=1)
        await change(per, "201", "same-user-other-group", group="400", familiarity=1)
        await change(per, "202", "trust-only", trust=-5)
        await change(per, "203", "affinity-only", affinity=-5)
        await change(per, "207", "zero")
        await change(per, "209", "old-decay", when=local_start - 86400, familiarity=40)
        await change(glob, "301", "global-1", familiarity=1)
        await change(glob, "301", "global-2", group="400", familiarity=2)
        await change(other, "401", "other-1", familiarity=5)
        with patch("time.time", return_value=now):
            await per.favor.observe_member("300", "205")
            await per.favor.apply_relation_event(
                "300", "204", "normal_question", event_id="question", cooldown_sec=0
            )
            await per.favor.apply_relation_event(
                "300", "206", "uncertain", event_id="neutral", cooldown_sec=0
            )
            await per.favor.set_temporary(
                "300",
                "208",
                dict(familiarity=99, affinity=99, trust=99),
                3600,
                "fixture-admin",
            )
            await per.favor.get("300", "209")
            original = next(
                row
                for row in await per.assessment.storage.query_logs("300", "203")
                if row.get("event_id") == "affinity-only"
            )
            await per.favor.undo_log(original["id"])
            payload, status = await today_api.page.handle_today_summary()
            assert status == 200
            assert payload["summary"] == dict(
                initial=dict(used=3, limit=17),
                review=dict(used=1, limit=7),
                changed_members=4,
                call_day="2026-11-03",
                relation_day="2026-11-04",
                relation_timezone="Asia/Shanghai",
                call_reset_at=utc_start + 86400,
                relation_reset_at=local_start + 86400,
            )
            today_api.params.update(group_id="400", search="99999", page="2")
            assert (await today_api.page.handle_today_summary())[0] == payload
            today_api.params["scope_mode"] = "global"
            global_payload, status = await today_api.page.handle_today_summary()
            assert status == 200 and global_payload["summary"]["changed_members"] == 1
            assert global_payload["summary"]["initial"] == dict(used=3, limit=17)
            today_api.params.update(bot_id="20002", scope_mode="per_group")
            other_payload, _ = await today_api.page.handle_today_summary()
            assert other_payload["summary"]["initial"]["used"] == 1
            assert other_payload["summary"]["changed_members"] == 1
            # 预算重置按 UTC；本地关系日尚未改变。
            with patch("time.time", return_value=utc_start + 86400):
                midnight, _ = await today_api.page.handle_today_summary()
                assert midnight["summary"]["initial"]["used"] == 0
                assert midnight["summary"]["changed_members"] == 1
            with patch("time.time", return_value=local_start + 86400):
                local_midnight, _ = await today_api.page.handle_today_summary()
                assert local_midnight["summary"]["changed_members"] == 0
                assert local_midnight["summary"]["relation_day"] == "2026-11-05"
            today_api.params.update(bot_id="10001", scope_mode="per_group")
            # 重开连接后统计仍取持久账本，不依赖进程缓存。
            for store in await per.assessment.budget_stores():
                await store.close()
                await store.init()
            today_api.runtime._config["assessment"] = dict(per_bot_daily_small_cap=999)
            assert (await today_api.page.handle_today_summary())[0] == payload

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["calls", "relations"])
def test_today_summary_statistics_failure_is_not_zero(
    page_module, monkeypatch, failure
):
    assessment = SimpleNamespace(
        today_usage=AsyncMock(
            return_value=dict(
                initial=dict(used=1, cap=17),
                review=dict(used=0, cap=7),
                day="2026-11-03",
                reset_at=123,
            )
        )
    )
    favor = SimpleNamespace(
        today_changes=AsyncMock(
            return_value=dict(
                changed_members=1, day="2026-11-03", timezone="UTC", reset_at=123
            )
        )
    )
    if failure == "calls":
        assessment.today_usage.side_effect = OSError("fixture storage failure")
    else:
        favor.today_changes.side_effect = OSError("fixture storage failure")
    page, runtime = make_api(page_module, monkeypatch, {}, favor)
    runtime.select.return_value = SimpleNamespace(assessment=assessment, favor=favor)
    payload, status = asyncio.run(page.handle_today_summary())
    assert status == 500 and payload == dict(
        success=False, error=page_module.ERROR_OPERATION
    )


def test_today_summary_requires_host_auth_and_valid_bot(page_module, monkeypatch):
    page, runtime = make_api(page_module, monkeypatch, {}, SimpleNamespace())
    monkeypatch.setattr(page_module, "g", SimpleNamespace())
    assert asyncio.run(page.handle_today_summary())[1] == 401
    runtime.select.assert_not_awaited()
    monkeypatch.setattr(page_module, "g", SimpleNamespace(username="fixture-admin"))
    monkeypatch.setattr(
        page,
        "_params",
        AsyncMock(return_value=dict(bot_id="99999", scope_mode="global")),
    )
    assert asyncio.run(page.handle_today_summary())[1] == 400
    runtime.select.assert_not_awaited()


def test_today_summary_queries_use_existing_day_and_group_time_indexes(today_api):
    async def run():
        deps = await today_api.runtime.select("10001", "per_group")
        await deps.favor.set_favor("300", "201", 1)
        store = deps.assessment.storage
        statements = []
        store._c().set_trace_callback(statements.append)
        await store.changed_members_count(0, 2_000_000_000)
        await store.assessment_day_counts("2026-11-03")
        store._c().set_trace_callback(None)
        queries = [query for query in statements if query.startswith("SELECT")]
        assert len(queries) == 2
        plans = [
            " ".join(
                row[3] for row in store._c().execute("EXPLAIN QUERY PLAN " + query)
            )
            for query in queries
        ]
        assert (
            "SEARCH logs USING INDEX idx_favor_log_group_ts (group_id=? AND ts>? AND ts<?)"
            in plans[0]
        )
        assert "SCAN logs" not in plans[0]
        assert "idx_assessment_budget (day=?)" in plans[1]

    asyncio.run(run())


@pytest.mark.parametrize("callback", ["none", "empty"])
def test_today_summary_optional_budget_stores_still_counts_current_store(
    today_api, callback
):
    from unittest.mock import patch

    async def run():
        with patch("time.time", return_value=1793700000):
            deps = await today_api.runtime.select("10001", "per_group")
            call_id = await deps.assessment.storage.begin_assessment_call(
                "fixture", "300", "201", "small", "fixture", 100, 100
            )
            assert call_id is not None
            deps.assessment.budget_stores = (
                None if callback == "none" else AsyncMock(return_value=[])
            )
            payload, status = await today_api.page.handle_today_summary()
            assert status == 200 and payload["summary"]["initial"] == dict(
                used=1, limit=17
            )

    asyncio.run(run())


def test_today_summary_reset_removes_records_and_logs_together(today_api):
    async def run():
        deps = await today_api.runtime.select("10001", "per_group")
        await deps.favor.set_favor("300", "201", 1)
        await deps.favor.set_favor("400", "201", 1)
        assert (await deps.favor.today_changes())["changed_members"] == 1
        await deps.favor.reset("300", "201")
        assert (await deps.favor.today_changes())["changed_members"] == 1
        await deps.favor.reset("400", "201")
        assert (await deps.favor.today_changes())["changed_members"] == 0
        assert await deps.assessment.storage.query_logs() == []

    asyncio.run(run())


def test_today_summary_invalid_json_reports_failure_not_zero(today_api):
    import time

    async def run():
        deps = await today_api.runtime.select("10001", "per_group")
        await deps.favor.observe_member("300", "201")
        conn = deps.assessment.storage._c()
        with conn:
            conn.execute(
                "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,ts,relation_delta) VALUES('300','201',0,0,0,?,'{')",
                (time.time(),),
            )
        payload, status = await today_api.page.handle_today_summary()
        assert status == 500 and payload == dict(success=False, error="操作失败")

    asyncio.run(run())
