"""关系保温期：固定时钟、真实服务与 SQLite 的合成回归。"""

import asyncio
import sqlite3
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.core.decay import effective_favor
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

START = 1_800_000_000.0
DAY = 86400


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def db(tmp_path):
    store = SQLiteBackend(tmp_path / "grace.db")
    run(store.init())
    yield store
    run(store.close())


def service(db, **kwargs):
    options = dict(
        maintenance_strategy="legacy",
        decay_enabled=True,
        half_life_base=10,
        half_life_growth=1,
        familiarity_decay_enabled=True,
        familiarity_half_life=10,
        trust_decay_enabled=True,
        trust_half_life=10,
        relation_caps=dict.fromkeys(("familiarity", "affinity", "trust"), (0, 0)),
    )
    options.update(kwargs)
    return FavorService(db, LevelTable.from_config(None), **options)


def seed(svc, group="123", *, event_id="seed"):
    return run(
        svc.set_relation(
            group, "456", familiarity=80, affinity=80, trust=-80, event_id=event_id
        )
    )


def event(svc, event_id, *, group="123", **kwargs):
    return run(
        svc.apply_relation_event(
            group, "456", "normal_question", event_id=event_id, cooldown_sec=0, **kwargs
        )
    )


def raw(db, svc, group="123"):
    return run(db.get(svc.group_key(group), "456"))


def anchors(record):
    return record.familiarity_updated_at, record.updated_at, record.trust_updated_at


def values(record):
    return record.familiarity, record.favor, record.trust


@pytest.mark.parametrize(
    "values,options,expected",
    [
        ((50, 0, 0), {}, ["familiarity"]),
        ((50, 30, 0), {}, ["familiarity", "affinity"]),
        ((50, 30, -50), {}, ["familiarity", "affinity", "trust"]),
        ((0, 0, -50), {}, ["trust"]),
        ((10, 0, -20), {}, []),
        ((50, 30, -50), {"trust_decay_enabled": False}, ["familiarity", "affinity"]),
    ],
)
def test_overview_decay_axes_use_current_conditions_before_rounding(
    db, values, options, expected
):
    svc = service(
        db, decay_grace_days=5, familiarity_baseline=10, trust_baseline=20, **options
    )
    with patch("time.time", return_value=START):
        run(
            svc.set_relation(
                "123",
                "456",
                familiarity=values[0],
                affinity=values[1],
                trust=values[2],
                event_id="seed",
            )
        )
    with patch("time.time", return_value=START + 5 * DAY):
        assert run(svc.standings("123"))[0]["decaying_dimensions"] == []
    with patch("time.time", return_value=START + 5 * DAY + 1):
        row = run(svc.standings("123"))[0]
        assert row["decaying_dimensions"] == expected
        assert row["decayed"] == bool(expected)
        # 刚进入衰减，显示值未必已变化；负 T 变大仍标记 T 衰减。
        assert row["familiarity"] == values[0]
        assert row["trust"] == values[2]


def test_overview_familiarity_maintenance_does_not_hide_trust_decay(db):
    svc = service(
        db,
        decay_grace_days=5,
        maintenance_strategy="independent",
        relation_caps=dict.fromkeys(("familiarity", "affinity", "trust"), (100, 100)),
    )
    with patch("time.time", return_value=START):
        seed(svc)
    with patch("time.time", return_value=START + 6 * DAY):
        event(svc, "only-familiarity")
        row = run(svc.standings("123"))[0]
        assert row["decaying_dimensions"] == ["affinity", "trust"]
        run(
            svc.set_temporary(
                "123", "456", dict(familiarity=80, affinity=80, trust=80), 60, "fixture"
            )
        )
        row = run(svc.standings("123"))[0]
        assert row["temporary"]["active"]
        assert row["decaying_dimensions"] == []
        assert not row["decayed"]


def test_overview_stops_reporting_decay_at_affinity_floor(db):
    svc = service(db, decay_floor_enabled=True)
    with patch("time.time", return_value=START):
        run(
            svc.set_relation(
                "123", "456", familiarity=0, affinity=95, trust=0, event_id="seed"
            )
        )
    with patch("time.time", return_value=START + 365 * DAY):
        row = run(svc.standings("123"))[0]
        assert row["affinity"] == svc._decay_floor(95, 0)
        assert row["decaying_dimensions"] == []


@pytest.mark.parametrize("grace", [0, 2.5, 5])
@pytest.mark.parametrize("stored,baseline", [(80, 20), (-80, -20)])
def test_only_excess_time_decays_with_fractional_days(grace, stored, baseline):
    options = dict(half_life=10, baseline=baseline, rounded=False, grace_days=grace)
    assert effective_favor(stored, START, START + grace * DAY, **options) == stored
    assert effective_favor(stored, START, START + (grace + 10) * DAY, **options) == (
        baseline + (stored - baseline) / 2
    )
    assert effective_favor(
        stored, START, START + (grace + 0.5) * DAY, **options
    ) == pytest.approx(baseline + (stored - baseline) * 0.5**0.05)


@pytest.mark.parametrize("grace", [-1, float("nan"), float("inf"), True, "5"])
def test_invalid_grace_rejected_even_with_decay_disabled(db, grace):
    with pytest.raises(ValueError, match="关系保温期"):
        service(db, decay_enabled=False, decay_grace_days=grace)
    with pytest.raises(ValueError, match="关系保温期"):
        effective_favor(80, START, START, half_life=10, baseline=0, grace_days=grace)


@pytest.mark.parametrize("days,expected", [(0, 80), (4, 80), (5, 80), (15, 40)])
def test_default_five_days_all_dimensions_and_read_only(db, days, expected):
    svc = service(db)
    with patch("time.time", return_value=START):
        seed(svc)
    with patch("time.time", return_value=START + days * DAY):
        assert values(run(svc.get("123", "456"))) == (expected, expected, -expected)
        assert values(raw(db, svc)) == (80, 80, -80)
        assert anchors(raw(db, svc)) == (START,) * 3
        assert len(run(svc.query_logs("123", "456"))) == 1


def test_zero_grace_and_each_dimensions_half_life(db):
    svc = service(db, decay_grace_days=0, familiarity_half_life=20, trust_half_life=40)
    with patch("time.time", return_value=START):
        seed(svc)
    with patch("time.time", return_value=START + 40 * DAY):
        assert values(run(svc.get("123", "456"))) == (20, 5, -40)


def test_legacy_capped_interactions_keep_f_a_warm_without_maintaining_trust(db):
    svc = service(db)
    with patch("time.time", return_value=START):
        seed(svc)
    for day in (4, 8):
        with patch("time.time", return_value=START + day * DAY):
            result = event(svc, f"capped-{day}")
            assert result["skipped"] is None
            assert result["delta"] == dict.fromkeys(result["delta"], 0)
            assert values(raw(db, svc)) == (80, 80, -80)
            assert anchors(raw(db, svc)) == (
                START + day * DAY,
                START + day * DAY,
                START,
            )
            assert raw(db, svc).trust_maintained_at == START
    with patch("time.time", return_value=START + 18 * DAY):
        result = event(svc, "after-decay")
        settled = 80 * 0.5**0.5
        effective_t = -80 * 0.5**1.3
        assert result["before"] == pytest.approx(
            dict(familiarity=settled, affinity=settled, trust=effective_t)
        )
        assert result["after"]["trust"] == pytest.approx(effective_t)
        assert values(raw(db, svc)) == pytest.approx((settled, settled, -80))
        assert anchors(raw(db, svc)) == (START + 18 * DAY, START + 18 * DAY, START)
        assert raw(db, svc).trust_maintained_at == START
        usage = run(db.daily_usage("123", "456", svc._today().isoformat()))
        assert all(pair == (0, 0) for pair in usage.values())
        assert not any(run(svc.query_logs("123", "456"))[0]["relation_delta"].values())
    run(db.close())
    run(db.init())
    svc = service(db)
    with patch("time.time", return_value=START + 23 * DAY):
        assert values(run(svc.get("123", "456"))) == (56.6, 56.6, -23)
    with patch("time.time", return_value=START + 28 * DAY):
        assert values(run(svc.get("123", "456"))) == (40, 40, -16.2)
        assert raw(db, svc).trust == -80
        assert raw(db, svc).trust_updated_at == START
        assert raw(db, svc).trust_maintained_at == START


def test_legacy_f_only_reanchors_f_a_and_preserves_trust_without_charging_decay(db):
    svc = service(
        db, relation_caps=dict(familiarity=(5, 0), affinity=(10, 8), trust=(2, 8))
    )
    with patch("time.time", return_value=START):
        seed(svc)
    with patch("time.time", return_value=START + 15 * DAY):
        result = event(svc, "f-only")
        assert result["before"] == dict(familiarity=40, affinity=40, trust=-40)
        assert result["delta"]["familiarity"] == pytest.approx(0.08)
        assert result["delta"]["affinity"] == result["delta"]["trust"] == 0
        assert values(raw(db, svc)) == pytest.approx((40.08, 40, -80))
        assert anchors(raw(db, svc)) == (START + 15 * DAY, START + 15 * DAY, START)
        assert raw(db, svc).trust_maintained_at == START
        usage = run(db.daily_usage("123", "456", svc._today().isoformat()))
        assert usage["familiarity"][0] == result["delta"]["familiarity"]
        assert usage["affinity"] == usage["trust"] == (0, 0)


def test_duplicates_cooldown_invalid_and_failed_transaction_leave_anchors(db):
    svc = service(db)
    with patch("time.time", return_value=START):
        seed(svc)
        event(svc, "once")
    with patch("time.time", return_value=START + DAY):
        assert event(svc, "once")["skipped"] == "duplicate"
        assert (
            run(
                svc.apply_relation_event(
                    "123", "456", "thanks", event_id="cooldown", cooldown_sec=2 * DAY
                )
            )["skipped"]
            == "cooldown"
        )
        with pytest.raises(ValueError):
            run(svc.apply_relation_event("123", "456", "invalid", event_id="invalid"))
        assert anchors(raw(db, svc)) == (START,) * 3
    db._c().execute(
        "CREATE TRIGGER reject_grace BEFORE INSERT ON favor_log BEGIN SELECT RAISE(ABORT,'fixture'); END"
    )
    db._c().commit()
    with patch("time.time", return_value=START + 15 * DAY):
        with pytest.raises(sqlite3.IntegrityError):
            event(svc, "failed")
        assert values(raw(db, svc)) == (80, 80, -80)
        assert anchors(raw(db, svc)) == (START,) * 3
        assert run(db.last_event_at("123", "456", "relation")) == START
        db._c().execute("DROP TRIGGER reject_grace")
        db._c().commit()
        assert event(svc, "failed")["skipped"] is None
        assert values(raw(db, svc)) == (40, 40, -80)
        assert anchors(raw(db, svc)) == (START + 15 * DAY, START + 15 * DAY, START)
        assert raw(db, svc).trust_maintained_at == START
        assert values(run(svc.get("123", "456"))) == (40, 40, -40)


def test_profile_nickname_page_reads_do_not_extend_grace(db):
    svc = service(db)
    with patch("time.time", return_value=START):
        seed(svc)
    with patch("time.time", return_value=START + 4 * DAY):
        run(svc.touch_nickname("123", "456", "合成昵称"))
        run(svc.update_member_names("123", "456", "合成昵称", "合成群名片"))
        run(
            svc.set_profile(
                "123", "456", impression="合成印象", tags=[], actor="fixture-admin"
            )
        )
        run(svc.ranking("123"))
        run(svc.standings("123"))
        assert anchors(raw(db, svc)) == (START,) * 3
    with patch("time.time", return_value=START + 15 * DAY):
        assert values(run(svc.get("123", "456"))) == (40, 40, -40)


def test_manual_affinity_set_preserves_other_clocks_and_full_set_reanchors(db):
    svc = service(db)
    with patch("time.time", return_value=START):
        seed(svc)
    with patch("time.time", return_value=START + 4 * DAY):
        run(svc.set_favor("123", "456", 60))
        assert anchors(raw(db, svc)) == (START, START + 4 * DAY, START)
    with patch("time.time", return_value=START + 15 * DAY):
        assert values(run(svc.get("123", "456"))) == (40, 39.6, -40)
        seed(svc, event_id="manual")
        assert anchors(raw(db, svc)) == (START + 15 * DAY,) * 3
    with patch("time.time", return_value=START + 20 * DAY):
        assert values(run(svc.get("123", "456"))) == (80, 80, -80)


def test_temporary_relation_pauses_remaining_grace_without_new_grace(db):
    svc = service(db)
    with patch("time.time", return_value=START):
        seed(svc)
    with patch("time.time", return_value=START + 4 * DAY):
        run(
            svc.set_temporary(
                "123",
                "456",
                dict(familiarity=99, affinity=99, trust=99),
                10 * DAY,
                "fixture-admin",
            )
        )
    with patch("time.time", return_value=START + 10 * DAY):
        assert run(svc.formal_relation("123", "456")) == dict(
            familiarity=80, affinity=80, trust=-80
        )
    with patch("time.time", return_value=START + 15 * DAY):
        # 列表首次读到覆盖到期时，应使用平移后的时钟，不能误报正在衰减。
        assert run(svc.standings("123"))[0]["decaying_dimensions"] == []
        assert values(run(svc.get("123", "456"))) == (80, 80, -80)
        assert anchors(raw(db, svc)) == (START + 10 * DAY,) * 3
    with patch("time.time", return_value=START + 25 * DAY):
        assert values(run(svc.get("123", "456"))) == (40, 40, -40)


@pytest.mark.parametrize("mode", ["per_group", "global"])
def test_group_and_global_read_write_use_same_grace_and_isolation(tmp_path, mode):
    router = BotScopeStorageRouter(tmp_path)
    try:
        db = run(router.get("10001", mode))
        svc = service(db, scope_mode=mode)
        with patch("time.time", return_value=START):
            seed(svc, "123")
            if mode == "per_group":
                seed(svc, "124", event_id="seed-other")
        with patch("time.time", return_value=START + 4 * DAY):
            event(svc, "renew", group="124")
        with patch("time.time", return_value=START + 15 * DAY):
            expected = 52.8 if mode == "global" else 40
            assert values(run(svc.get("123", "456"))) == (expected, expected, -40)
            assert raw(db, svc).trust == -80
            assert raw(db, svc).trust_updated_at == START
            assert raw(db, svc).trust_maintained_at == START
            other = service(run(router.get("20002", mode)), scope_mode=mode)
            assert values(run(other.get("123", "456"))) == (0, 0, 0)
    finally:
        run(router.close())


def test_undo_zero_delta_does_not_grant_another_grace_period(db):
    svc = service(db)
    with patch("time.time", return_value=START):
        seed(svc)
        event(svc, "zero")
        log_id = run(svc.query_logs("123", "456"))[0]["id"]
    with patch("time.time", return_value=START + 15 * DAY):
        run(svc.undo_log(log_id, actor="fixture-admin"))
        assert anchors(raw(db, svc)) == (START,) * 3
        assert values(run(svc.get("123", "456"))) == (40, 40, -40)
    with patch("time.time", return_value=START + 25 * DAY):
        assert values(run(svc.get("123", "456"))) == (20, 20, -20)
