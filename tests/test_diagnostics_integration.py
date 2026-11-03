"""观察点只能记录已发生的结果，不改变评分、调用或人格。"""

import asyncio
import json
from types import SimpleNamespace

from astrbot_plugin_bond.core.assessment import Interaction
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_policy import RelationPolicy
from astrbot_plugin_bond.services.assessment_service import (
    AssessmentService,
    AssessmentConfig,
    AssessmentCapacity,
)
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend


class Collector:
    def __init__(self, enabled):
        self.enabled = enabled
        self.rows = []

    def emit(self, bot, mode, group, user, kind, **fields):
        self.rows.append(dict(kind=kind, **fields))


async def run_case(path, enabled, *, failed=False, disk=False, broken_disk=False):
    store = SQLiteBackend(path / "10001" / "group.db")
    await store.init()
    from astrbot_plugin_bond.core.taskregistry import TaskRegistry
    from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder

    if disk:
        root = path / "diagnostics"
        if broken_disk:
            root.write_text("synthetic blocked directory", encoding="utf-8")
        sink = DiagnosticRecorder(root, TaskRegistry(), enabled)
    else:
        sink = Collector(enabled)
    favor = FavorService(store, LevelTable.from_config(None), diagnostics=sink)
    calls = []

    async def chat(prompt):
        calls.append(prompt)
        if failed:
            raise RuntimeError("synthetic-secret-error")
        return SimpleNamespace(
            completion_text=json.dumps(
                dict(event_type="thanks", confidence=0.95, evidence="谢谢")
            ),
            usage=SimpleNamespace(total=None),
        )

    async def stores():
        return [store]

    assessment = AssessmentService(
        store,
        favor,
        SimpleNamespace(get_provider_by_id=lambda _: SimpleNamespace(text_chat=chat)),
        AssessmentConfig(enabled=True, small_provider_id="small"),
        AssessmentCapacity(),
        stores,
    )
    event = Interaction(
        "10001",
        "20001",
        "30001",
        "synthetic",
        "m1",
        "谢谢你帮忙：synthetic-private-text",
    )
    try:
        status = await assessment.assess(event, directed=True)
        duplicate = await assessment.assess(event, directed=True)
        state = (await favor.get_relation("20001", "30001")).as_dict()
        logs = await favor.query_logs("20001", "30001")
        if disk:
            exported = await sink.export("10001", "per_group")
            rows = [dict(kind=r["kind"], **r["fields"]) for r in exported["rows"]]
            stats = exported["stats"]
        else:
            rows, stats = sink.rows, {}
        return dict(
            status=status,
            duplicate=duplicate,
            state=state,
            calls=calls,
            logs=[
                {k: r[k] for k in ("event_id", "relation_delta", "reason")}
                for r in logs
            ],
            rows=rows,
            diagnostics_stats=stats,
        )
    finally:
        if disk:
            await sink.close()
        await store.close()


def test_enabled_and_disabled_observe_identical_real_transactions(tmp_path):
    async def run():
        off = await run_case(tmp_path / "off", False)
        on = await run_case(tmp_path / "on", True)
        assert off["rows"] == []
        for key in ("status", "duplicate", "state", "calls", "logs"):
            assert off[key] == on[key], key
        relation = next(r for r in on["rows"] if r["kind"] == "relation")
        assert relation["after"] == on["state"]
        assert relation["delta"] == on["logs"][0]["relation_delta"]
        assert relation["term_before"] == "r13"
        assert relation["policy_version"] == "0.3.9"
        assert len(relation["config_hash"]) == 64
        call = next(r for r in on["rows"] if r["kind"] == "call")
        assert call["tokens"] is None
        assert call["elapsed_ms"] >= 0
        assert "synthetic-private-text" not in json.dumps(on["rows"])
        assert "谢谢" not in json.dumps(on["rows"], ensure_ascii=False)
        assert [r["status"] for r in on["rows"] if r["kind"] == "assessment"] == [
            "accepted",
            "duplicate_or_cooldown",
        ]

    asyncio.run(run())


def test_provider_failure_is_observed_without_error_text_or_retry(tmp_path):
    async def run():
        result = await run_case(tmp_path, True, failed=True)
        assert result["state"] == dict(familiarity=0, affinity=0, trust=0)
        assert len(result["calls"]) == 1
        assert any(
            r["kind"] == "call" and r["status"] == "failed" for r in result["rows"]
        )
        assert "synthetic-secret-error" not in json.dumps(result["rows"])

    asyncio.run(run())


def test_rule_snapshot_cannot_modify_policy():
    policy = RelationPolicy()
    view = policy.snapshot()
    view["rules"]["thanks"]["trust"] = 99
    view["event_caps"]["thanks"]["trust"] = 99
    assert policy.deltas("thanks")["trust"] == 0


def test_real_writer_and_unwritable_path_preserve_business_results(tmp_path):
    async def run():
        off = await run_case(tmp_path / "off", False, disk=True)
        on = await run_case(tmp_path / "on", True, disk=True)
        blocked = await run_case(
            tmp_path / "blocked", True, disk=True, broken_disk=True
        )
        for observed in (on, blocked):
            for key in ("status", "duplicate", "state", "calls", "logs"):
                assert off[key] == observed[key], key
        assert on["rows"] and not on["diagnostics_stats"]["gap"]
        assert blocked["rows"] == []
        assert blocked["diagnostics_stats"]["counters"]["write_errors"] > 0
        assert not (tmp_path / "off" / "diagnostics").exists()

    asyncio.run(run())


def test_observation_exception_does_not_fail_committed_change(tmp_path, monkeypatch):
    from astrbot_plugin_bond.core.taskregistry import TaskRegistry
    from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder

    async def run():
        store = SQLiteBackend(tmp_path / "10001" / "group.db")
        await store.init()
        sink = DiagnosticRecorder(tmp_path / "diagnostics", TaskRegistry(), True)
        favor = FavorService(store, LevelTable.from_config(None), diagnostics=sink)

        def broken(*args, **kwargs):
            raise RuntimeError("synthetic-secret-error")

        monkeypatch.setattr(favor, "describe_relation", broken)
        try:
            result = await favor.set_relation(
                "20001",
                "30001",
                familiarity=10,
                affinity=20,
                trust=30,
                event_id="synthetic-one",
            )
            assert result["after"]["trust"] == 30
            assert (await favor.get_relation("20001", "30001")).trust == 30
            assert sink.stats()["counters"]["dropped"] == 1
            monkeypatch.setattr(sink, "emit", broken)
            favor.record_diagnostic("20001", "30001", "entry", status="ok")
            assert sink.stats()["counters"]["dropped"] == 2
            assert "synthetic-secret-error" not in json.dumps(sink.stats())
        finally:
            await sink.close()
            await store.close()

    asyncio.run(run())


def test_global_reset_preserves_origin_and_undo_never_links_unknown_group(tmp_path):
    from astrbot_plugin_bond.core.taskregistry import TaskRegistry
    from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder

    async def run():
        store = SQLiteBackend(tmp_path / "10001" / "global.db")
        await store.init()
        sink = DiagnosticRecorder(tmp_path / "diagnostics", TaskRegistry(), True)
        favor = FavorService(
            store, LevelTable.from_config(None), diagnostics=sink, scope_mode="global"
        )
        try:
            for group in ("20001", "20002"):
                await favor.set_relation(
                    group,
                    "30001",
                    familiarity=10,
                    affinity=20,
                    trust=30,
                    event_id="synthetic-" + group,
                )
                log = (await favor.query_logs(group, "30001"))[0]
                await favor.undo_log(log["id"])
                await favor.reset(group, "30001")
            data = await sink.export("10001", "global")
            resets = [r for r in data["rows"] if r["fields"].get("reason") == "reset"]
            assert len(resets) == 2
            assert len({r["partition"] for r in resets}) == 2
            assert len({r["member"] for r in resets}) == 2
            assert not any(r["fields"].get("reason") == "undo" for r in data["rows"])
            assert data["stats"]["counters"]["dropped"] == 2
            assert (await favor.get_relation("20001", "30001")).as_dict() == dict(
                familiarity=0, affinity=0, trust=0
            )
        finally:
            await sink.close()
            await store.close()

    asyncio.run(run())
