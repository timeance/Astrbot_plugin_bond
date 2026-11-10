"""持续尊重边界的持久证据、事务、撤销和隔离，使用合成来源。"""

import asyncio
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.core.assessment import (
    Interaction,
    Verdict,
    parse_verdict,
    verify_trust_evidence,
)
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_profiles import build_relation_profile
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage import migrations
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

START = 1_800_000_000
DAY = 86400
BOUNDARY = "请勿公开我的私人信息，分享前请先匿名化。"


def proof(
    number,
    *,
    kind="boundary_respected",
    group="789",
    bot="123",
    text=None,
    behavior_id=None,
    boundary="response:boundary",
    observed_at=None,
):
    text = text or f"这是第{number}份已匿名化的公开材料。"
    current = f"action-{number}"
    interaction = Interaction(bot, group, "456", "test", current, text)
    behavior_id = behavior_id or current
    verdict = Verdict(kind, 0.95, text, True, boundary, BOUNDARY, behavior_id, text)
    turns = [dict(role="bot", message_id=boundary, text=BOUNDARY, ts=START - DAY)]
    if behavior_id != current:
        turns.append(
            dict(
                role="user",
                message_id=behavior_id,
                text=text,
                ts=observed_at or time.time(),
            )
        )
    payload = json.dumps(
        dict(recent_turns=turns, trusted_metadata=dict(observed_at=time.time()))
    )
    return verify_trust_evidence(interaction, verdict, verdict, payload)


async def setup(tmp_path, *, mode="realistic", bot="123", scope="per_group", caps=None):
    db = SQLiteBackend(tmp_path / bot / "trust.db")
    await db.init()
    policy, limits = build_relation_profile(mode, caps=caps)
    favor = FavorService(
        db,
        LevelTable.from_config(None),
        relation_policy=policy,
        relation_caps=limits,
        scope_mode=scope,
    )
    return db, favor


async def apply(favor, item, **kwargs):
    return await favor.apply_relation_event(
        item.group_id,
        item.user_id,
        item.event_type,
        event_id=item.event_id,
        message=item.behavior_excerpt,
        trust_evidence=item,
        cooldown_sec=0,
        **kwargs,
    )


def records(db):
    return (
        db._c()
        .execute(
            "SELECT event_id,consumed_by,invalidated,source_log_id FROM trust_evidence ORDER BY rowid"
        )
        .fetchall()
    )


@pytest.mark.parametrize("mode,reward", [("realistic", 1), ("galgame", 2)])
def test_three_distinct_actions_same_boundary_across_restart_and_five_day_cooldown(
    tmp_path, mode, reward
):
    async def run():
        db, favor = await setup(tmp_path, mode=mode)
        for number, days in [(1, 0), (2, 7)]:
            with patch("time.time", return_value=START + days * DAY):
                assert (await apply(favor, proof(number)))["delta"]["trust"] == 0
                assert (await db.get("789", "456")).trust_maintained_at is None
        await db.close()
        db, favor = await setup(tmp_path, mode=mode)
        with patch("time.time", return_value=START + 14 * DAY):
            assert (await apply(favor, proof(3), intensity=999))["delta"][
                "trust"
            ] == reward
            assert len({row[1] for row in records(db)}) == 1
            assert all(row[1] for row in records(db))
        for number in (4, 5, 6):
            with patch("time.time", return_value=START + 14 * DAY + number):
                assert (await apply(favor, proof(number)))["delta"]["trust"] == 0
        with patch("time.time", return_value=START + 19 * DAY - 1):
            assert (await apply(favor, proof(7)))["delta"]["trust"] == 0
        with patch("time.time", return_value=START + 19 * DAY):
            assert (await apply(favor, proof(8)))["delta"]["trust"] == reward
            assert sum(bool(row[1]) for row in records(db)) == 6
            assert (await favor.get_relation("789", "456")).trust == 2 * reward
        await db.close()

    asyncio.run(run())


def test_window_expires_and_old_actions_do_not_reenter(tmp_path):
    async def run():
        db, favor = await setup(tmp_path)
        with patch("time.time", return_value=START):
            first = proof(1)
            await apply(favor, first)
        with patch("time.time", return_value=START + 14 * DAY + 1):
            await apply(favor, proof(2))
            assert (await apply(favor, proof(3)))["delta"]["trust"] == 0
            assert records(db)[0][2] == 1
            assert (await apply(favor, proof(4)))["delta"]["trust"] == 1
            assert (await apply(favor, first))["skipped"] == "duplicate"
        await db.close()

    asyncio.run(run())


def test_restatements_and_other_trust_categories_share_action_dedup(tmp_path):
    async def run():
        db, favor = await setup(tmp_path)
        with patch("time.time", return_value=START):
            first = proof(1)
            await apply(favor, first)
            assert (await apply(favor, proof(2, text=first.behavior_excerpt)))[
                "skipped"
            ]
            assert (
                await apply(
                    favor, proof(3, behavior_id="action-1", text=first.behavior_excerpt)
                )
            )["skipped"]
            assert (
                await apply(
                    favor,
                    proof(
                        4,
                        kind="repair_action",
                        behavior_id="action-1",
                        text=first.behavior_excerpt,
                    ),
                )
            )["skipped"]
            positive = proof(
                5, kind="verified_correction", boundary="response:correction"
            )
            assert (await apply(favor, positive))["delta"]["trust"] == 0.5
            assert (
                await apply(
                    favor,
                    proof(6, behavior_id="action-5", text=positive.behavior_excerpt),
                )
            )["skipped"]
            assert len(records(db)) == 2
        await db.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "negative",
    [
        "boundary_violation",
        "repeated_pressure",
        "deliberate_deception",
        "severe_manipulation",
    ],
)
def test_negative_clears_unclaimed_window_even_at_daily_cap_and_undo_keeps_it_cleared(
    tmp_path, negative
):
    async def run():
        db, favor = await setup(tmp_path, caps={"trust": (2, 0)})
        with patch("time.time", return_value=START):
            await apply(favor, proof(1))
            await apply(favor, proof(2))
        with patch("time.time", return_value=START + 1):
            item = proof(9, kind=negative, boundary="response:violation")
            assert (await apply(favor, item))["delta"]["trust"] == 0
            await favor.undo_log(records(db)[-1][3])
            assert all(row[2] for row in records(db))
        with patch("time.time", return_value=START + 2):
            assert (await apply(favor, proof(3)))["delta"]["trust"] == 0
            assert (await apply(favor, proof(4)))["delta"]["trust"] == 0
            assert (await apply(favor, proof(5)))["delta"]["trust"] == 1
        await db.close()

    asyncio.run(run())


@pytest.mark.parametrize("index", [0, 2])
def test_source_or_reward_revocation_keeps_consumption_cooldown_and_usage(
    tmp_path, index
):
    async def run():
        db, favor = await setup(tmp_path)
        with patch("time.time", return_value=START):
            items = [proof(n) for n in (1, 2, 3)]
            for item in items:
                await apply(favor, item)
            rows = records(db)
            await favor.undo_log(rows[index][3])
            # 低层重复标记保持幂等，服务已经反向的分数不能再反向。
            await db.mark_reversed(rows[index][3])
            await db.mark_reversed(rows[index][3])
            assert (await favor.get_relation("789", "456")).trust == 0
            assert all(row[1] and row[2] for row in records(db))
            assert db._c().execute("SELECT trust_up FROM daily_gain").fetchone()[0] == 1
            for item in items:
                assert (await apply(favor, item))["skipped"] == "duplicate"
            for number in (4, 5, 6):
                assert (await apply(favor, proof(number)))["delta"]["trust"] == 0
        with patch("time.time", return_value=START + 5 * DAY):
            assert (await apply(favor, proof(7)))["delta"]["trust"] == 1
        await db.close()

    asyncio.run(run())


def test_pending_source_revocation_prevents_three_action_reward(tmp_path):
    async def run():
        db, favor = await setup(tmp_path)
        with patch("time.time", return_value=START):
            await apply(favor, proof(1))
            await favor.undo_log(records(db)[0][3])
            await apply(favor, proof(2))
            assert (await apply(favor, proof(3)))["delta"]["trust"] == 0
        await db.close()

    asyncio.run(run())


def test_reward_uses_existing_daily_limit_and_does_not_consume_without_gain(tmp_path):
    async def run():
        db, favor = await setup(tmp_path, caps={"trust": (0, 8)})
        with patch("time.time", return_value=START):
            for n in (1, 2, 3):
                assert (await apply(favor, proof(n)))["delta"]["trust"] == 0
            assert all(not row[1] for row in records(db))
            assert (
                not db._c()
                .execute("SELECT 1 FROM cooldown WHERE key='boundary_respected'")
                .fetchone()
            )
            policy, caps = build_relation_profile("realistic", caps={"trust": (0.5, 8)})
            favor = FavorService(
                db,
                LevelTable.from_config(None),
                relation_policy=policy,
                relation_caps=caps,
            )
            assert (await apply(favor, proof(4)))["delta"]["trust"] == 0.5
            assert sum(bool(row[1]) for row in records(db)) == 3
        await db.close()

    asyncio.run(run())


def test_transaction_rolls_back_evidence_consumption_usage_and_score(tmp_path):
    async def run():
        db, favor = await setup(tmp_path)
        with patch("time.time", return_value=START):
            await apply(favor, proof(1))
            await apply(favor, proof(2))
            db._c().execute(
                "CREATE TEMP TRIGGER fail_trust BEFORE INSERT ON favor_log BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
            )
            with pytest.raises(sqlite3.IntegrityError):
                await apply(favor, proof(3))
            assert len(records(db)) == 2
            assert all(not row[1] for row in records(db))
            assert (await favor.get_relation("789", "456")).trust == 0
            assert db._c().execute("SELECT trust_up FROM daily_gain").fetchone()[0] == 0
            db._c().execute("DROP TRIGGER fail_trust")
            assert (await apply(favor, proof(3)))["delta"]["trust"] == 1
        await db.close()

    asyncio.run(run())


def test_separate_connections_cannot_reward_same_batch_twice(tmp_path):
    async def run():
        first, favor = await setup(tmp_path)
        second, other = await setup(tmp_path)
        with patch("time.time", return_value=START):
            await apply(favor, proof(1))
            await apply(favor, proof(2))
            items = [proof(3), proof(4)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda pair: asyncio.run(apply(*pair)),
                        zip((favor, other), items),
                    )
                )
            assert sum(result["delta"]["trust"] for result in results) == 1
            assert sum(bool(row[1]) for row in records(first)) == 3
        await second.close()
        await first.close()

    asyncio.run(run())


def test_temporary_override_and_expired_inflight_revision_block_evidence(tmp_path):
    async def run():
        db, favor = await setup(tmp_path)
        with patch("time.time", return_value=START):
            await apply(favor, proof(1))
            revision = (await favor.temporary_state("789", "456"))["revision"]
            await db.set_override(
                "789", "456", dict(familiarity=0, affinity=0, trust=0), 10, "tester"
            )
            with pytest.raises(ValueError):
                await apply(favor, proof(2), expected_revision=revision)
            assert len(records(db)) == 1
        with patch("time.time", return_value=START + 11):
            with pytest.raises(ValueError):
                await apply(favor, proof(2), expected_revision=revision)
            assert len(records(db)) == 1
            await apply(favor, proof(2))
        await db.close()

    asyncio.run(run())


def test_bot_group_isolation_global_cooldown_and_context_privacy(tmp_path):
    async def run():
        db, favor = await setup(tmp_path, scope="global")
        other, other_favor = await setup(tmp_path, bot="124", scope="global")
        with patch("time.time", return_value=START):
            for n in (1, 2):
                await apply(favor, proof(n))
            assert (await apply(favor, proof(3, group="790")))["delta"]["trust"] == 1
            for n in (4, 5, 6):
                assert (await apply(favor, proof(n, group="790")))["delta"][
                    "trust"
                ] == 0
            context = await db.boundary_evidence_context(
                favor.group_key("789"), "456", origin_group_id="790"
            )
            assert all(
                item["behavior_id"] not in {"action-1", "action-2"} for item in context
            )
            for n in (1, 2):
                assert (await apply(other_favor, proof(n, bot="124")))["delta"][
                    "trust"
                ] == 0
            with pytest.raises(ValueError):
                await apply(other_favor, proof(7))
        await other.close()
        await db.close()

    asyncio.run(run())


def test_schema20_preserves_history_and_migration_failure_rolls_back():
    conn = sqlite3.connect(":memory:")
    with patch.object(migrations, "SCHEMA_VERSION", 19):
        migrations.migrate(conn)
    conn.execute(
        "INSERT INTO favor(group_id,user_id,favor,updated_at,trust) VALUES('789','456',3,1,7)"
    )
    conn.commit()
    original = list(migrations.MIGRATIONS[20])
    with patch.dict(
        migrations.MIGRATIONS, {20: original + ["SELECT * FROM absent_table"]}
    ):
        with pytest.raises(sqlite3.OperationalError):
            migrations.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 19
    assert not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='trust_evidence'"
    ).fetchone()
    migrations.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
    assert conn.execute("SELECT favor,trust FROM favor").fetchone() == (3, 7)
    assert conn.execute("SELECT COUNT(*) FROM trust_evidence").fetchone()[0] == 0
    conn.close()


def test_assessment_recovers_boundary_after_restart_and_undo_keeps_call_budget(
    tmp_path,
):
    from test_assessment import Provider, setup as assessment_setup, verdict
    from astrbot_plugin_bond.services.assessment_service import AssessmentCapacity

    async def run():
        small, large = Provider(), Provider()
        db, favor, service, _ = await assessment_setup(
            tmp_path, small, large, large_provider_id="large"
        )
        with patch("time.time", return_value=START - 1):
            initial = Interaction(
                "123", "789", "456", "test", "boundary", "请说明分享要求"
            )
            service.remember_admitted_interaction(initial)
            service.remember_bot_reply(initial, BOUNDARY)
        for number, days in [(1, 0), (2, 8), (3, 13)]:
            with patch("time.time", return_value=START + days * DAY):
                if number > 1:
                    await db.close()
                    await db.init()
                    service.capacity = AssessmentCapacity(2)
                current = Interaction(
                    "123",
                    "789",
                    "456",
                    "test",
                    f"material-{number}",
                    f"已匿名化的第{number}份公开材料如下。",
                )
                small.result = large.result = verdict(
                    text=current.text,
                    kind="boundary_respected",
                    reference_message_id="response:boundary",
                    reference_excerpt=BOUNDARY,
                    behavior_message_id=current.message_id,
                    behavior_excerpt=current.text,
                )
                if number > 1:
                    snapshot = json.loads(await service._snapshot(current))
                    assert snapshot["boundary_evidence"]
                    assert any(
                        t["message_id"] == "response:boundary"
                        for t in snapshot["recent_turns"]
                    )
                assert await service.assess(current, directed=True) == (
                    "accepted" if number == 3 else "unchanged"
                )
        with patch("time.time", return_value=START + 13 * DAY):
            assert (await favor.get_relation("789", "456")).trust == 1
            calls = (
                db._c().execute("SELECT COUNT(*) FROM assessment_call").fetchone()[0]
            )
            assert calls == 6
            await favor.undo_log(records(db)[0][3])
            assert (await favor.get_relation("789", "456")).trust == 0
            assert (
                db._c().execute("SELECT COUNT(*) FROM assessment_call").fetchone()[0]
                == calls
            )
        await db.close()

    asyncio.run(run())


def test_persisted_source_revoked_between_snapshot_and_commit_is_rechecked(tmp_path):
    from test_assessment import setup as assessment_setup

    async def run():
        db, favor, service, _ = await assessment_setup(tmp_path)
        with patch("time.time", return_value=START):
            await apply(favor, proof(1))
            current = Interaction(
                "123", "789", "456", "test", "next", "这份公开材料也已匿名化。"
            )
            payload = await service._snapshot(current)
            verdict = Verdict(
                "boundary_respected",
                0.95,
                current.text,
                True,
                "response:boundary",
                BOUNDARY,
                "next",
                current.text,
            )
            item = verify_trust_evidence(current, verdict, verdict, payload)
            assert item.source_events
            await favor.undo_log(records(db)[0][3])
            with pytest.raises(ValueError, match="来源已撤销"):
                await apply(favor, item)
            assert len(records(db)) == 1
        await db.close()

    asyncio.run(run())


@pytest.mark.parametrize("raw", [False, True])
def test_revoked_anchor_invalidates_already_persisted_unclaimed_dependent(
    tmp_path, raw
):
    from test_assessment import setup as assessment_setup

    async def run():
        db, favor, service, _ = await assessment_setup(tmp_path)
        with patch("time.time", return_value=START):
            await apply(favor, proof(1))
            current = Interaction(
                "123", "789", "456", "test", "dependent", "第二份公开材料已匿名化。"
            )
            payload = await service._snapshot(current)
            verdict = Verdict(
                "boundary_respected",
                0.95,
                current.text,
                True,
                "response:boundary",
                BOUNDARY,
                current.message_id,
                current.text,
            )
            dependent = verify_trust_evidence(current, verdict, verdict, payload)
            assert dependent.source_events == (records(db)[0][0],)
            await apply(favor, dependent)
            if raw:
                await db.mark_reversed(records(db)[0][3])
            else:
                await favor.undo_log(records(db)[0][3])
            await apply(favor, proof(3))
            result = await apply(favor, proof(4))
            assert result["delta"]["trust"] == 0
            assert records(db)[1][2] == 1
        await db.close()

    asyncio.run(run())


def test_source_revocation_invalidates_persisted_descendants_and_their_rewards(
    tmp_path,
):
    from test_assessment import setup as assessment_setup

    async def run():
        db, favor, service, _ = await assessment_setup(tmp_path)
        for number in range(1, 7):
            days = 0 if number <= 3 else 5
            with patch("time.time", return_value=START + days * DAY + number):
                if number == 1:
                    await apply(favor, proof(1))
                else:
                    current = Interaction(
                        "123",
                        "789",
                        "456",
                        "test",
                        f"dependent-{number}",
                        f"第{number}份公开材料已匿名化。",
                    )
                    payload = await service._snapshot(current)
                    verdict = Verdict(
                        "boundary_respected",
                        0.95,
                        current.text,
                        True,
                        "response:boundary",
                        BOUNDARY,
                        current.message_id,
                        current.text,
                    )
                    await apply(
                        favor, verify_trust_evidence(current, verdict, verdict, payload)
                    )
        with patch("time.time", return_value=START + 6 * DAY):
            assert (await favor.get_relation("789", "456")).trust == 2
            await favor.undo_log(records(db)[0][3])
            assert (await favor.get_relation("789", "456")).trust == 0
            assert all(row[1] and row[2] for row in records(db))
            assert (
                db._c().execute("SELECT SUM(trust_up) FROM daily_gain").fetchone()[0]
                == 2
            )
        await db.close()

    asyncio.run(run())


def test_raw_mark_reversed_keeps_numeric_contract_and_invalidates_evidence(tmp_path):
    async def run():
        db, favor = await setup(tmp_path)
        with patch("time.time", return_value=START):
            await apply(favor, proof(1, kind="verified_correction"))
            log_id = records(db)[0][3]
            before = (await favor.get_relation("789", "456")).trust
            await db.mark_reversed(log_id)
            await db.mark_reversed(log_id)
            assert (await favor.get_relation("789", "456")).trust == before == 0.5
            assert records(db)[0][2] == 1
        await db.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind,text",
    [
        ("apology", "对不起"),
        ("normal_question", "我保证以后尊重边界"),
        ("thanks", "谢谢，我会照做"),
    ],
)
def test_ordinary_apology_promise_and_compliance_do_not_enter_boundary_window(
    tmp_path, kind, text
):
    from test_assessment import (
        Provider,
        setup as assessment_setup,
        interaction,
        verdict,
    )

    async def run():
        db, favor, service, _ = await assessment_setup(
            tmp_path, Provider(verdict(text=text, kind=kind))
        )
        await service.assess(interaction(text), directed=True)
        assert (await favor.get_relation("789", "456")).trust == 0
        assert not records(db)
        await db.close()

    asyncio.run(run())


def test_reset_removes_only_target_evidence_and_expired_quotes_are_pruned(tmp_path):
    async def run():
        db, favor = await setup(tmp_path)
        with patch("time.time", return_value=START):
            await apply(favor, proof(1))
            await apply(favor, proof(2, group="790"))
        with patch("time.time", return_value=START + 15 * DAY):
            await apply(favor, proof(3))
            assert db._c().execute(
                "SELECT reference_excerpt,behavior_excerpt FROM trust_evidence WHERE event_id=?",
                (proof(1).event_id,),
            ).fetchone() == ("", "")
            await favor.reset("789", "456")
            assert len(records(db)) == 1
            assert (
                db._c().execute("SELECT group_id FROM trust_evidence").fetchone()[0]
                == "790"
            )
            await favor.reset("790")
            assert not records(db)
        await db.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    [
        "missing_behavior",
        "wrong_source",
        "different",
        "low",
        "before_boundary",
        "boundary_as_action",
    ],
)
def test_boundary_requires_matching_reviews_and_distinct_observable_source(case):
    with patch("time.time", return_value=START):
        current = Interaction("123", "789", "456", "test", "act", "这是匿名化材料")
        first = Verdict(
            "boundary_respected",
            0.95,
            current.text,
            True,
            "boundary",
            BOUNDARY,
            "act",
            current.text,
        )
        reviewed = first
        turns = [dict(role="bot", message_id="boundary", text=BOUNDARY, ts=START - 1)]
        if case == "missing_behavior":
            first = reviewed = replace(
                first, behavior_message_id="", behavior_excerpt=""
            )
        elif case == "wrong_source":
            turns[0]["role"] = "user"
        elif case == "different":
            reviewed = replace(first, behavior_message_id="other")
        elif case == "low":
            reviewed = replace(first, confidence=0.89)
        elif case == "before_boundary":
            turns[0]["ts"] = START + 1
        elif case == "boundary_as_action":
            first = reviewed = replace(
                first, behavior_message_id="boundary", behavior_excerpt=BOUNDARY
            )
        with pytest.raises(ValueError):
            verify_trust_evidence(
                current, first, reviewed, json.dumps(dict(recent_turns=turns))
            )
        raw = json.dumps(
            dict(
                event_type="boundary_respected",
                confidence=0.95,
                evidence=current.text,
                reference_message_id="boundary",
                reference_excerpt=BOUNDARY,
            )
        )
        with pytest.raises(ValueError):
            parse_verdict(raw, current)


def test_bot_reply_cannot_be_boundary_respect_behavior_but_can_confirm_promise():
    with patch("time.time", return_value=START):
        current = Interaction("123", "789", "456", "test", "current", "这次材料请核对")
        confirmation = "这份材料已经匿名化。"
        verdict = Verdict(
            "boundary_respected",
            0.95,
            current.text,
            True,
            "boundary",
            BOUNDARY,
            "response:confirmation",
            confirmation,
        )
        payload = json.dumps(
            dict(
                recent_turns=[
                    dict(
                        role="bot", message_id="boundary", text=BOUNDARY, ts=START - 2
                    ),
                    dict(
                        role="bot",
                        message_id="response:confirmation",
                        text=confirmation,
                        ts=START - 1,
                    ),
                ]
            )
        )
        with pytest.raises(ValueError):
            verify_trust_evidence(current, verdict, verdict, payload)
        promise = replace(verdict, event_type="promise_kept")
        assert (
            verify_trust_evidence(current, promise, promise, payload).event_type
            == "promise_kept"
        )
