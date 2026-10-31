"""公开开发基线的事务失败、衰减下限和排行一致性回归测试。"""

import asyncio
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_bond.core.levels import LevelTable  # noqa: E402
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


def test_ranking_retains_half_life_and_selects_effective_top(backend, monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    svc = service(backend, decay_enabled=True)
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
    assert ranking[0].favor == individual.favor == 71.3


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
