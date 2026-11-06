"""被动熟悉度事务：固定时钟和真实 SQLite，不调用模型。"""

import asyncio
import sqlite3
from dataclasses import asdict
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.scope import GLOBAL_GROUP
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

START = 1_800_000_000.0
DAY = 86400


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def setup(tmp_path):
    now = [START]
    with patch("time.time", side_effect=lambda: now[0]):
        db = SQLiteBackend(tmp_path / "10001" / "passive.db")
        run(db.init())
        svc = FavorService(
            db,
            LevelTable.from_config(None),
            decay_enabled=True,
            half_life_base=30,
            familiarity_decay_enabled=True,
            familiarity_half_life=30,
            familiarity_baseline=10,
            trust_decay_enabled=True,
            trust_half_life=30,
            decay_grace_days=5,
        )
        yield db, svc, now
        run(db.close())


def award(db, svc, event="passive-one", group="123", user="456", **opts):
    params = dict(
        event_id=event,
        day=svc._today().isoformat(),
        day_start=svc._day_start(),
        day_end=svc._day_end(),
        daily_cap_up=5,
        origin_group_id="123",
    )
    params.update(opts)
    return run(db.apply_passive_familiarity(group, user, **params))


def seed(db, svc, f=40, group="123", user="456"):
    run(
        svc.set_relation(
            group, user, familiarity=f, affinity=50, trust=-30, event_id="seed"
        )
    )
    return run(db.get(group, user))


def unrelated(rec):
    vals = asdict(rec)
    for key in ("familiarity", "familiarity_updated_at"):
        vals.pop(key)
    return vals


@pytest.mark.parametrize("strategy", ["independent", "legacy"])
def test_awards_settle_f_without_maintaining_any_dimension(setup, strategy):
    db, svc, now = setup
    db.configure_maintenance(strategy, svc._effective_record)
    initial = seed(db, svc)
    expected = 40.0
    last = START + 5 * DAY
    for i, offset in enumerate((7, 9, 15, 21)):
        now[0] = START + offset * DAY
        expected = 10 + (expected - 10) * 2 ** (-(now[0] - last) / (30 * DAY))
        last = now[0]
        res = award(db, svc, event=f"passive-{i}")
        assert res["before"]["familiarity"] == pytest.approx(expected)
        expected += 0.3
        assert res["after"]["familiarity"] == pytest.approx(expected)
        raw = run(db.get("123", "456"))
        assert unrelated(raw) == unrelated(initial)
        assert raw.familiarity_updated_at == now[0]
        assert not any(res["maintenance"]["maintenance_applied"].values())
    now[0] += DAY
    assert svc._effective_record(raw)["familiarity"] == pytest.approx(
        10 + (expected - 10) * 2 ** (-1 / 30)
    )


def test_new_member_has_no_implicit_grace(setup):
    db, svc, now = setup
    res = award(db, svc)
    assert res["delta"] == dict(familiarity=0.3, affinity=0, trust=0)
    raw = run(db.get("123", "456"))
    assert raw.familiarity_maintained_at == 0
    assert raw.affinity_maintained_at == raw.trust_maintained_at == 0
    assert raw.updated_at == raw.trust_updated_at == 0
    now[0] += DAY
    # F低于基线时保持原值，不会自动向10上升；换零基线验证立即衰减。
    svc._familiarity_baseline = 0
    assert svc._effective_record(raw)["familiarity"] == pytest.approx(
        0.3 * 2 ** (-1 / 30)
    )
    assert len(run(db.impression_logs("123", "456", 20))) == 0


def test_null_legacy_clock_keeps_old_anchor_not_new(setup):
    db, svc, now = setup
    seed(db, svc)
    db._c().execute("UPDATE favor SET familiarity_maintained_at=NULL")
    db._c().commit()
    now[0] += 9 * DAY
    award(db, svc)
    assert run(db.get("123", "456")).familiarity_maintained_at == START


def test_bound_and_daily_quota_do_not_write_or_maintain(setup):
    db, svc, now = setup
    initial = seed(db, svc, 80)
    assert award(db, svc)["skipped"] == "bound"
    assert run(db.get("123", "456")) == initial
    assert db._c().execute("SELECT COUNT(*) FROM cooldown").fetchone()[0] == 0
    now[0] += 30 * DAY
    assert award(db, svc, event="later")["delta"]["familiarity"] == 0.3
    rec = run(db.get("123", "456"))
    now[0] += 1800
    assert award(db, svc, event="quota", daily_cap_up=0.3)["skipped"] == "quota"
    assert run(db.get("123", "456")) == rec


def test_partial_awards_clip_ceiling_and_remainder(setup):
    db, svc, now = setup
    seed(db, svc, 59.8)
    res = award(db, svc)
    assert res["after"]["familiarity"] == 60
    assert res["delta"]["familiarity"] == pytest.approx(0.2)
    assert run(db.daily_usage("123", "456", svc._today().isoformat()))["familiarity"][
        0
    ] == pytest.approx(0.2)
    seed(db, svc, 30, user="789")
    res = award(db, svc, user="789", daily_cap_up=0.07)
    assert res["delta"]["familiarity"] == 0.07
    assert res["after"]["familiarity"] == 30.07


def test_cooldown_and_duplicate_survive_restart(setup):
    db, svc, now = setup
    award(db, svc)
    now[0] += 1799
    assert award(db, svc, event="second")["skipped"] == "cooldown"
    run(db.close())
    run(db.init())
    now[0] += 1
    assert award(db, svc, event="second")["delta"]["familiarity"] == 0.3
    now[0] += 1800
    assert award(db, svc)["skipped"] == "duplicate"
    assert db._c().execute("SELECT COUNT(*) FROM favor_log").fetchone()[0] == 2


def test_global_group_shares_cooldown_and_keeps_origin(setup):
    db, svc, now = setup
    award(db, svc, group=GLOBAL_GROUP, origin_group_id="123")
    res = award(db, svc, event="othergroup", group=GLOBAL_GROUP, origin_group_id="999")
    assert res["skipped"] == "cooldown"
    row = (
        db._c()
        .execute("SELECT group_id,origin_group_id,message,source FROM favor_log")
        .fetchone()
    )
    assert row == (GLOBAL_GROUP, "123", "", "passive_familiarity")
    assert (
        award(db, svc, event="isolated", group="other")["delta"]["familiarity"] == 0.3
    )


def test_shared_quota_and_direct_cooldown_are_independent(setup):
    db, svc, now = setup
    seed(db, svc)
    svc._relation_caps["familiarity"] = (0.35, 0)
    award(db, svc, daily_cap_up=0.35)
    result = run(
        svc.apply_relation_event(
            "123", "456", "normal_question", event_id="direct", cooldown_sec=120
        )
    )
    assert result["skipped"] is None
    assert result["delta"]["familiarity"] == pytest.approx(0.05)
    now[0] += 1800
    assert award(db, svc, event="second", daily_cap_up=0.35)["skipped"] == "quota"


def test_transaction_failure_rolls_back_score_quota_log_cooldown(setup):
    db, svc, now = setup
    initial = seed(db, svc)
    db._c().execute(
        "CREATE TRIGGER fail_passive BEFORE INSERT ON favor_log WHEN NEW.source='passive_familiarity' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
    )
    db._c().commit()
    with pytest.raises(sqlite3.IntegrityError):
        award(db, svc)
    assert run(db.get("123", "456")) == initial
    assert db._c().execute("SELECT COUNT(*) FROM daily_gain").fetchone()[0] == 0
    assert db._c().execute("SELECT COUNT(*) FROM cooldown").fetchone()[0] == 0


def test_temporary_and_obsolete_revision_cannot_award(setup):
    db, svc, now = setup
    initial = seed(db, svc)
    run(
        svc.set_temporary(
            "123", "456", dict(familiarity=90, affinity=60, trust=30), 60, "admin"
        )
    )
    with pytest.raises(ValueError):
        award(db, svc)
    assert run(db.get("123", "456")) == initial
    now[0] += 61
    with pytest.raises(ValueError):
        award(db, svc, expected_revision=0)
    assert db._c().execute("SELECT COUNT(*) FROM cooldown").fetchone()[0] == 0


def test_undo_passive_does_not_maintain_or_refund(setup):
    db, svc, now = setup
    initial = seed(db, svc)
    award(db, svc)
    log_id = (
        db._c()
        .execute("SELECT id FROM favor_log WHERE source='passive_familiarity'")
        .fetchone()[0]
    )
    now[0] += 30
    run(svc.undo_log(log_id))
    raw = run(db.get("123", "456"))
    assert raw.familiarity == pytest.approx(initial.familiarity)
    assert raw.familiarity_maintained_at == initial.familiarity_maintained_at
    assert raw.affinity_maintained_at == initial.affinity_maintained_at
    assert raw.trust_maintained_at == initial.trust_maintained_at
    assert award(db, svc, event="after-undo")["skipped"] == "cooldown"
    assert (
        run(db.daily_usage("123", "456", svc._today().isoformat()))["familiarity"][0]
        == 0.3
    )


def test_concurrent_connections_cannot_double_award(setup):
    db, svc, now = setup
    other = SQLiteBackend(db._db_path)
    run(other.init())
    other._effective_record = svc._effective_record

    async def work():
        return await asyncio.gather(
            asyncio.to_thread(award, db, svc, "first"),
            asyncio.to_thread(award, other, svc, "second"),
        )

    try:
        results = run(work())
        assert sorted(x["delta"]["familiarity"] for x in results) == [0, 0.3]
        assert run(db.get("123", "456")).familiarity == 0.3
    finally:
        run(other.close())


@pytest.mark.parametrize(
    "opts",
    [
        {"amount": float("nan")},
        {"ceiling": 101},
        {"daily_cap_up": -1},
        {"cooldown_sec": False},
        {"event_id": ""},
    ],
)
def test_invalid_parameters_leave_no_data(setup, opts):
    db, svc, now = setup
    with pytest.raises(ValueError):
        award(db, svc, **opts)
    assert run(db.get("123", "456")) is None


def test_snapshot_excludes_passive_before_limit_but_audit_keeps_it(setup):
    db, svc, now = setup
    seed(db, svc)
    run(svc.apply_relation_event("123", "456", "friendly", event_id="meaningful"))
    for n in range(4):
        now[0] += 1800
        award(db, svc, event=f"passive-{n}")
    assert (
        run(db.query_logs("123", "456", limit=1))[0]["source"] == "passive_familiarity"
    )
    _, _, logs, _ = run(db.read_relation_snapshot("123", "456", log_limit=1))
    assert len(logs) == 1
    assert logs[0]["event_id"] == "meaningful"


@pytest.mark.parametrize("strategy", ["independent", "legacy"])
def test_passive_undo_chain_preserves_maintenance_and_evidence(setup, strategy):
    db, svc, now = setup
    db.configure_maintenance(strategy, svc._effective_record)
    initial = seed(db, svc)
    evidence_ids = [
        row["id"]
        for row in run(db.read_relation_snapshot("123", "456", log_limit=5))[2]
    ]
    now[0] += 9 * DAY
    award(db, svc)
    log_id = (
        db._c()
        .execute("SELECT id FROM favor_log WHERE source='passive_familiarity'")
        .fetchone()[0]
    )
    usage = run(db.daily_usage("123", "456", svc._today().isoformat()))
    for _ in range(4):
        now[0] += 60
        run(svc.undo_log(log_id))
        raw = run(db.get("123", "456"))
        for name in (
            "affinity_maintained_at",
            "familiarity_maintained_at",
            "trust_maintained_at",
            "updated_at",
            "trust_updated_at",
            "favor",
            "trust",
        ):
            assert getattr(raw, name) == getattr(initial, name), name
        latest = run(db.query_logs("123", "456", limit=1))[0]
        assert latest["source"] == "passive_familiarity"
        log_id = latest["id"]
        assert run(db.daily_usage("123", "456", svc._today().isoformat())) == usage
        assert [
            row["id"]
            for row in run(db.read_relation_snapshot("123", "456", log_limit=5))[2]
        ] == evidence_ids
    expected = 10 + (raw.familiarity - 10) * 2 ** (-1 / 30)
    now[0] += DAY
    assert svc._effective_record(raw)["familiarity"] == pytest.approx(expected)
