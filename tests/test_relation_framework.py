"""数值框架的真实服务/SQLite 合成集成测试，不推断模型语义准确率。"""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.core.level_economy import EconomyConfig
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_profiles import build_relation_profile
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.migrations import MIGRATIONS, SCHEMA_VERSION, migrate
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

START = datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp()
DAY = 86400


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def db(tmp_path):
    store = SQLiteBackend(tmp_path / "fixture.db")
    run(store.init())
    try:
        yield store
    finally:
        run(store.close())


def service(db, mode="realistic", *, rules=None, caps=None, event_caps=None, **kwargs):
    # legacy 仅联动 F/A；T 仍只由合格的 T 事件结算和维护。
    kwargs.setdefault("maintenance_strategy", "legacy")
    policy, effective_caps = build_relation_profile(
        mode, rules=rules, event_caps=event_caps, caps=caps
    )
    return FavorService(
        db,
        LevelTable.from_config(None),
        relation_policy=policy,
        relation_caps=effective_caps,
        tz_name="Asia/Shanghai",
        **kwargs,
    )


def seed(svc, *, f=95, a=0, t=0, event_id="seed", user="u"):
    return run(
        svc.set_relation(
            "g", user, familiarity=f, affinity=a, trust=t, event_id=event_id
        )
    )


def event(svc, eid, kind="normal_question", *, user="u", **kwargs):
    return run(
        svc.apply_relation_event(
            "g", user, kind, event_id=eid, cooldown_sec=0, **kwargs
        )
    )


def raw(db, user="u"):
    return run(db.get("g", user))


def usage(db, svc, user="u"):
    return (
        db._c()
        .execute(
            "SELECT familiarity_up,familiarity_down,affinity_up,affinity_down,trust_up,trust_down,positive_count FROM daily_gain WHERE group_id='g' AND user_id=? AND day=?",
            (user, svc._today().isoformat()),
        )
        .fetchone()
    )


def log_for(db, eid):
    return next(
        row for row in run(db.query_logs("g", "u")) if row.get("event_id") == eid
    )


def test_raw_f_carry_restart_duplicate_admin_set_and_undo_redo(db):
    with patch("time.time", return_value=START):
        svc = service(db)
        seed(svc)
        first = event(svc, "tiny")
        assert first["delta"]["familiarity"] == pytest.approx(0.015)
        assert raw(db).familiarity == pytest.approx(95.015)
        assert run(svc.get_relation("g", "u")).familiarity == 95
        original = log_for(db, "tiny")
        assert original["relation_delta"]["familiarity"] == pytest.approx(0.015)
        run(db.close())
        run(db.init())
        svc = service(db)
        assert raw(db).familiarity == pytest.approx(95.015)
        assert event(svc, "tiny")["skipped"] == "duplicate"
        assert raw(db).familiarity == pytest.approx(95.015)
        run(svc.undo_log(original["id"]))
        assert raw(db).familiarity == 95
        undo = run(db.query_logs("g", "u"))[0]
        run(svc.undo_log(undo["id"]))
        assert raw(db).familiarity == pytest.approx(95.015)
        for i in range(3):
            event(svc, f"next-{i}")
        assert raw(db).familiarity == pytest.approx(95.06)
        assert run(svc.get_relation("g", "u")).familiarity == 95.1
        seed(svc, f=95.2, event_id="manual-reset")
        event(svc, "after-manual")
        assert raw(db).familiarity == pytest.approx(95.215)


def test_fractional_rewards_cannot_bypass_daily_or_total_bounds(db):
    with patch("time.time", return_value=START):
        svc = service(db, caps={"familiarity": (0.02, 0)})
        seed(svc)
        for i in range(20):
            event(svc, f"fraction-{i}")
        assert raw(db).familiarity == pytest.approx(95.02)
        assert usage(db, svc)[0] == pytest.approx(0.02)
        assert sum(
            row["relation_delta"].get("familiarity", 0)
            for row in run(db.query_logs("g", "u"))
            if row["source"] == "judge"
        ) == pytest.approx(0.02)
    with patch("time.time", return_value=START + DAY):
        event(svc, "next-day")
        assert raw(db).familiarity == pytest.approx(95.035)
        seed(svc, f=99.9, event_id="near-total")
        unrestricted = service(db)
        for i in range(20):
            event(unrestricted, f"total-{i}")
        assert raw(db).familiarity == 100
        assert event(unrestricted, "at-total")["delta"]["familiarity"] == 0


def test_synthetic_positive_negative_quotas_and_undo_do_not_refund(db):
    # 显式合成 T 规则仅验证日额与撤销数学，不代表自动评分证据。
    rules = {
        "friendly": {"familiarity": 0, "affinity": 10, "trust": 2},
        "insult": {"affinity": -8, "trust": -8},
    }
    with patch("time.time", return_value=START):
        svc = service(db, rules=rules, event_caps={"friendly": 10, "insult": 8})
        seed(svc, f=0)
        event(svc, "up", "friendly")
        event(svc, "down", "insult")
        assert raw(db).favor == 2
        assert raw(db).trust == -6
        consumed = usage(db, svc)
        assert consumed == (0, 0, 10, 8, 2, 8, 1)
        assert not any(event(svc, "no-more-up", "friendly")["delta"].values())
        assert not any(event(svc, "no-more-down", "insult")["delta"].values())
        run(svc.undo_log(log_for(db, "up")["id"]))
        assert usage(db, svc) == consumed
        assert not any(event(svc, "no-refund", "friendly")["delta"].values())
        undo = next(
            row for row in run(db.query_logs("g", "u")) if row["source"] == "undo"
        )
        run(svc.undo_log(undo["id"]))
        assert raw(db).favor == 2
        assert raw(db).trust == -6
        assert usage(db, svc) == consumed


def test_synthetic_mode_switch_caps_do_not_multiply_existing_state_or_refill(db):
    # 自定义 T 增量仅作为倍率与日额的数学输入，不代表自动评分证据。
    with patch("time.time", return_value=START):
        options = dict(
            rules={"friendly": {"familiarity": 0, "affinity": 3, "trust": 0.5}},
            event_caps={"friendly": 10},
            caps={"affinity": (3, 4), "trust": (0.5, 1)},
        )
        realistic = service(db, **options)
        seed(realistic, f=0)
        event(realistic, "base", "friendly")
        assert (raw(db).favor, raw(db).trust) == (3, 0.5)
        galgame = service(db, "galgame", **options)
        assert (raw(db).favor, raw(db).trust) == (3, 0.5)
        event(galgame, "boosted", "friendly")
        assert (raw(db).favor, raw(db).trust) == (6, 1)
        event(realistic, "switch-back", "friendly")
        event(galgame, "switch-again", "friendly")
        assert (raw(db).favor, raw(db).trust) == (6, 1)
        assert usage(db, galgame)[2:6] == (6, 0, 1, 0)


def test_concurrent_f_curve_and_a_positive_count_equal_serial(db):
    with patch("time.time", return_value=START):
        svc = service(db, economy=EconomyConfig())
        seed(svc, f=9.9, user="serial")
        seed(svc, f=9.9, user="parallel", event_id="parallel-seed")
        for i in range(8):
            event(svc, f"serial-{i}", "friendly", user="serial")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(
                    lambda i: event(svc, f"parallel-{i}", "friendly", user="parallel"),
                    range(8),
                )
            )
        assert all(result["skipped"] is None for result in results)
        serial, parallel = raw(db, "serial"), raw(db, "parallel")
        assert parallel.familiarity == serial.familiarity == pytest.approx(12.87)
        assert parallel.favor == serial.favor == pytest.approx(2.8)
        assert usage(db, svc, "parallel") == usage(db, svc, "serial")
        assert usage(db, svc, "parallel")[-1] == 8


def test_transaction_log_failure_rolls_back_raw_carry_and_usage(db):
    with patch("time.time", return_value=START):
        svc = service(db)
        seed(svc)
        db._c().execute(
            "CREATE TRIGGER fixture_reject BEFORE INSERT ON favor_log BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
        db._c().commit()
        with pytest.raises(sqlite3.IntegrityError):
            event(svc, "retry")
        assert raw(db).familiarity == 95
        assert usage(db, svc) is None
        db._c().execute("DROP TRIGGER fixture_reject")
        db._c().commit()
        assert event(svc, "retry")["delta"]["familiarity"] == pytest.approx(0.015)


def decaying(db, **kwargs):
    return service(
        db,
        decay_enabled=True,
        half_life_base=kwargs.pop("a_half", 60),
        half_life_growth=1,
        half_life_max=360,
        trust_decay_enabled=True,
        trust_half_life=kwargs.pop("t_half", 180),
        **kwargs,
    )


@pytest.mark.parametrize("grace_days", [0, 5])
@pytest.mark.parametrize("trust", [40, -40])
def test_separate_decay_halflives_and_f_does_not_decay(db, trust, grace_days):
    with patch("time.time", return_value=START):
        svc = decaying(db, decay_grace_days=grace_days)
        seed(svc, a=40, t=trust)
    with patch("time.time", return_value=START + 60 * DAY):
        state = run(svc.get_relation("g", "u"))
        assert state.familiarity == 95
        assert state.affinity == round(40 * 0.5 ** ((60 - grace_days) / 60), 1)
        assert state.trust == pytest.approx(
            round(trust * 0.5 ** ((60 - grace_days) / 180), 1)
        )
    with patch("time.time", return_value=START + 180 * DAY):
        state = run(svc.get_relation("g", "u"))
        assert state.familiarity == 95
        assert state.affinity == round(40 * 0.5 ** ((180 - grace_days) / 60), 1)
        assert state.trust == round(trust * 0.5 ** ((180 - grace_days) / 180), 1)
        assert raw(db).trust == trust
        assert raw(db).trust_updated_at == START


@pytest.mark.parametrize("grace_days", [0, 5])
@pytest.mark.parametrize("trust", [40, -40])
@pytest.mark.parametrize("kind", ["normal_question", "friendly", "insult"])
def test_f_a_writes_and_undo_preserve_raw_trust_and_continuous_decay(
    db, kind, trust, grace_days
):
    with patch("time.time", return_value=START):
        svc = decaying(db, decay_grace_days=grace_days)
        seed(svc, a=40, t=trust)
    for days in (30, 60, 90):
        # F-only、A-only 和 F/A 互动均不重授 T 保温期，按原始锚连续衰减。
        expected = trust * 0.5 ** ((days - grace_days) / 180)
        with patch("time.time", return_value=START + days * DAY):
            assert run(svc.get_relation("g", "u")).trust == round(expected, 1)
            result = event(svc, f"event-{days}", kind)
            assert result["delta"]["trust"] == 0
            assert result["before"]["trust"] == pytest.approx(expected)
            assert result["after"]["trust"] == pytest.approx(expected)
            assert raw(db).trust == trust
            assert raw(db).trust_updated_at == raw(db).trust_maintained_at == START
            assert run(svc.get_relation("g", "u")).trust == round(expected, 1)
            assert usage(db, svc)[4:6] == (0, 0)
    with patch("time.time", return_value=START + 120 * DAY):
        expected = trust * 0.5 ** ((120 - grace_days) / 180)
        assert run(svc.get_relation("g", "u")).trust == round(expected, 1)
        run(svc.undo_log(log_for(db, "event-90")["id"]))
        # 撤销 F/A 增量同样保留 T 原始值及其结算、维护时钟。
        assert raw(db).trust == trust
        assert raw(db).trust_updated_at == raw(db).trust_maintained_at == START
        assert run(svc.get_relation("g", "u")).trust == round(expected, 1)
        assert run(db.query_logs("g", "u"))[0]["relation_delta"]["trust"] == 0
    with patch("time.time", return_value=START + 180 * DAY):
        expected = trust * 0.5 ** ((180 - grace_days) / 180)
        assert run(svc.get_relation("g", "u")).trust == round(expected, 1)
        assert abs(expected) < abs(trust)
        assert raw(db).trust == trust
        assert raw(db).trust_updated_at == raw(db).trust_maintained_at == START
        assert usage(db, svc) is None  # 读取衰减不消费互动扣分额度。


@pytest.mark.parametrize("grace_days", [0, 5])
def test_reload_half_life_changes_readout_without_rewriting_anchor(db, grace_days):
    with patch("time.time", return_value=START):
        old = decaying(db, decay_grace_days=grace_days)
        seed(old, a=40, t=-40)
    with patch("time.time", return_value=START + 60 * DAY):
        assert run(old.get_relation("g", "u")).affinity == round(
            40 * 0.5 ** ((60 - grace_days) / 60), 1
        )
        slower = decaying(db, a_half=120, t_half=360, decay_grace_days=grace_days)
        state = run(slower.get_relation("g", "u"))
        assert state.affinity == round(40 * 0.5 ** ((60 - grace_days) / 120), 1)
        assert state.trust == round(-40 * 0.5 ** ((60 - grace_days) / 360), 1)
        assert raw(db).updated_at == raw(db).trust_updated_at == START
        assert len(run(db.query_logs("g", "u"))) == 1


def test_admin_set_resets_both_decay_anchors_and_undo_remains_symmetric(db):
    with patch("time.time", return_value=START):
        svc = decaying(db)
        seed(svc, a=40, t=-40)
    with patch("time.time", return_value=START + 60 * DAY):
        before = run(svc.get_relation("g", "u"))
        seed(svc, f=96, a=30, t=-10, event_id="edit")
        assert raw(db).updated_at == raw(db).trust_updated_at == START + 60 * DAY
        run(svc.undo_log(log_for(db, "edit")["id"]))
        assert run(svc.get_relation("g", "u")) == before
        undo = run(db.query_logs("g", "u"))[0]
        run(svc.undo_log(undo["id"]))
        assert (raw(db).familiarity, raw(db).favor, raw(db).trust) == (96, 30, -10)


@pytest.mark.parametrize("grace_days", [0, 5])
def test_temporary_pauses_both_clocks_and_expiry_rejects_old_work(db, grace_days):
    with patch("time.time", return_value=START):
        svc = decaying(db, decay_grace_days=grace_days)
        seed(svc, a=40, t=-40)
    with patch("time.time", return_value=START + 30 * DAY):
        before = run(svc.formal_relation("g", "u"))
        revision = run(svc.temporary_state("g", "u"))["revision"]
        run(
            svc.set_temporary(
                "g",
                "u",
                dict(familiarity=5, affinity=99, trust=99),
                30 * DAY,
                "fixture-admin",
            )
        )
    with patch("time.time", return_value=START + 45 * DAY):
        assert run(svc.get_relation("g", "u")).trust == 99
        assert run(svc.formal_relation("g", "u")) == before
        with pytest.raises(ValueError, match="临时关系"):
            event(svc, "in-flight", expected_revision=revision)
    run(db.close())
    run(db.init())
    with patch("time.time", return_value=START + 60 * DAY):
        assert not run(svc.temporary_state("g", "u"))["active"]
        assert run(svc.formal_relation("g", "u")) == before
        assert raw(db).trust_updated_at == START + 30 * DAY
        with pytest.raises(ValueError, match="版本"):
            event(svc, "old-after-expiry", expected_revision=revision)
    with patch("time.time", return_value=START + 90 * DAY):
        state = run(svc.get_relation("g", "u"))
        assert state.affinity == round(40 * 0.5 ** ((60 - grace_days) / 60), 1)
        assert state.trust == round(-40 * 0.5 ** ((60 - grace_days) / 180), 1)


def v15(path):
    conn = sqlite3.connect(path)
    for version in range(1, 16):
        for stmt in MIGRATIONS[version]:
            conn.execute(stmt)
    conn.execute("PRAGMA user_version=15")
    conn.execute(
        "INSERT INTO favor(group_id,user_id,favor,updated_at,familiarity,trust) VALUES('g','u',2,?,0.2,40)",
        (START - 365 * DAY,),
    )
    conn.commit()
    return conn


def test_v15_local_day_reconstruction_excludes_admin_undo_and_other_day(tmp_path):
    path = tmp_path / "legacy.db"
    conn = v15(path)
    day = "2026-11-03"
    conn.execute(
        "INSERT INTO daily_gain(group_id,user_id,day,gain,familiarity_gain,trust_gain) VALUES('g','u',?,2,0.2,-1)",
        (day,),
    )
    for delta, trust, f, ts, source in [
        (5, 2, 0.2, START - 4 * 3600, "judge"),
        (-3, -3, 0, START, "judge"),
        (20, 20, 2, START - 10 * 3600, "judge"),
        (40, 40, 2, START, "admin"),
        (2, 2, 2, START, "undo"),
    ]:
        conn.execute(
            "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,source,ts,relation_delta) VALUES('g','u',?,0,?,?,?,?)",
            (
                delta,
                delta,
                source,
                ts,
                json.dumps(dict(familiarity=f, affinity=delta, trust=trust)),
            ),
        )
    conn.commit()
    conn.close()
    db = SQLiteBackend(path)
    run(db.init())
    try:
        assert db._c().execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert raw(db).trust == 40 and raw(db).trust_updated_at > 0
        with patch("time.time", return_value=START):
            svc = service(
                db, rules={"friendly": {"familiarity": 0, "affinity": 1, "trust": 0}}
            )
            event(svc, "new", "friendly")
            assert usage(db, svc) == (0.2, 0, 6, 3, 2, 3, 2)
            assert (
                db._c()
                .execute("SELECT split_ready FROM daily_gain WHERE day=?", (day,))
                .fetchone()[0]
                == 1
            )
            event(svc, "another", "friendly")
            assert usage(db, svc)[2:4] == (7, 3)  # 历史只重建一次。
    finally:
        run(db.close())


def test_v16_migration_failure_rolls_back_schema_and_keeps_backup(
    tmp_path, monkeypatch
):
    path, backup = tmp_path / "legacy.db", tmp_path / "backup.db"
    conn = v15(path)
    saved = sqlite3.connect(backup)
    conn.backup(saved)
    saved.close()
    monkeypatch.setitem(
        MIGRATIONS, 16, [*MIGRATIONS[16], "SELECT fixture_missing_column FROM favor"]
    )
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    assert "trust_updated_at" not in {
        row[1] for row in conn.execute("PRAGMA table_info(favor)")
    }
    assert conn.execute("SELECT trust FROM favor").fetchone()[0] == 40
    conn.close()
    with sqlite3.connect(backup) as saved:
        assert saved.execute("PRAGMA user_version").fetchone()[0] == 15
        assert saved.execute("SELECT trust FROM favor").fetchone()[0] == 40


def test_current_schema_backup_reopen_and_future_version_rejection(db, tmp_path):
    with patch("time.time", return_value=START):
        svc = service(db)
        seed(svc, t=40)
        event(svc, "fraction")
    backup = tmp_path / "current-backup.db"
    with sqlite3.connect(backup) as out:
        db._c().backup(out)
    restored = SQLiteBackend(backup)
    run(restored.init())
    try:
        assert raw(restored).familiarity == pytest.approx(95.015)
        assert raw(restored).trust_updated_at == START
        assert (
            restored._c().execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        )
    finally:
        run(restored.close())
    with sqlite3.connect(backup) as conn:
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    future = SQLiteBackend(backup)
    with pytest.raises(ValueError, match="未来"):
        run(future.init())
    run(future.close())
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION + 1


def test_raw_fractional_state_isolated_across_bots_groups_and_spaces(tmp_path):
    from astrbot_plugin_bond.storage.router import BotScopeStorageRouter

    router = BotScopeStorageRouter(tmp_path / "isolated")
    group_id = "30003"
    try:
        with patch("time.time", return_value=START):
            stores = {}
            for bot, mode in (
                ("10001", "per_group"),
                ("10001", "global"),
                ("20002", "per_group"),
            ):
                store = run(router.get(bot, mode))
                stores[bot, mode] = store
                svc = service(store, scope_mode=mode)
                run(
                    svc.set_relation(
                        group_id,
                        "u",
                        familiarity=95,
                        affinity=0,
                        trust=0,
                        event_id="seed",
                    )
                )
            group = service(stores["10001", "per_group"])
            run(
                group.apply_relation_event(
                    group_id,
                    "u",
                    "normal_question",
                    event_id="one-fraction",
                    cooldown_sec=0,
                )
            )
            assert run(
                stores["10001", "per_group"].get(group_id, "u")
            ).familiarity == pytest.approx(95.015)
            assert (
                run(stores["20002", "per_group"].get(group_id, "u")).familiarity == 95
            )
            global_svc = service(stores["10001", "global"], scope_mode="global")
            assert (
                run(
                    stores["10001", "global"].get(global_svc.group_key(group_id), "u")
                ).familiarity
                == 95
            )
            assert run(group.get_relation("30004", "u")).familiarity == 0
            assert run(group.get_relation(group_id, "other-member")).familiarity == 0
    finally:
        run(router.close())


def test_two_connections_share_caps_and_atomic_positive_counts(db):
    other = SQLiteBackend(db._db_path)
    run(other.init())
    try:
        with patch("time.time", return_value=START):
            first = service(
                db,
                economy=EconomyConfig(),
                caps={"familiarity": (0.7, 0), "affinity": (1, 8)},
            )
            second = service(
                other,
                economy=EconomyConfig(),
                caps={"familiarity": (0.7, 0), "affinity": (1, 8)},
            )
            seed(first, f=0)

            def write(i):
                return event(first if i % 2 else second, f"multi-{i}", "friendly")

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(write, range(12)))
            assert raw(db).familiarity == pytest.approx(0.7)
            assert raw(db).favor == 1
            assert usage(db, first)[0] == pytest.approx(0.7)
            assert usage(db, first)[2] == 1
            assert (
                usage(db, first)[-1] == 2
            )  # 0.8 后仅实际剩余 0.2，之后零增量不增加计数。
    finally:
        run(other.close())


def test_v15_missing_history_keeps_net_proven_minimum_usage(tmp_path):
    path = tmp_path / "net-only.db"
    conn = v15(path)
    conn.execute(
        "INSERT INTO daily_gain(group_id,user_id,day,gain,familiarity_gain,trust_gain) VALUES('g','u','2026-11-03',-3,0.3,2)"
    )
    conn.commit()
    conn.close()
    db = SQLiteBackend(path)
    run(db.init())
    try:
        with patch("time.time", return_value=START):
            svc = service(
                db, rules={"friendly": {"familiarity": 0, "affinity": 1, "trust": 0}}
            )
            event(svc, "new-after-net", "friendly")
            assert usage(db, svc) == (0.3, 0, 1, 3, 2, 0, 1)
    finally:
        run(db.close())


@pytest.mark.parametrize("grace_days", [0, 5])
@pytest.mark.parametrize("trust", [40, -40])
def test_synthetic_trust_change_settles_own_clock_and_undo_redo_once(
    db, trust, grace_days
):
    with patch("time.time", return_value=START):
        # 合成 T 扣分隔离结算/撤销数学，真实新事件的证据校验由 test_trust_v2 覆盖。
        svc = decaying(
            db,
            decay_grace_days=grace_days,
            rules={"insult": {"trust": -1.5}},
        )
        seed(svc, a=40, t=trust)
    with patch("time.time", return_value=START + 90 * DAY):
        before = trust * 0.5 ** ((90 - grace_days) / 180)
        result = event(svc, "breach", "insult")
        assert result["delta"]["trust"] == -1.5
        assert raw(db).trust == pytest.approx(before - 1.5)
        assert raw(db).trust_updated_at == START + 90 * DAY
        assert raw(db).trust_maintained_at == START + 90 * DAY
        used = usage(db, svc)
        run(svc.undo_log(log_for(db, "breach")["id"]))
        assert raw(db).trust == pytest.approx(before)
        undo = run(db.query_logs("g", "u"))[0]
        run(svc.undo_log(undo["id"]))
        assert raw(db).trust == pytest.approx(before - 1.5)
        assert usage(db, svc) == used
    with patch("time.time", return_value=START + 270 * DAY):
        assert run(svc.get_relation("g", "u")).trust == round(
            (before - 1.5) * 0.5 ** ((180 - grace_days) / 180), 1
        )


@pytest.mark.parametrize("grace_days", [0, 5])
@pytest.mark.parametrize("trust", [40, -40])
def test_v15_override_started_before_upgrade_and_ended_after_upgrade(
    tmp_path, trust, grace_days
):
    path = tmp_path / "active-at-upgrade.db"
    conn = v15(path)
    started, expires = START - 10 * DAY, START + 10 * DAY
    ended = START + 8 * DAY
    conn.execute("UPDATE favor SET trust=?", (trust,))
    conn.execute(
        "INSERT INTO relation_override VALUES('g','u',1,1,?,?,?)",
        (started, expires, json.dumps(dict(familiarity=5, affinity=99, trust=99))),
    )
    conn.commit()
    # 固定迁移的 SQLite 时钟；生产仍调用同一 migrate/SQL 路径。
    conn.create_function("strftime", 2, lambda fmt, value: str(int(START)))
    migrate(conn)
    conn.close()
    db = SQLiteBackend(path)
    run(db.init())
    try:
        svc = decaying(db, decay_grace_days=grace_days)
        with patch("time.time", return_value=START + 5 * DAY):
            assert run(svc.formal_relation("g", "u"))["trust"] == trust
            assert raw(db).trust == trust
        with patch("time.time", return_value=ended):
            run(svc.set_temporary("g", "u", None, 0, "fixture-admin"))
            assert raw(db).trust_updated_at == ended
            assert run(svc.get_relation("g", "u")).trust == trust
        run(db.close())
        run(db.init())
        with patch("time.time", return_value=ended + 180 * DAY):
            assert run(svc.get_relation("g", "u")).trust == round(
                trust * 0.5 ** ((180 - grace_days) / 180), 1
            )
    finally:
        run(db.close())


@pytest.mark.parametrize("grace_days", [0, 5])
@pytest.mark.parametrize("trust", [40, -40])
def test_v15_override_expired_before_upgrade_but_not_settled(
    tmp_path, trust, grace_days
):
    path = tmp_path / "expired-at-upgrade.db"
    conn = v15(path)
    started, expires = START - 20 * DAY, START - 5 * DAY
    conn.execute("UPDATE favor SET trust=?", (trust,))
    conn.execute(
        "INSERT INTO relation_override VALUES('g','u',1,1,?,?,?)",
        (started, expires, json.dumps(dict(familiarity=5, affinity=99, trust=99))),
    )
    conn.commit()
    conn.create_function("strftime", 2, lambda fmt, value: str(int(START)))
    migrate(conn)
    conn.close()
    db = SQLiteBackend(path)
    run(db.init())
    try:
        svc = decaying(db, decay_grace_days=grace_days)
        with patch("time.time", return_value=START):
            assert run(svc.get_relation("g", "u")).trust == trust
            assert not run(svc.temporary_state("g", "u"))["active"]
            assert raw(db).trust_updated_at == START
            assert run(svc.temporary_audit("g", "u"))[0]["action"] == "expired"
        with patch("time.time", return_value=START + 180 * DAY):
            assert run(svc.get_relation("g", "u")).trust == round(
                trust * 0.5 ** ((180 - grace_days) / 180), 1
            )
            assert raw(db).trust_updated_at == START  # 再读不重复补暂停时长。
    finally:
        run(db.close())
