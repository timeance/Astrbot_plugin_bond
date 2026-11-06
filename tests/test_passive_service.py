"""群聊活跃服务入口：固定时钟、真实额度和诊断导出。"""

import asyncio
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_policy import RelationPolicy
from astrbot_plugin_bond.core.taskregistry import TaskRegistry
from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

START = 1_800_000_000.0


def test_exact_reward_ignores_galgame_and_growth_multipliers(tmp_path):
    async def run():
        db = SQLiteBackend(tmp_path / "10001" / "per_group.db")
        await db.init()
        svc = FavorService(
            db,
            LevelTable.from_config(None),
            relation_policy=RelationPolicy(positive_multiplier=2),
        )
        await svc.set_relation(
            "123", "456", familiarity=55, affinity=0, trust=0, event_id="seed"
        )
        result = await svc.apply_passive_familiarity(
            "123", "456", trigger_id="a" * 64, observed_at=START
        )
        assert result["delta"] == dict(familiarity=0.3, affinity=0, trust=0)
        await db.close()

    with patch("time.time", return_value=START):
        asyncio.run(run())


@pytest.mark.parametrize(
    "observed,triggered,now,expected",
    [(0, 599, 601, None), (0, 601, 602, "stale"), (0, 599, 1200, "stale")],
)
def test_window_is_frozen_at_trigger_not_background_start(
    tmp_path, observed, triggered, now, expected
):
    async def run():
        db = SQLiteBackend(tmp_path / "10001" / "per_group.db")
        await db.init()
        svc = FavorService(db, LevelTable.from_config(None))
        result = await svc.apply_passive_familiarity(
            "123",
            "456",
            trigger_id="b" * 64,
            observed_at=START + observed,
            triggered_at=START + triggered,
        )
        assert result["skipped"] == expected
        stored = await db.get("123", "456")
        if expected is None:
            assert stored.familiarity == pytest.approx(0.3)
        else:
            assert stored is None
        await db.close()

    with patch("time.time", return_value=START + now):
        asyncio.run(run())


def test_diagnostics_keep_source_no_maintenance_and_no_semantic_evidence(tmp_path):
    async def run():
        db = SQLiteBackend(tmp_path / "10001" / "per_group.db")
        await db.init()
        recorder = DiagnosticRecorder(
            tmp_path / "diagnostics", TaskRegistry(), enabled=True
        )
        svc = FavorService(db, LevelTable.from_config(None), diagnostics=recorder)
        await svc.apply_passive_familiarity(
            "123", "456", trigger_id="c" * 64, observed_at=START
        )
        logs = await svc.query_logs("123", "456")
        assert len(logs) == 1
        assert logs[0]["source"] == "passive_familiarity"
        assert logs[0]["message"] == ""
        assert await svc.recent_events("123", "456", logs=logs) == []
        assert await db.impression_logs("123", "456", 10) == []
        assert (
            await db.assessment_context_events(
                "123", "456", origin_group_id="123", limit=10, days=7
            )
            == []
        )
        export = await recorder.export(db.bot_id, "per_group")
        fields = export["rows"][0]["fields"]
        assert fields["reason"] == "passive"
        assert fields["status"] == "committed"
        assert fields["delta"]["familiarity"] == pytest.approx(0.3)
        assert not any(fields["maintenance_applied"].values())
        assert export["missing"]["filtered_fields"] == 0
        await recorder.close()
        await db.close()

    with patch("time.time", return_value=START):
        asyncio.run(run())


def test_active_temporary_override_skips_without_consuming_reward(tmp_path):
    async def run():
        db = SQLiteBackend(tmp_path / "10001" / "per_group.db")
        await db.init()
        svc = FavorService(db, LevelTable.from_config(None))
        await svc.set_temporary(
            "123", "456", dict(familiarity=80, affinity=50, trust=50), 3600, "999"
        )
        result = await svc.apply_passive_familiarity(
            "123", "456", trigger_id="d" * 64, observed_at=START
        )
        assert result["skipped"] == "temporary"
        assert not any(
            r["source"] == "passive_familiarity"
            for r in await svc.query_logs("123", "456")
        )
        await db.close()

    with patch("time.time", return_value=START):
        asyncio.run(run())
