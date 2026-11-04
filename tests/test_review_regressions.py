"""公开开发基线的事务失败、衰减下限和排行一致性回归测试。"""

import asyncio
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_bond.core.levels import LevelTable  # noqa: E402
from astrbot_plugin_bond.core.taskregistry import TaskRegistry
from astrbot_plugin_bond.services.favor_service import FavorService  # noqa: E402
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend  # noqa: E402


@pytest.fixture
def backend(tmp_path):
    storage = SQLiteBackend(tmp_path / "test.db")
    asyncio.run(storage.init())
    yield storage
    asyncio.run(storage.close())


def service(backend, **kwargs):
    return FavorService(
        backend,
        LevelTable.from_config(None),
        daily_cap_up=100,
        daily_cap_down=100,
        **kwargs,
    )


@pytest.mark.parametrize("existing", [False, True])
def test_admin_log_failure_rolls_back_score(backend, existing):
    svc = service(backend)
    if existing:
        asyncio.run(backend.set_value("group", "member", 10))
    backend._c().execute(
        "CREATE TRIGGER reject_log BEFORE INSERT ON favor_log "
        "BEGIN SELECT RAISE(ABORT, 'fixture log failure'); END"
    )
    backend._c().commit()
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(svc.set_favor("group", "member", 70))
    record = asyncio.run(backend.get("group", "member"))
    assert (record.favor if record else None) == (10 if existing else None)
    assert not asyncio.run(backend.query_logs("group", "member"))
    # 失败后连接仍可提交下一次完整事务。
    backend._c().execute("DROP TRIGGER reject_log")
    backend._c().commit()
    asyncio.run(svc.set_favor("group", "member", 20))
    assert asyncio.run(backend.get("group", "member")).favor == 20
    assert len(asyncio.run(backend.query_logs("group", "member"))) == 1


@pytest.mark.parametrize("floor_enabled, expected", [(True, 56), (False, 1.1)])
def test_write_uses_same_decay_baseline_as_read(
    backend, monkeypatch, floor_enabled, expected
):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    svc = service(backend, decay_enabled=True, decay_floor_enabled=floor_enabled)
    asyncio.run(backend.set_value("group", "member", 95))
    backend._c().execute(
        "UPDATE favor SET updated_at=? WHERE group_id=? AND user_id=?",
        (now - 100 * 86400, "group", "member"),
    )
    backend._c().commit()
    before = asyncio.run(svc.get("group", "member")).favor
    result = asyncio.run(svc.change("group", "member", 1))
    assert result.favor_after == expected
    logs = asyncio.run(backend.query_logs("group", "member"))
    assert logs[0]["favor_before"] == before
    assert logs[0]["delta"] == 1


@pytest.mark.parametrize("grace_days,expected", [(0, 71.3), (5, 75.5)])
def test_ranking_retains_half_life_and_selects_effective_top(
    backend, monkeypatch, grace_days, expected
):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    svc = service(backend, decay_enabled=True, decay_grace_days=grace_days)
    for member, score, half_life in (("slow", 80, 60), ("fast", 90, 10)):
        asyncio.run(backend.set_value("group", member, score))
        backend._c().execute(
            "UPDATE favor SET updated_at=?, half_life=? WHERE group_id=? AND user_id=?",
            (now - 10 * 86400, half_life, "group", member),
        )
    backend._c().commit()
    records = asyncio.run(backend.ranking("group", 2))
    assert {r.user_id: r.half_life for r in records} == {"slow": 60, "fast": 10}
    ranking = asyncio.run(svc.ranking("group", 1))
    individual = asyncio.run(svc.get("group", "slow"))
    assert ranking[0].user_id == "slow"
    assert ranking[0].favor == individual.favor == expected


def test_admin_log_uses_effective_score_and_preserves_profile(backend, monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    svc = service(backend, decay_enabled=True, decay_floor_enabled=True)
    asyncio.run(backend.set_value("group", "member", 95))
    asyncio.run(backend.set_nickname("group", "member", "测试成员"))
    backend._c().execute(
        "UPDATE favor SET updated_at=?, half_life=? WHERE group_id=? AND user_id=?",
        (now - 100 * 86400, 20, "group", "member"),
    )
    backend._c().commit()
    asyncio.run(svc.set_favor("group", "member", 70, reason="x" * 201))
    logs = asyncio.run(backend.query_logs("group", "member"))
    assert (logs[0]["favor_before"], logs[0]["favor_after"], logs[0]["delta"]) == (
        55,
        70,
        15,
    )
    assert len(logs[0]["reason"]) == 200
    stored = asyncio.run(backend.get("group", "member"))
    assert stored.half_life == 20 and stored.nickname == "测试成员"
    assert asyncio.run(svc.undo_log(logs[0]["id"])).favor == 55


@pytest.mark.parametrize("decay_enabled", [False, True])
def test_standings_applies_limit_after_effective_sort(
    backend, monkeypatch, decay_enabled
):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    svc = service(backend, decay_enabled=decay_enabled)
    asyncio.run(backend.set_value("group", "older_high", 99))
    backend._c().execute(
        "UPDATE favor SET updated_at=?, half_life=? WHERE group_id=? AND user_id=?",
        (now - 10 * 86400, 60, "group", "older_high"),
    )
    backend._c().commit()
    asyncio.run(backend.set_value("group", "new_low", 1))
    asyncio.run(backend.set_value("other_group", "outsider", 100))
    result = asyncio.run(svc.standings("group", limit=1))
    assert len(result) == 1 and result[0]["user_id"] == "older_high"
    assert result[0]["favor"] == asyncio.run(svc.get("group", "older_high")).favor


@pytest.mark.parametrize("delta,current,expected", [(-10, 99, 100), (10, -99, -100)])
def test_undo_preview_matches_clamped_result(backend, delta, current, expected):
    svc = service(backend)
    asyncio.run(svc.change("g", "u", delta))
    log = asyncio.run(backend.query_logs("g", "u"))[0]
    asyncio.run(svc.set_favor("g", "u", current))
    assert asyncio.run(svc.undo_preview(log["id"]))["after"] == expected
    assert asyncio.run(svc.undo_log(log["id"])).favor == expected


def test_impression_counts_new_evaluations_and_survives_restart(backend):
    from types import SimpleNamespace
    from astrbot_plugin_bond.services.impression_service import ImpressionService

    class Judge:
        calls = 0

        async def resolve_display_name(self, umo):
            return "测试"

        async def resolve_summary_provider(self):
            return self

        async def text_chat(self, **kw):
            self.calls += 1
            return SimpleNamespace(completion_text="印象:测试印象\n标签:无")

    async def run():
        judge = Judge()
        svc = ImpressionService(
            backend, registry=TaskRegistry(), summarizer=judge, interval=8
        )
        for i in range(16):
            await backend.add_log("g", "u", 1, 0, 1, "理由", "judge", i, message="样本")
            await svc.maybe_refresh("g", "u")
            if svc._registry._tasks:
                await asyncio.gather(*svc._registry._tasks)
            if i == 7:
                svc = ImpressionService(
                    backend, registry=TaskRegistry(), summarizer=judge, interval=8
                )
        assert judge.calls == 2
        assert await backend.impression_logs("g", "u", 20) == []

    asyncio.run(run())


@pytest.mark.parametrize("points_mode", [False, True])
def test_reversed_impression_evidence_never_reaches_model(backend, points_mode):
    from types import SimpleNamespace
    from astrbot_plugin_bond.services.impression_service import ImpressionService
    from astrbot_plugin_bond.core.impression import stats_tags

    class Judge:
        async def resolve_display_name(self, umo):
            return "测试"

        async def resolve_summary_provider(self):
            return self

        async def text_chat(self, prompt):
            assert "已撤销内容" not in prompt
            assert "有效内容" in prompt
            return SimpleNamespace(
                completion_text=(
                    '[{"point":"友善","weight":3}]'
                    if points_mode
                    else "印象:友善\n标签:无"
                )
            )

    async def run():
        await backend.add_log(
            "g", "u", 1, 0, 1, "理由", "judge", 1, message="已撤销内容"
        )
        row = (await backend.query_logs("g", "u"))[0]
        await service(backend).undo_log(row["id"])
        await backend.add_log("g", "u", 1, 0, 1, "理由", "judge", 2, message="有效内容")
        ok, _ = await ImpressionService(
            backend,
            registry=TaskRegistry(),
            summarizer=Judge(),
            points_mode=points_mode,
        ).refresh_now("g", "u")
        assert ok
        assert stats_tags([dict(source="judge", delta=-1, reversed=1)] * 10) == []

    asyncio.run(run())


def test_impression_commit_rejects_concurrent_undo_and_retries(backend):
    async def run():
        await backend.add_log("g", "u", 1, 0, 1, "r", "judge", 1)
        row = (await backend.impression_logs("g", "u", 20))[0]
        await service(backend).undo_log(row["id"])
        assert not await backend.commit_impression(
            "g", "u", "错误印象", [], [row["id"]]
        )
        assert (await backend.get("g", "u")).impression == ""
        assert (
            backend._c()
            .execute("SELECT COUNT(*) FROM impression_progress")
            .fetchone()[0]
            == 0
        )

    asyncio.run(run())


def test_new_and_merged_impression_points_keep_latest_timestamp():
    from astrbot_plugin_bond.core.impression_points import merge_points

    assert merge_points([], [dict(point="同一事实", weight=2, ts=100)])[0]["ts"] == 100
    merged = merge_points(
        [dict(point="同一事实", weight=2, ts=100)],
        [dict(point="同一事实", weight=2, ts=200)],
    )
    assert merged[0]["ts"] == 200 and merged[0]["weight"] == 4
    assert (
        merge_points(merged, [dict(point="同一事实", weight=1, ts=50)])[0]["ts"] == 200
    )


@pytest.mark.parametrize("grace_days,expected", [(0, 40), (5, 56.6)])
def test_exhausted_allowance_returns_effective_score_without_writes(
    backend, monkeypatch, grace_days, expected
):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    svc = service(backend, decay_enabled=True, decay_grace_days=grace_days)
    asyncio.run(backend.set_value("g", "u", 80))
    backend._c().execute("UPDATE favor SET updated_at=?", (now - 10 * 86400,))
    backend._c().commit()
    asyncio.run(backend.add_daily_gain("g", "u", svc._today().isoformat(), 100))
    before = asyncio.run(svc.get("g", "u"))
    stored = backend._c().execute("SELECT favor,updated_at FROM favor").fetchone()
    result = asyncio.run(svc.change("g", "u", 1))
    assert (
        result.delta == 0
        and result.favor_after == result.favor_before == before.favor == expected
    )
    assert (
        backend._c().execute("SELECT favor,updated_at FROM favor").fetchone() == stored
    )
    assert asyncio.run(svc.query_logs("g", "u")) == []


@pytest.mark.parametrize("path", ["change", "nickname", "admin", "impression"])
def test_new_member_uses_configured_half_life(backend, path):
    svc = service(backend, decay_enabled=True, half_life_base=5)

    async def run():
        if path == "nickname":
            await svc.touch_nickname("g", "u", "测试")
        if path == "admin":
            await svc.set_favor("g", "u", 1)
        if path == "impression":
            await backend.set_impression("g", "u", "测试", [])
        result = await svc.change("g", "u", 1)
        assert result.delta == 1
        assert (await backend.get("g", "u")).half_life == 6.5

    asyncio.run(run())


def test_deep_relationship_uses_scaled_repair_factor(backend):
    from astrbot_plugin_bond.core.level_economy import (
        EconomyConfig,
        repair_params_for_level,
    )

    cfg = EconomyConfig(
        noise_floor=0, level_mult={}, same_day_decay=0, repair_threshold=2
    )
    svc = service(backend, economy=cfg)

    async def run():
        await svc.set_favor("g", "u", 70)
        await svc.apply_judge("g", "u", -3)
        expected = repair_params_for_level("亲密", cfg)[1]
        assert await svc._repair_factor("g", "u") == expected
        change = await svc.apply_judge("g", "u", 2)
        assert change.delta == round(2 * expected, 1)

    asyncio.run(run())


def test_tags_preserve_configured_initial_score(backend):
    from astrbot_plugin_bond.services.impression_service import ImpressionService

    svc = service(backend, default_favor=10)

    async def run():
        assert (await svc.get("g", "u")).favor == 10
        await ImpressionService(backend, registry=TaskRegistry()).set_tags(
            "g", "u", ["友善"]
        )
        assert (await svc.get("g", "u")).favor == 10
        await svc.set_favor("g", "u", 30)
        await ImpressionService(backend, registry=TaskRegistry()).set_tags(
            "g", "u", ["熟人"]
        )
        assert (await svc.get("g", "u")).favor == 30

    asyncio.run(run())


def test_reversed_positive_judgment_does_not_reduce_next_gain(backend):
    from astrbot_plugin_bond.core.level_economy import EconomyConfig

    svc = service(backend, economy=EconomyConfig(noise_floor=0, level_mult={}))

    async def run():
        await svc.apply_judge("g", "u", 2)
        log = (await svc.query_logs("g", "u"))[0]
        await svc.undo_log(log["id"])
        assert (await svc.apply_judge("g", "u", 2)).delta == 2

    asyncio.run(run())


@pytest.mark.parametrize("group_id", ["", None])
def test_private_ranking_never_queries_group_data(monkeypatch, group_id):
    from types import ModuleType, SimpleNamespace
    from unittest.mock import AsyncMock

    event_module = ModuleType("astrbot.api.event")
    event_module.AstrMessageEvent = object
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    from astrbot_plugin_bond.api import commands, llm_tools

    svc = SimpleNamespace(standings=AsyncMock(), ranking=AsyncMock())
    event = SimpleNamespace(get_group_id=lambda: group_id)

    async def run():
        assert await commands.rank_reply(svc, event) == (
            "text",
            "请在群聊中查询本群排行。",
        )
        assert (
            await llm_tools.tool_query_ranking(svc, event) == "请在群聊中查询本群排行。"
        )
        svc.standings.assert_not_awaited()
        svc.ranking.assert_not_awaited()

    asyncio.run(run())
