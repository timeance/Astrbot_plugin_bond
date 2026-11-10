"""独立维护与切换点：固定时钟、真实 SQLite，不调用模型。"""

import asyncio
import sqlite3
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_policy import RelationPolicy
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

START = 1_800_000_000.0
DAY = 86400


def run(value):
    return asyncio.run(value)


@pytest.fixture
def db(tmp_path):
    store = SQLiteBackend(tmp_path / "10001" / "maintenance.db")
    run(store.init())
    yield store
    run(store.close())


def service(db, **overrides):
    opts = dict(
        decay_enabled=True,
        half_life_base=10,
        half_life_growth=1,
        familiarity_decay_enabled=True,
        familiarity_half_life=10,
        trust_decay_enabled=True,
        trust_half_life=10,
        relation_caps=dict.fromkeys(("familiarity", "affinity", "trust"), (100, 100)),
    )
    opts.update(overrides)
    return FavorService(db, LevelTable.from_config(None), **opts)


def seed(svc, user="456", affinity=80):
    return run(
        svc.set_relation(
            "123",
            user,
            familiarity=80,
            affinity=affinity,
            trust=-80,
            event_id="seed-" + user,
        )
    )


def event(svc, name="normal_question", event_id="event"):
    return run(
        svc.apply_relation_event("123", "456", name, event_id=event_id, cooldown_sec=0)
    )


def raw(db):
    return run(db.get("123", "456"))


def state(svc):
    rec = run(svc.get("123", "456"))
    return rec.familiarity, rec.favor, rec.trust


def stable_at(rec):
    return (
        rec.updated_at,
        rec.trust_updated_at,
        rec.affinity_maintained_at,
        rec.trust_maintained_at,
        rec.affinity_floor_reference,
        rec.half_life,
    )


def test_f_only_preserves_all_unrelated_references(db):
    with patch("time.time", return_value=START):
        svc = service(db, half_life_growth=1.3, decay_floor_enabled=True)
        seed(svc)
        initial = raw(db)
    for day in (4, 8, 15, 30):
        with patch("time.time", return_value=START + day * DAY):
            result = event(svc, event_id=f"question-{day}")
            assert result["delta"]["familiarity"] > 0
            assert stable_at(raw(db)) == stable_at(initial)
            assert raw(db).favor == initial.favor
            assert raw(db).trust == initial.trust
    with patch("time.time", return_value=START + 40 * DAY):
        expected = svc._effective_record(initial)
        assert state(svc)[1:] == (
            round(expected["affinity"], 1),
            round(expected["trust"], 1),
        )


@pytest.mark.parametrize(
    "limiter", ["quota", "original_zero", "positive_multiplier", "event_cap"]
)
def test_ineligible_events_do_not_maintain_any_dimension(db, limiter):
    options = {}
    if limiter == "quota":
        options["relation_caps"] = dict.fromkeys(
            ("familiarity", "affinity", "trust"), (0, 0)
        )
    elif limiter == "original_zero":
        options["relation_policy"] = RelationPolicy(
            rules={"normal_question": {"familiarity": 0}}
        )
    elif limiter == "positive_multiplier":
        options["relation_policy"] = RelationPolicy(positive_multiplier=0)
    else:
        options["relation_policy"] = RelationPolicy(event_caps={"normal_question": 0})
    with patch("time.time", return_value=START):
        svc = service(db, **options)
        seed(svc)
        initial = raw(db)
    with patch("time.time", return_value=START + 9 * DAY):
        assert not any(event(svc)["delta"].values())
        assert raw(db) == initial
        assert state(svc) == (60.6, 60.6, -60.6)


def test_score_cap_is_not_quota_exhaustion(db):
    with patch("time.time", return_value=START):
        svc = service(db)
        seed(svc, affinity=100)
    with patch("time.time", return_value=START + 4 * DAY):
        result = event(svc, "friendly")
        assert result["delta"]["affinity"] == 0
        assert raw(db).affinity_maintained_at == START + 4 * DAY
        assert raw(db).trust_maintained_at == START
    with patch("time.time", return_value=START + 8 * DAY):
        assert state(svc)[1] == 100
        assert state(svc)[2] == -65


@pytest.mark.parametrize("name", ["insult", "manipulation", "apology"])
def test_ordinary_negative_events_and_apology_preserve_raw_trust_and_clocks(db, name):
    with patch("time.time", return_value=START):
        svc = service(db)
        seed(svc)
    with patch("time.time", return_value=START + 6 * DAY):
        result = event(svc, name)
        rec = raw(db)
        assert rec.affinity_maintained_at == START + 6 * DAY
        assert result["delta"]["trust"] == 0
        assert result["before"]["trust"] == pytest.approx(-80 * 0.5**0.1)
        assert result["after"]["trust"] == result["before"]["trust"]
        assert rec.trust == -80
        assert rec.trust_updated_at == rec.trust_maintained_at == START
        assert rec.familiarity_maintained_at == (
            START + 6 * DAY if name == "apology" else START
        )


def test_repeated_switches_reads_and_restart_do_not_create_warmth(db):
    with patch("time.time", return_value=START):
        svc = service(db, maintenance_strategy="legacy")
        seed(svc)
        seed(svc, user="789")  # 从不读取的成员也在切换时结算。
    for day, strategy in [(2, "independent"), (7, "legacy"), (12, "independent")]:
        with patch("time.time", return_value=START + day * DAY):
            svc = service(db, maintenance_strategy=strategy)
            assert db._c().execute(
                "SELECT strategy,effective_at FROM maintenance_policy"
            ).fetchone() == (strategy, START + day * DAY)
            assert raw(db).affinity_maintained_at == START
            assert run(db.get("123", "789")).updated_at == START + day * DAY
            before = raw(db)
            for _ in range(3):
                state(svc)
            assert raw(db) == before
            run(db.close())
            run(db.init())
            svc = service(db, maintenance_strategy=strategy)
            assert raw(db) == before
    with patch("time.time", return_value=START + 25 * DAY):
        assert state(svc) == (20, 20, -20)
        assert run(svc.get("123", "789")).favor == 20


def test_switch_preserves_affinity_floor_reference(db):
    with patch("time.time", return_value=START):
        svc = service(db, decay_floor_enabled=True, maintenance_strategy="legacy")
        seed(svc, affinity=100)
        initial = raw(db)
    for day, strategy in [(20, "independent"), (40, "legacy"), (60, "independent")]:
        with patch("time.time", return_value=START + day * DAY):
            svc = service(db, decay_floor_enabled=True, maintenance_strategy=strategy)
            assert raw(db).affinity_floor_reference == 100
    with patch("time.time", return_value=START + 200 * DAY):
        assert state(svc)[1] == round(svc._effective_record(initial)["affinity"], 1)


def test_failed_switch_is_atomic_and_retry_cannot_double_decay(db):
    with patch("time.time", return_value=START):
        svc = service(db, maintenance_strategy="legacy")
        seed(svc)
        initial = raw(db)
    db._c().execute(
        "CREATE TRIGGER reject_switch BEFORE UPDATE ON maintenance_policy BEGIN SELECT RAISE(ABORT,'fixture'); END"
    )
    db._c().commit()
    with patch("time.time", return_value=START + 15 * DAY):
        with pytest.raises(sqlite3.IntegrityError):
            service(db)
        assert raw(db) == initial
        assert (
            db._c().execute("SELECT strategy FROM maintenance_policy").fetchone()[0]
            == "legacy"
        )
        db._c().execute("DROP TRIGGER reject_switch")
        db._c().commit()
        svc = service(db)
        assert state(svc) == (40, 40, -40)
        assert raw(db).affinity_maintained_at == START
    with patch("time.time", return_value=START + 25 * DAY):
        assert state(svc) == (20, 20, -20)


def test_legacy_rollback_restores_f_a_linkage_and_f_only_preserves_trust(db):
    with patch("time.time", return_value=START):
        svc = service(db)
        seed(svc)
    with patch("time.time", return_value=START + 15 * DAY):
        svc = service(db, maintenance_strategy="legacy")
        assert state(svc) == (40, 40, -40)
        before = raw(db)
        event(svc)
        assert raw(db).affinity_maintained_at == START + 15 * DAY
        assert raw(db).familiarity_maintained_at == START + 15 * DAY
        assert raw(db).trust == before.trust
        assert raw(db).trust_updated_at == before.trust_updated_at
        assert raw(db).trust_maintained_at == START
    with patch("time.time", return_value=START + 19 * DAY):
        assert state(svc)[1:] == (40, -30.3)


def test_switch_during_override_preserves_pause_once(db):
    with patch("time.time", return_value=START):
        svc = service(db, maintenance_strategy="legacy")
        seed(svc)
    with patch("time.time", return_value=START + 3 * DAY):
        run(
            db.set_override(
                "123",
                "456",
                dict(familiarity=10, affinity=10, trust=10),
                duration=10 * DAY,
                actor="fixture",
            )
        )
    with patch("time.time", return_value=START + 8 * DAY):
        svc = service(db)
        assert state(svc) == (10, 10, 10)
    with patch("time.time", return_value=START + 13 * DAY):
        assert state(svc) == (80, 80, -80)
        assert raw(db).affinity_maintained_at == START + 10 * DAY
    with patch("time.time", return_value=START + 25 * DAY):
        assert state(svc) == (40, 40, -40)


def test_duplicate_and_failed_event_leave_maintenance_intact(db):
    with patch("time.time", return_value=START):
        svc = service(db)
        seed(svc)
        event(svc)
        initial = raw(db)
    with patch("time.time", return_value=START + DAY):
        assert event(svc)["skipped"] == "duplicate"
        assert raw(db) == initial
        db._c().execute(
            "CREATE TRIGGER reject_event BEFORE INSERT ON favor_log BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
        db._c().commit()
        with pytest.raises(sqlite3.IntegrityError):
            event(svc, "friendly", "other")
        assert raw(db) == initial


def test_v17_upgrade_settles_old_curve_at_switch_without_new_grace(tmp_path):
    from astrbot_plugin_bond.storage.migrations import MIGRATIONS
    from astrbot_plugin_bond.storage.pragma_version import set_user_version

    path = tmp_path / "v17.db"
    conn = sqlite3.connect(path)
    for version in range(1, 18):
        for sql in MIGRATIONS[version]:
            conn.execute(sql)
        set_user_version(conn, version)
        conn.commit()
    conn.execute(
        "INSERT INTO favor(group_id,user_id,favor,familiarity,trust,updated_at,familiarity_updated_at,trust_updated_at) VALUES('123','456',80,80,-80,?,?,?)",
        (START, START, START),
    )
    conn.execute(
        "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,ts,source) VALUES('123','456',80,0,80,?,'admin')",
        (START,),
    )
    conn.commit()
    conn.close()
    db = SQLiteBackend(path)
    try:
        with patch("time.time", return_value=START + 15 * DAY):
            run(db.init())
            svc = service(db)
            assert state(svc) == (40, 40, -40)
            assert raw(db).affinity_maintained_at == START
            assert raw(db).updated_at == START + 15 * DAY
            assert raw(db).affinity_floor_reference == 80
            assert len(run(db.query_logs("123", "456"))) == 1
        with patch("time.time", return_value=START + 25 * DAY):
            assert state(svc) == (20, 20, -20)
    finally:
        run(db.close())


def test_restore_legacy_backup_reapplies_current_policy_at_restore_point(db, tmp_path):
    with patch("time.time", return_value=START):
        svc = service(db, maintenance_strategy="legacy")
        seed(svc)
        backup = tmp_path / "snapshot.db"
        run(db._backup_to(backup))
    with patch("time.time", return_value=START + 10 * DAY):
        svc = service(db)
        event(svc, "friendly")
    with patch("time.time", return_value=START + 15 * DAY):
        run(db._restore_from(backup))
        assert db._c().execute(
            "SELECT strategy,effective_at FROM maintenance_policy"
        ).fetchone() == ("independent", START + 15 * DAY)
        assert state(svc) == (40, 40, -40)
        assert raw(db).affinity_maintained_at == START
        assert len(run(db.query_logs("123", "456"))) == 1
    with patch("time.time", return_value=START + 25 * DAY):
        assert state(svc) == (20, 20, -20)


def test_undo_preview_matches_commit_after_switch_and_does_not_maintain_other_axes(db):
    with patch("time.time", return_value=START):
        svc = service(db, maintenance_strategy="legacy")
        seed(svc)
        event(svc, "friendly")
        log_id = run(db.query_logs("123", "456"))[0]["id"]
    with patch("time.time", return_value=START + 10 * DAY):
        svc = service(db)
    with patch("time.time", return_value=START + 20 * DAY):
        initial_t = raw(db).trust_maintained_at
        preview = run(svc.undo_preview(log_id))
        changed = run(svc.undo_log(log_id))
        assert (changed.familiarity, changed.favor, changed.trust) == tuple(
            preview["relation_after"][k] for k in ("familiarity", "affinity", "trust")
        )
        assert raw(db).trust_maintained_at == initial_t


def test_startup_switches_all_known_bot_scopes_before_later_access(tmp_path):
    import ast
    from unittest.mock import AsyncMock
    from pathlib import Path
    from types import SimpleNamespace

    from astrbot_plugin_bond.services.runtime_service import RuntimeService
    from astrbot_plugin_bond.storage.router import BotScopeStorageRouter

    async def check():
        router = BotScopeStorageRouter(tmp_path)
        with patch("time.time", return_value=START):
            for bot in ("10001", "10002"):
                for mode in ("per_group", "global"):
                    svc = service(
                        await router.get(bot, mode),
                        maintenance_strategy="legacy",
                        scope_mode=mode,
                    )
                    await svc.set_relation(
                        "123",
                        "456",
                        familiarity=80,
                        affinity=80,
                        trust=-80,
                        event_id="seed",
                    )
        await router.close()
        router = BotScopeStorageRouter(tmp_path)
        runtime = RuntimeService(
            router,
            {},
            lambda store, mode: SimpleNamespace(favor=service(store, scope_mode=mode)),
        )
        # 执行实际 initialize 方法体，不用框架桩替换其业务步骤。
        tree = ast.parse(
            (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
        )
        plugin = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "BondPlugin"
        )
        method = next(
            node
            for node in plugin.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "initialize"
        )
        module = ast.Module(body=[method], type_ignores=[])
        namespace = {"logger": SimpleNamespace(info=lambda *_: None)}
        exec(
            compile(ast.fix_missing_locations(module), "startup-fixture", "exec"),
            namespace,
        )
        try:
            with patch("time.time", return_value=START + 15 * DAY):
                await namespace["initialize"](
                    SimpleNamespace(
                        _runtime=runtime,
                        _storage=router,
                        _diagnostics=SimpleNamespace(start=AsyncMock()),
                    )
                )
            with patch("time.time", return_value=START + 25 * DAY):
                for bot in ("10001", "10002"):
                    for mode in ("per_group", "global"):
                        bundle = await runtime.select(bot, mode)
                        assert (await bundle.favor.get("123", "456")).favor == 20
                        store = await router.get(bot, mode)
                        assert (
                            store._c()
                            .execute("SELECT effective_at FROM maintenance_policy")
                            .fetchone()[0]
                            == START + 15 * DAY
                        )
        finally:
            await router.close()

    run(check())


def test_read_only_views_and_switch_do_not_report_a_new_interaction(db):
    with patch("time.time", return_value=START):
        svc = service(db, maintenance_strategy="legacy")
        seed(svc)
    with patch("time.time", return_value=START + 15 * DAY):
        svc = service(db)
        row = run(svc.standings("123"))[0]
        assert row["updated_at"] == START and row["idle_days"] == 15
        event(svc)
        row = run(svc.standings("123"))[0]
        assert row["updated_at"] == START + 15 * DAY and row["idle_days"] == 0
        assert raw(db).affinity_maintained_at == START


def test_f_only_fractional_decay_does_not_create_phantom_deltas_or_charge_quota(db):
    with patch("time.time", return_value=START):
        svc = service(db)
        seed(svc)
    with patch("time.time", return_value=START + 9 * DAY):
        result = event(svc)
        assert result["before"]["affinity"] == pytest.approx(80 * 0.5**0.4)
        assert result["after"]["affinity"] == result["before"]["affinity"]
        assert result["after"]["trust"] == result["before"]["trust"]
        assert result["delta"]["affinity"] == result["delta"]["trust"] == 0
        usage = run(db.daily_usage("123", "456", svc._today().isoformat()))
        assert usage["affinity"] == usage["trust"] == (0, 0)
        log = run(db.query_logs("123", "456"))[0]
        assert (
            log["delta"]
            == log["relation_delta"]["affinity"]
            == log["relation_delta"]["trust"]
            == 0
        )
        assert log["favor_before"] == log["favor_after"]


def test_formal_relation_uses_same_post_switch_anchors(db):
    with patch("time.time", return_value=START):
        svc = service(db, maintenance_strategy="legacy")
        seed(svc)
    with patch("time.time", return_value=START + 15 * DAY):
        svc = service(db)
    with patch("time.time", return_value=START + 19 * DAY):
        expected = run(svc.get_relation("123", "456")).as_dict()
        assert expected == dict(familiarity=30.3, affinity=30.3, trust=-30.3)
        assert run(svc.formal_relation("123", "456")) == expected
        run(
            svc.set_temporary(
                "123",
                "456",
                dict(familiarity=10, affinity=10, trust=10),
                10 * DAY,
                "fixture",
            )
        )
    with patch("time.time", return_value=START + 24 * DAY):
        assert state(svc) == (10, 10, 10)
        assert run(svc.formal_relation("123", "456")) == expected
        svc = service(db, maintenance_strategy="legacy")
        assert run(svc.formal_relation("123", "456")) == expected
    with patch("time.time", return_value=START + 29 * DAY):
        assert run(svc.formal_relation("123", "456")) == expected
        assert run(svc.get_relation("123", "456")).as_dict() == expected


@pytest.mark.parametrize("cap, expected", [(0.05, True), (0, False)])
def test_synthetic_trust_partial_quota_precision_maintains_but_exhaustion_does_not(
    db, cap, expected
):
    with patch("time.time", return_value=START):
        svc = service(
            db,
            # 合成 T 请求用于隔离额度与舍入数学，不代表自动评分证据。
            relation_policy=RelationPolicy(rules={"insult": {"trust": -1.5}}),
            relation_caps={
                "familiarity": (0, 0),
                "affinity": (0, 0),
                "trust": (0, cap),
            },
        )
        seed(svc)
    with patch("time.time", return_value=START + 4 * DAY):
        result = event(svc, "insult")
        assert result["delta"]["trust"] == 0
        assert raw(db).trust_maintained_at == (START + 4 * DAY if expected else START)
        detail = result["maintenance"]
        assert detail["maintenance_qualified"]["trust"] is expected
        assert detail["maintenance_reason"]["trust"] == (
            "precision" if expected else "quota"
        )
        assert run(db.daily_usage("123", "456", svc._today().isoformat()))["trust"] == (
            0,
            0,
        )


def test_economy_precision_zero_keeps_qualified_affinity_maintenance(db):
    from astrbot_plugin_bond.core.level_economy import EconomyConfig

    with patch("time.time", return_value=START):
        svc = service(db, economy=EconomyConfig(), relation_policy=RelationPolicy())
        seed(svc, affinity=99)
    for index in range(1, 5):
        with patch("time.time", return_value=START + index * 3600):
            result = event(svc, "thanks", f"thanks-{index}")
            assert raw(db).affinity_maintained_at == START + index * 3600
    assert result["maintenance"]["adjusted"]["affinity"] == pytest.approx(0.025)
    assert result["delta"]["affinity"] == 0
    assert result["maintenance"]["maintenance_reason"]["affinity"] == "precision"


def test_detail_and_list_age_use_same_maintenance_after_switch(db):
    with patch("time.time", return_value=START):
        svc = service(db, maintenance_strategy="legacy")
        seed(svc)
    with patch("time.time", return_value=START + 15 * DAY):
        svc = service(db)
        assert svc.idle_days(run(svc.get("123", "456"))) == 15
    with patch("time.time", return_value=START + 19 * DAY):
        event(svc)
        rec = run(svc.get("123", "456"))
        assert rec.updated_at == START + 15 * DAY
        assert svc.idle_days(rec) == run(svc.standings("123"))[0]["idle_days"] == 0


def test_maintenance_diagnostics_use_committed_transaction_decision(db, tmp_path):
    from astrbot_plugin_bond.core.taskregistry import TaskRegistry
    from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder

    async def check():
        recorder = DiagnosticRecorder(
            tmp_path / "diagnostics", TaskRegistry(), enabled=True
        )
        try:
            with patch("time.time", return_value=START):
                svc = service(db, diagnostics=recorder)
                await svc.set_relation(
                    "123",
                    "456",
                    familiarity=80,
                    affinity=80,
                    trust=-80,
                    event_id="seed",
                )
            with patch("time.time", return_value=START + DAY):
                result = await svc.apply_relation_event(
                    "123", "456", "normal_question", event_id="a" * 64, cooldown_sec=0
                )
                exported = await recorder.export("10001", "per_group")
            row = exported["rows"][-1]["fields"]
            for key, value in result["maintenance"].items():
                assert row[key] == value
            assert row["maintenance_applied"] == dict(
                familiarity=True, affinity=False, trust=False
            )
            assert (
                exported["missing"]["filtered_fields"]
                == exported["missing"]["corrupt_lines"]
                == 0
            )
        finally:
            await recorder.close()

    run(check())


@pytest.mark.parametrize("dimension", ["familiarity", "affinity", "trust"])
@pytest.mark.parametrize(
    "requested, cap, expected_reason",
    [(0.3, 0, "rule_zero"), (0, 1, "original_zero")],
)
def test_zero_event_cap_preserves_original_intent_in_diagnostics(
    db, tmp_path, dimension, requested, cap, expected_reason
):
    from astrbot_plugin_bond.core.taskregistry import TaskRegistry
    from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder

    async def check():
        recorder = DiagnosticRecorder(
            tmp_path / "diagnostics", TaskRegistry(), enabled=True
        )
        configured = dict.fromkeys(("familiarity", "affinity", "trust"), 0)
        configured[dimension] = requested
        # 合成维度请求只验证限幅诊断，含 T 的分支不代表自动评分证据。
        policy = RelationPolicy(
            rules={"friendly": configured}, event_caps={"friendly": {dimension: cap}}
        )
        try:
            with patch("time.time", return_value=START):
                svc = service(db, relation_policy=policy, diagnostics=recorder)
                await svc.set_relation(
                    "123",
                    "456",
                    familiarity=80,
                    affinity=80,
                    trust=-80,
                    event_id="seed",
                )
                stored_before = await db.get("123", "456")
            with patch("time.time", return_value=START + 4 * DAY):
                day = svc._today().isoformat()
                usage_before = await db.daily_usage("123", "456", day)
                result = await svc.apply_relation_event(
                    "123",
                    "456",
                    "friendly",
                    intensity=2,
                    event_id="zero-rule",
                    cooldown_sec=0,
                )
                stored_after = await db.get("123", "456")
                usage_after = await db.daily_usage("123", "456", day)
                exported = await recorder.export("10001", "per_group")
            assert result["skipped"] is None
            assert result["delta"] == dict.fromkeys(configured, 0)
            assert stored_after == stored_before  # 分值、维护/结算锚及半衰期都不动。
            assert usage_after == usage_before
            detail = result["maintenance"]
            assert detail["intent"][dimension] == pytest.approx(requested * 2)
            assert (
                detail["adjusted"] == detail["allowed"] == dict.fromkeys(configured, 0)
            )
            assert detail["maintenance_reason"][dimension] == expected_reason
            assert not any(detail["maintenance_qualified"].values())
            assert not any(detail["maintenance_applied"].values())
            assert detail["maintenance_before"] == detail["maintenance_after"]
            fields = exported["rows"][-1]["fields"]
            for key, value in detail.items():
                assert fields[key] == value
            assert exported["missing"]["filtered_fields"] == 0
            assert exported["missing"]["corrupt_lines"] == 0
        finally:
            await recorder.close()

    run(check())
