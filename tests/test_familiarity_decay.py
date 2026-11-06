"""F 独立衰减：真实服务/SQLite 合成回归，不评估真实模型语义。"""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_profiles import build_relation_profile
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.migrations import MIGRATIONS, SCHEMA_VERSION, migrate
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

START = datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp()
DAY = 86400


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def db(tmp_path):
    store = SQLiteBackend(tmp_path / "synthetic.db")
    run(store.init())
    yield store
    run(store.close())


def service(db, **kwargs):
    # 此套回归固定旧维护语义；独立策略另由 test_independent_maintenance 覆盖。
    kwargs.setdefault("maintenance_strategy", "legacy")
    policy, caps = build_relation_profile(
        "realistic", rules=kwargs.pop("rules", None), caps=kwargs.pop("caps", None)
    )
    options = dict(
        decay_grace_days=0,
        familiarity_decay_enabled=True,
        familiarity_half_life=60,
        trust_decay_enabled=True,
        trust_half_life=180,
    )
    options.update(kwargs)
    return FavorService(
        db,
        LevelTable.from_config(None),
        relation_policy=policy,
        relation_caps=caps,
        **options,
    )


def seed(svc, f=40, a=0, t=40, eid="seed"):
    return run(
        svc.set_relation("g", "u", familiarity=f, affinity=a, trust=t, event_id=eid)
    )


def event(svc, eid, kind="normal_question"):
    return run(svc.apply_relation_event("g", "u", kind, event_id=eid, cooldown_sec=0))


def raw(db):
    return run(db.get("g", "u"))


def log(db, eid):
    return next(x for x in run(db.query_logs("g", "u")) if x.get("event_id") == eid)


def usage(db):
    return (
        db._c()
        .execute(
            "SELECT familiarity_up,familiarity_down,affinity_up,affinity_down,trust_up,trust_down FROM daily_gain ORDER BY day"
        )
        .fetchall()
    )


@pytest.mark.parametrize("days,expected", [(60, 20), (120, 10)])
def test_f40_fixed_half_life_independent_of_affinity_floor(db, days, expected):
    svc = service(
        db,
        decay_enabled=True,
        half_life_base=60,
        half_life_growth=3,
        decay_floor_enabled=True,
    )
    with patch("time.time", return_value=START):
        seed(svc, a=95)
    with patch("time.time", return_value=START + days * DAY):
        assert run(svc.get("g", "u")).familiarity == expected
        assert raw(db).familiarity == 40
        assert raw(db).familiarity_updated_at == START


def test_config_reload_reuses_original_anchor_without_writes(db):
    with patch("time.time", return_value=START):
        seed(service(db))
    with patch("time.time", return_value=START + 60 * DAY):
        assert run(service(db).get("g", "u")).familiarity == 20
        assert (
            run(service(db, familiarity_half_life=120).get("g", "u")).familiarity
            == 28.3
        )
        assert (
            run(service(db, familiarity_decay_enabled=False).get("g", "u")).familiarity
            == 40
        )
        assert raw(db).familiarity_updated_at == START
        assert len(run(db.query_logs("g", "u"))) == 1


def test_tonly_fonly_interleave_settle_all_dimensions_without_charging_decay(db):
    svc = service(db, rules={"insult": {"affinity": 0, "trust": -1.5}})
    with patch("time.time", return_value=START):
        seed(svc)
    with patch("time.time", return_value=START + 17 * DAY + 13):
        before = svc._effective_familiarity(40, START)
        result = event(svc, "t-only", "insult")
        assert result["delta"]["familiarity"] == 0
        assert (
            result["before"]["familiarity"] == result["after"]["familiarity"] == before
        )
        assert raw(db).familiarity == before
        assert raw(db).familiarity_updated_at == START + 17 * DAY + 13
        assert log(db, "t-only")["relation_delta"]["familiarity"] == 0
        assert usage(db)[0][:2] == (0, 0)
        t_value, t_anchor = raw(db).trust, raw(db).trust_updated_at
    with patch("time.time", return_value=START + 60 * DAY):
        result = event(svc, "f-only")
        assert result["before"]["familiarity"] == pytest.approx(20)
        assert result["delta"]["familiarity"] == pytest.approx(0.12)
        assert raw(db).familiarity == pytest.approx(20.12)
        assert raw(db).trust == pytest.approx(svc._effective_trust(t_value, t_anchor))
        assert raw(db).trust_updated_at == START + 60 * DAY
        assert usage(db)[-1][:2] == (0.12, 0)
        assert log(db, "f-only")["relation_delta"]["familiarity"] == pytest.approx(0.12)


def test_raw_fraction_restart_undo_redo_use_effective_value_without_quota_refund(db):
    svc = service(db)
    with patch("time.time", return_value=START):
        seed(svc, f=95)
        assert event(svc, "tiny")["delta"]["familiarity"] == pytest.approx(0.015)
        assert raw(db).familiarity == pytest.approx(95.015)
    run(db.close())
    run(db.init())
    with patch("time.time", return_value=START + 60 * DAY):
        svc = service(db)
        result = event(svc, "after-decay")
        assert result["before"]["familiarity"] == pytest.approx(47.5075)
        assert raw(db).familiarity == pytest.approx(47.5875)
        used = usage(db)
        preview = run(svc.undo_preview(log(db, "after-decay")["id"]))
        run(svc.undo_log(log(db, "after-decay")["id"]))
        assert raw(db).familiarity == pytest.approx(47.5075)
        assert (
            run(svc.get("g", "u")).familiarity
            == preview["relation_after"]["familiarity"]
        )
        undo = run(db.query_logs("g", "u"))[0]
        run(svc.undo_log(undo["id"]))
        assert raw(db).familiarity == pytest.approx(47.5875)
        assert usage(db) == used


def test_rejected_events_preserve_anchor_committed_zero_event_reanchors(db):
    svc = service(db)
    with patch("time.time", return_value=START):
        seed(svc, f=95)
        event(svc, "once")
        stored = raw(db)
    with patch("time.time", return_value=START + DAY):
        event(svc, "once")
        with pytest.raises(ValueError):
            event(svc, "invalid", "not_an_event")
        assert raw(db).familiarity == stored.familiarity
        assert raw(db).familiarity_updated_at == stored.familiarity_updated_at
        result = event(svc, "uncertain", "uncertain")
        assert raw(db).familiarity == result["before"]["familiarity"]
        assert raw(db).familiarity_updated_at == START + DAY
        assert log(db, "uncertain")["relation_delta"]["familiarity"] == 0


def test_decay_does_not_refund_same_day_f_quota(db):
    svc = service(
        db,
        familiarity_half_life=5,
        caps={"familiarity": (0.1, 0), "affinity": (10, 8), "trust": (2, 5)},
    )
    with patch("time.time", return_value=START):
        seed(svc)
        event(svc, "first")
    with patch("time.time", return_value=START + 3600):
        event(svc, "second")
        event(svc, "third")
        assert usage(db)[0][:2] == pytest.approx((0.1, 0))
        assert log(db, "third")["relation_delta"]["familiarity"] == 0
        assert raw(db).familiarity_updated_at == START + 3600


def test_legacy_affinity_writes_profile_and_empty_vector_undo_preserve_f(db):
    svc = service(db)
    with patch("time.time", return_value=START):
        seed(svc)
    with patch("time.time", return_value=START + 17 * DAY + 13):
        run(svc.change("g", "u", 1))
        legacy = run(db.query_logs("g", "u"))[0]
        assert legacy["relation_delta"] == {}
        run(svc.undo_log(legacy["id"]))
        run(svc.set_favor("g", "u", 12))
        run(
            svc.set_profile(
                "g", "u", impression="合成档案", tags=[], actor="fixture-admin"
            )
        )
        assert raw(db).familiarity == 40
        assert raw(db).familiarity_updated_at == START
        assert raw(db).trust_updated_at == START


def test_manual_set_reanchors_and_reset_removes_old_f(db):
    svc = service(db)
    with patch("time.time", return_value=START):
        seed(svc)
    with patch("time.time", return_value=START + 60 * DAY):
        seed(svc, f=30, eid="manual")
        assert raw(db).familiarity_updated_at == START + 60 * DAY
        assert log(db, "manual")["relation_delta"]["familiarity"] == 10
        run(svc.reset("g", "u"))
        assert raw(db) is None
        event(svc, "new")
        assert raw(db).familiarity == pytest.approx(0.15)
        assert raw(db).familiarity_updated_at == START + 60 * DAY


def test_temporary_pause_formal_ranking_and_expiry_reject_old_task(db):
    svc = service(db)
    with patch("time.time", return_value=START):
        seed(svc)
    with patch("time.time", return_value=START + 30 * DAY):
        revision = run(svc.temporary_state("g", "u"))["revision"]
        run(
            svc.set_temporary(
                "g",
                "u",
                dict(familiarity=99, affinity=99, trust=99),
                10 * DAY,
                "fixture-admin",
            )
        )
    with patch("time.time", return_value=START + 35 * DAY):
        assert run(svc.formal_relation("g", "u"))["familiarity"] == 28.3
        assert run(svc.ranking("g"))[0].familiarity == 99
        assert not run(svc.standings("g"))[0]["decayed"]
    with patch("time.time", return_value=START + 70 * DAY):
        assert run(svc.get("g", "u")).familiarity == 20
        assert raw(db).familiarity_updated_at == START + 10 * DAY
        assert run(svc.ranking("g"))[0].familiarity == 20
        assert run(svc.standings("g"))[0]["decayed"]
        with pytest.raises(ValueError, match="版本"):
            run(
                svc.apply_relation_event(
                    "g",
                    "u",
                    "normal_question",
                    event_id="old-task",
                    cooldown_sec=0,
                    expected_revision=revision,
                )
            )


def legacy_v16(path, *, started=None, expires=None):
    conn = sqlite3.connect(path)
    for version in range(1, 17):
        for statement in MIGRATIONS[version]:
            conn.execute(statement)
    conn.execute("PRAGMA user_version=16")
    conn.execute(
        "INSERT INTO favor(group_id,user_id,favor,updated_at,familiarity,trust,trust_updated_at) VALUES('g','u',0,?,40,40,?)",
        (START - 365 * DAY, START - 3 * DAY),
    )
    if started is not None:
        conn.execute(
            "INSERT INTO relation_override VALUES('g','u',1,1,?,?,?)",
            (started, expires, json.dumps(dict(familiarity=99, affinity=99, trust=99))),
        )
    conn.commit()
    conn.create_function("strftime", 2, lambda fmt, value: str(int(START)))
    return conn


@pytest.mark.parametrize("pending_expired", [False, True])
def test_v16_upgrade_temporary_pause_counts_preupgrade_duration_once(
    tmp_path, pending_expired
):
    path = tmp_path / "legacy.db"
    started = START - (20 if pending_expired else 10) * DAY
    expires = START + (-5 if pending_expired else 10) * DAY
    conn = legacy_v16(path, started=started, expires=expires)
    migrate(conn)
    conn.close()
    db = SQLiteBackend(path)
    run(db.init())
    try:
        svc = service(db)
        if pending_expired:
            ended = START
            with patch("time.time", return_value=START):
                assert run(svc.get("g", "u")).familiarity == 40
                assert raw(db).familiarity_updated_at == START
                run(svc.get("g", "u"))
                assert raw(db).familiarity_updated_at == START
        else:
            ended = START + 8 * DAY
            with patch("time.time", return_value=START + 5 * DAY):
                assert run(svc.formal_relation("g", "u"))["familiarity"] == 40
            with patch("time.time", return_value=ended):
                run(svc.set_temporary("g", "u", None, 0, "fixture-admin"))
                assert raw(db).familiarity_updated_at == ended
        with patch("time.time", return_value=ended + 60 * DAY):
            assert run(svc.get("g", "u")).familiarity == 20
    finally:
        run(db.close())


def test_v16_upgrade_does_not_retroactively_decay_or_touch_t_anchor(tmp_path):
    path = tmp_path / "legacy.db"
    conn = legacy_v16(path)
    migrate(conn)
    conn.close()
    db = SQLiteBackend(path)
    run(db.init())
    try:
        assert raw(db).familiarity_updated_at == START
        assert raw(db).trust_updated_at == START - 3 * DAY
        with patch("time.time", return_value=START + 120 * DAY):
            assert run(service(db).get("g", "u")).familiarity == 10
    finally:
        run(db.close())


def test_schema17_failure_rolls_back_and_backup_validates_anchor(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    conn = legacy_v16(path)
    monkeypatch.setitem(
        MIGRATIONS, 17, [*MIGRATIONS[17], "SELECT missing_fixture_column FROM favor"]
    )
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 16
    assert "familiarity_updated_at" not in {
        x[1] for x in conn.execute("PRAGMA table_info(favor)")
    }
    assert conn.execute("SELECT familiarity FROM favor").fetchone()[0] == 40
    conn.close()


def test_schema17_backup_reopen_keeps_raw_value_and_all_list_anchors(db, tmp_path):
    with patch("time.time", return_value=START):
        svc = service(db)
        seed(svc, f=95)
        event(svc, "tiny")
    backup = tmp_path / "backup.db"
    with sqlite3.connect(backup) as out:
        db._c().backup(out)
    BotScopeStorageRouter._validate_backup_schema(backup)
    restored = SQLiteBackend(backup)
    run(restored.init())
    try:
        assert (
            restored._c().execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        )
        assert raw(restored).familiarity == pytest.approx(95.015)
        for rows in (
            run(restored.ranking("g")),
            run(restored.list_favor("g")),
            run(restored.list_favor()),
        ):
            assert rows[0].familiarity_updated_at == START
            assert rows[0].trust_updated_at == START
    finally:
        run(restored.close())
    with sqlite3.connect(backup) as conn:
        conn.execute("ALTER TABLE favor DROP COLUMN familiarity_updated_at")
    with pytest.raises(ValueError, match="结构不完整"):
        BotScopeStorageRouter._validate_backup_schema(backup)


def test_concurrent_f_settlement_matches_serial_raw_curve(tmp_path):
    def scenario(path, concurrent):
        first, second = SQLiteBackend(path), SQLiteBackend(path)
        run(first.init())
        run(second.init())
        try:
            with patch("time.time", return_value=START):
                seed(service(first))
            with patch("time.time", return_value=START + 60 * DAY):
                services = [service(first), service(second)]
                if concurrent:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        list(
                            pool.map(
                                lambda i: event(services[i % 2], f"e-{i}"), range(8)
                            )
                        )
                else:
                    for i in range(8):
                        event(services[i % 2], f"e-{i}")
                assert raw(first).trust_updated_at == START + 60 * DAY
                return raw(first).familiarity, usage(first)
        finally:
            run(first.close())
            run(second.close())

    assert scenario(tmp_path / "parallel.db", True) == scenario(
        tmp_path / "serial.db", False
    )


@pytest.mark.parametrize(
    "stored,amplitude,target",
    [
        (-40, 20, -20),
        (-20.1, 20, -20),
        (-20, 20, -20),
        (-19.9, 20, -19.9),
        (0, 20, 0),
        (19.9, 20, 19.9),
        (20, 20, 20),
        (20.1, 20, 20),
        (40, 20, 20),
        (-40, 0, 0),
        (40, 0, 0),
        (10, 100, 10),
    ],
)
def test_decay_target_stop_range_boundaries(stored, amplitude, target):
    from astrbot_plugin_bond.core.decay import decay_target

    assert decay_target(stored, amplitude) == target


@pytest.mark.parametrize("amplitude", [-1, float("inf"), float("nan")])
def test_stop_range_invalid_amplitude_rejected(db, amplitude):
    from astrbot_plugin_bond.core.decay import decay_target

    with pytest.raises(ValueError):
        decay_target(40, amplitude)
    for key in ("decay_baseline", "familiarity_baseline", "trust_baseline"):
        with pytest.raises(ValueError):
            service(db, **{key: amplitude})


@pytest.mark.parametrize("value", [-40, -20, -10, 0, 10, 20, 40])
def test_signed_affinity_and_trust_stop_range_never_increases_magnitude(db, value):
    svc = service(
        db,
        decay_enabled=True,
        half_life_base=30,
        half_life_growth=1,
        decay_baseline=20,
        trust_half_life=90,
        trust_baseline=20,
    )
    with patch("time.time", return_value=START):
        seed(svc, a=value, t=value)
    with patch("time.time", return_value=START + 90 * DAY):
        state = run(svc.get("g", "u"))
        expected_t = value if abs(value) <= 20 else (30 if value > 0 else -30)
        expected_a = value if abs(value) <= 20 else (22.5 if value > 0 else -22.5)
        assert state.trust == expected_t
        assert state.favor == expected_a
        assert abs(state.trust) <= abs(value)
        assert raw(db).trust == value


@pytest.mark.parametrize("value,expected", [(0, 0), (10, 10), (20, 20), (40, 30)])
def test_familiarity_stop_range_never_raises_low_value(db, value, expected):
    svc = service(db, familiarity_baseline=20)
    with patch("time.time", return_value=START):
        seed(svc, f=value)
    with patch("time.time", return_value=START + 60 * DAY):
        assert run(svc.get("g", "u")).familiarity == expected


def test_stop_ranges_reload_use_original_clocks_without_rewrite(db):
    with patch("time.time", return_value=START):
        seed(service(db), f=40, a=-40, t=-40)
    with patch("time.time", return_value=START + 60 * DAY):
        zero = service(
            db,
            decay_enabled=True,
            half_life_base=60,
            half_life_growth=1,
            trust_half_life=60,
        )
        stopped = service(
            db,
            decay_enabled=True,
            half_life_base=60,
            half_life_growth=1,
            trust_half_life=60,
            decay_baseline=20,
            familiarity_baseline=20,
            trust_baseline=20,
        )
        state = run(zero.get("g", "u"))
        assert (state.familiarity, state.favor, state.trust) == (20, -20, -20)
        state = run(stopped.get("g", "u"))
        assert (state.familiarity, state.favor, state.trust) == (30, -30, -30)
        stored = raw(db)
        assert (
            stored.familiarity_updated_at
            == stored.trust_updated_at
            == stored.updated_at
            == START
        )
        assert len(run(db.query_logs("g", "u"))) == 1


def test_stop_ranges_transaction_deltas_undo_and_legacy_writes(db):
    svc = service(
        db,
        decay_enabled=True,
        half_life_base=60,
        half_life_growth=1,
        trust_half_life=60,
        decay_baseline=20,
        familiarity_baseline=20,
        trust_baseline=20,
        rules={"friendly": {"familiarity": 0.1, "affinity": 0.5, "trust": 0.2}},
    )
    with patch("time.time", return_value=START):
        seed(svc, f=40, a=-40, t=-40)
    with patch("time.time", return_value=START + 60 * DAY):
        result = event(svc, "bounded", "friendly")
        assert result["before"] == dict(familiarity=30, affinity=-30, trust=-30)
        assert result["delta"] == dict(familiarity=0.08, affinity=0.5, trust=0.2)
        assert usage(db)[-1] == pytest.approx((0.08, 0, 0.5, 0, 0.2, 0))
        assert log(db, "bounded")["relation_delta"] == result["delta"]
        preview = run(svc.undo_preview(log(db, "bounded")["id"]))
        run(svc.undo_log(log(db, "bounded")["id"]))
        state = run(svc.get("g", "u"))
        assert preview["relation_after"] == dict(
            familiarity=30, affinity=-30, trust=-30
        )
        assert (state.familiarity, state.favor, state.trust) == (30, -30, -30)
        assert usage(db)[-1] == pytest.approx((0.08, 0, 0.5, 0, 0.2, 0))
        run(svc.set_favor("g", "u", -10))
    with patch("time.time", return_value=START + 120 * DAY):
        run(svc.change("g", "u", 1))
        assert run(svc.get("g", "u")).favor == -9
        assert raw(db).familiarity_updated_at == START + 60 * DAY
        assert raw(db).trust_updated_at == START + 60 * DAY


def test_stop_ranges_temporary_pause_and_expiry_use_same_targets(db):
    svc = service(
        db,
        decay_enabled=True,
        half_life_base=60,
        half_life_growth=1,
        trust_half_life=60,
        decay_baseline=20,
        familiarity_baseline=20,
        trust_baseline=20,
    )
    with patch("time.time", return_value=START):
        seed(svc, f=40, a=-40, t=-40)
    with patch("time.time", return_value=START + 30 * DAY):
        before = run(svc.formal_relation("g", "u"))
        run(
            svc.set_temporary(
                "g",
                "u",
                dict(familiarity=99, affinity=99, trust=99),
                30 * DAY,
                "fixture-admin",
            )
        )
    with patch("time.time", return_value=START + 45 * DAY):
        assert run(svc.formal_relation("g", "u")) == before
    with patch("time.time", return_value=START + 90 * DAY):
        state = run(svc.get("g", "u"))
        assert (state.familiarity, state.favor, state.trust) == (30, -30, -30)
        stored = raw(db)
        assert (
            stored.updated_at
            == stored.familiarity_updated_at
            == stored.trust_updated_at
            == START + 30 * DAY
        )


def test_positive_affinity_floor_preserved_with_signed_stop_target(db):
    svc = service(
        db,
        decay_enabled=True,
        half_life_base=30,
        half_life_growth=1,
        decay_floor_enabled=True,
        decay_baseline=20,
        familiarity_baseline=20,
        trust_baseline=20,
    )
    with patch("time.time", return_value=START):
        seed(svc, f=95, a=95, t=95)
    with patch("time.time", return_value=START + 3600 * DAY):
        state = run(svc.get("g", "u"))
        assert state.favor == 55
        assert state.familiarity == state.trust == 20
