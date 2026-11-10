"""信任规则、证据去重与一次性配置升级的回归。"""

import asyncio
import copy
import json
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.core.assessment import (
    Interaction,
    Verdict,
    verify_trust_evidence,
)
from astrbot_plugin_bond.core.config_migrations import (
    migrate_saved_defaults,
    trust_config_backup,
)
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_profiles import build_relation_profile
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend
from astrbot_plugin_bond.services.config_upgrade import upgrade_config


def proof(kind, current="message", reference="response:record"):
    interaction = Interaction("123", "789", "456", "test", current, "这件事已经核对")
    verdict = Verdict(kind, 0.95, "已经核对", True, reference, "可核对的记录")
    payload = json.dumps(
        {
            "recent_turns": [
                {"message_id": reference, "role": "bot", "text": "这是可核对的记录"}
            ]
        }
    )
    return verify_trust_evidence(interaction, verdict, verdict, payload)


def test_upgrade_only_t_fields_once_preserves_shared_settings():
    config = dict(
        relation_mode="realistic",
        decay=dict(
            trust_half_life=123,
            trust_baseline=0,
            trust_enabled=False,
            familiarity_half_life=17,
            grace_days=8,
            maintenance_strategy="legacy",
        ),
        relation_daily_caps=dict(trust_up=9, trust_down=30, affinity_up=7),
        assessment=dict(small_provider_id="local-provider"),
    )
    previous = copy.deepcopy(config)
    backup = trust_config_backup(config)
    assert backup["decay"]["trust_half_life"] == 123
    assert "assessment" not in backup
    assert len(migrate_saved_defaults(config)) == 6
    assert config["decay"]["trust_half_life"] == 60
    assert config["decay"]["trust_baseline"] == 20
    assert config["decay"]["trust_enabled"] is True
    assert config["decay"]["grace_days"] == 8
    assert config["decay"]["familiarity_half_life"] == 17
    assert config["relation_mode"] == "realistic"
    assert config["assessment"] == previous["assessment"]
    assert config["relation_daily_caps"] == dict(
        trust_up=2, trust_down=8, affinity_up=7
    )
    config["decay"]["trust_half_life"] = 90
    assert migrate_saved_defaults(config) == []
    assert config["decay"]["trust_half_life"] == 90


def test_config_save_failure_rolls_back_memory_and_retry_keeps_original_backup(
    tmp_path,
):
    class Config(dict):
        fail = True

        def save_config(self):
            if self.fail:
                raise OSError("synthetic disk failure")
            self.saved = copy.deepcopy(dict(self))

    config = Config(
        decay={"trust_half_life": 123}, provider={"credential": "synthetic-only"}
    )
    original = copy.deepcopy(config)
    with pytest.raises(OSError):
        upgrade_config(config, tmp_path)
    assert config == original
    backup_path = tmp_path / "trust-settings-before-0.6.json"
    backup = backup_path.read_text(encoding="utf-8")
    assert "credential" not in backup
    assert json.loads(backup)["decay"]["trust_half_life"] == 123
    config.fail = False
    assert upgrade_config(config, tmp_path)
    assert config.saved["trust_config_version"] == 1
    assert backup_path.read_text(encoding="utf-8") == backup
    config["decay"]["trust_half_life"] = 90
    assert upgrade_config(config, tmp_path) == []
    assert config["decay"]["trust_half_life"] == 90


def test_missing_marker_runs_once_and_future_marker_is_not_downgraded():
    fresh = {}
    assert migrate_saved_defaults(fresh)
    assert migrate_saved_defaults(fresh) == []
    future = {"trust_config_version": 2, "decay": {"trust_half_life": 75}}
    assert migrate_saved_defaults(future) == []
    assert future["decay"]["trust_half_life"] == 75


@pytest.mark.parametrize("mode,positive", [("realistic", 0.5), ("galgame", 1)])
def test_trust_event_caps_quotas_duplicate_and_undo(tmp_path, mode, positive):
    async def run():
        db = SQLiteBackend(tmp_path / "123" / "trust.db")
        await db.init()
        policy, caps = build_relation_profile(mode)
        favor = FavorService(
            db, LevelTable.from_config(None), relation_policy=policy, relation_caps=caps
        )
        item = proof("verified_correction")
        with pytest.raises(ValueError):
            await favor.apply_relation_event(
                "789", "456", item.event_type, event_id=item.event_id
            )

        async def apply(e):
            return await favor.apply_relation_event(
                "789",
                "456",
                e.event_type,
                event_id=e.event_id,
                intensity=999,
                trust_evidence=e,
                cooldown_sec=0,
            )

        result = await apply(item)
        assert result["delta"]["trust"] == positive
        duplicate = proof("promise_kept", "new-message")
        assert duplicate.event_id == item.event_id
        assert (await apply(duplicate))["skipped"]
        first = await apply(
            proof("severe_manipulation", reference="response:serious-1")
        )
        second = await apply(
            proof("severe_manipulation", reference="response:serious-2")
        )
        assert first["delta"]["trust"] == -6
        assert second["delta"]["trust"] == -2
        assert (
            await apply(proof("severe_manipulation", reference="response:serious-3"))
        )["delta"]["trust"] == 0
        logs = await favor.query_logs("789", "456")
        log = next(row for row in logs if "serious-1" in row["reason"])
        before = (await favor.get_relation("789", "456")).trust
        await favor.undo_log(log["id"])
        assert (await favor.get_relation("789", "456")).trust == pytest.approx(
            before + 6
        )
        assert (
            await apply(proof("severe_manipulation", reference="response:serious-4"))
        )["delta"]["trust"] == 0
        await db.close()

    asyncio.run(run())


def test_legacy_chat_does_not_maintain_trust_and_direct_decay_is_read_only(tmp_path):
    async def run():
        db = SQLiteBackend(tmp_path / "123" / "trust.db")
        await db.init()
        start = 1_800_000_000
        with patch("time.time", return_value=start):
            old = FavorService(
                db,
                LevelTable.from_config(None),
                maintenance_strategy="legacy",
                trust_decay_enabled=True,
                trust_half_life=120,
                trust_baseline=20,
            )
            await old.set_relation(
                "789", "456", familiarity=50, affinity=30, trust=80, event_id="seed"
            )
            original = await db.get("789", "456")
        with patch("time.time", return_value=start + 65 * 86400):
            old_effective = (await old.get("789", "456")).trust
            fresh = FavorService(
                db,
                LevelTable.from_config(None),
                maintenance_strategy="legacy",
                trust_decay_enabled=True,
                trust_half_life=60,
                trust_baseline=20,
            )
            assert (await fresh.get("789", "456")).trust < old_effective
            assert vars(await db.get("789", "456")) == vars(original)
            await fresh.apply_relation_event(
                "789", "456", "friendly", event_id="chat", cooldown_sec=0
            )
            after = await db.get("789", "456")
            assert (after.trust, after.trust_updated_at, after.trust_maintained_at) == (
                original.trust,
                original.trust_updated_at,
                original.trust_maintained_at,
            )
            assert after.affinity_maintained_at > original.affinity_maintained_at
        await db.close()

    asyncio.run(run())


def test_no_confidence_substitute_for_source_and_review_agreement():
    interaction = Interaction("123", "789", "456", "test", "m", "已经完成")
    first = Verdict("promise_kept", 1, "已经完成", True, "r", "确认完成")
    for reviewed, turns in [
        (None, []),
        (first, []),
        (first, [{"message_id": "r", "role": "user", "text": "确认完成"}]),
        (first, [{"message_id": "r", "role": "bot", "text": "没有确认"}]),
        (
            Verdict("repair_action", 1, "已经完成", True, "r", "确认完成"),
            [{"message_id": "r", "role": "bot", "text": "确认完成"}],
        ),
    ]:
        with pytest.raises(ValueError):
            verify_trust_evidence(
                interaction, first, reviewed, json.dumps({"recent_turns": turns})
            )


@pytest.mark.parametrize(
    "case",
    ["accepted", "missing", "different", "low_confidence", "single", "user_quote"],
)
def test_assessment_requires_two_matching_reviews_and_trusted_local_reference(
    tmp_path, case
):
    from test_assessment import setup, Provider, interaction, verdict

    async def run():
        text = "这处算式已按核对结果纠正"
        excerpt = "先前算式确实有误，你指出的 2+2=4 正确"
        ref = "response:previous"
        result = verdict(
            text=text,
            kind="verified_correction",
            reference_message_id=ref,
            reference_excerpt=excerpt,
        )
        review = result
        if case == "different":
            review = verdict(
                text=text,
                kind="promise_kept",
                reference_message_id=ref,
                reference_excerpt=excerpt,
            )
        elif case == "low_confidence":
            review = verdict(
                text=text,
                kind="verified_correction",
                confidence=0.85,
                reference_message_id=ref,
                reference_excerpt=excerpt,
            )
        db, favor, service, _ = await setup(
            tmp_path,
            Provider(result),
            Provider(review),
            large_provider_id="large",
            assessment_mode="single" if case == "single" else "tiered",
        )
        prior = interaction("这道算式应该是4", message="previous")
        service.remember_admitted_interaction(prior)
        if case == "user_quote":
            service._remember(prior, "user", excerpt, ref)
        elif case != "missing":
            service.remember_bot_reply(prior, excerpt)
        status = await service.assess(
            interaction(text, message="current"), directed=True
        )
        assert status == ("accepted" if case == "accepted" else "abstained")
        assert (await favor.get_relation("789", "456")).trust == (
            0.5 if case == "accepted" else 0
        )
        if case == "accepted":
            assert (
                await service.assess(interaction(text, message="again"), directed=True)
                == "unchanged"
            )
            assert (await favor.get_relation("789", "456")).trust == 0.5
            logs = await favor.query_logs("789", "456")
            assert "2+2=4" in logs[0]["message"]
            assert len(logs) == 1
        await db.close()

    asyncio.run(run())
