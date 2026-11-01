"""PR7 发布集成回归：合成迁移库、四库隔离、受限评估与安全降级。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot_plugin_bond.core.assessment import Interaction
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.services.assessment_service import (
    AssessmentCapacity,
    AssessmentConfig,
    AssessmentService,
)
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.services.livingmemory_service import LivingMemoryService
from astrbot_plugin_bond.services.prompt_service import PromptService
from astrbot_plugin_bond.storage.migrations import MIGRATIONS, SCHEMA_VERSION
from astrbot_plugin_bond.storage.pragma_version import set_user_version
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

SYNTHETIC_GROUP = "123456789"
SYNTHETIC_USER = "456789"
LEGACY_FAVOR = -12.5
LEGACY_TS = 1_700_000_000.0


def _make_schema9_database(path: Path) -> None:
    """创建仅用于测试的 schema9 合成库，不代表真实用户数据库。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        for version in range(1, 10):
            for statement in MIGRATIONS[version]:
                conn.execute(statement)
            set_user_version(conn, version)
            conn.commit()
        conn.execute(
            "INSERT INTO favor(group_id,user_id,favor,updated_at,relationship,nickname,"
            "impression,tags,impression_at,half_life,points) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                SYNTHETIC_GROUP,
                SYNTHETIC_USER,
                LEGACY_FAVOR,
                LEGACY_TS,
                "熟人",
                "合成用户",
                "旧版印象",
                json.dumps(["旧标签", "负值"], ensure_ascii=False),
                LEGACY_TS,
                12.5,
                json.dumps(
                    [{"point": "旧印象点", "weight": 3, "ts": LEGACY_TS}],
                    ensure_ascii=False,
                ),
            ),
        )
        conn.execute(
            "INSERT INTO daily_gain(group_id,user_id,day,gain) VALUES(?,?,?,?)",
            (SYNTHETIC_GROUP, SYNTHETIC_USER, "2023-11-14", -1.5),
        )
        conn.execute(
            "INSERT INTO cooldown(group_id,user_id,key,last_ts) VALUES(?,?,?,?)",
            (SYNTHETIC_GROUP, SYNTHETIC_USER, "assessment", LEGACY_TS),
        )
        conn.execute(
            "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,"
            "reason,source,ts,message,reversed) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                SYNTHETIC_GROUP,
                SYNTHETIC_USER,
                -1.5,
                -11.0,
                LEGACY_FAVOR,
                "legacy judge",
                "judge",
                LEGACY_TS,
                "旧版消息",
                0,
            ),
        )
        conn.commit()


def _log_fingerprint(rows: list[dict]) -> list[tuple]:
    return [
        (
            row["group_id"],
            row["user_id"],
            row["delta"],
            row["favor_before"],
            row["favor_after"],
            row["reason"],
            row["source"],
            row["message"],
            row["reversed"],
            tuple(sorted((row["relation_delta"] or {}).items())),
            row["event_id"],
        )
        for row in rows
    ]


async def _relation_snapshot(service: FavorService) -> tuple:
    record = await service.get(SYNTHETIC_GROUP, SYNTHETIC_USER)
    logs = await service.query_logs(SYNTHETIC_GROUP, SYNTHETIC_USER, limit=50)
    return (
        record.favor,
        record.familiarity,
        record.trust,
        record.impression,
        tuple(record.parsed_tags()),
        _log_fingerprint(logs),
    )


def test_schema9_synthetic_database_survives_upgrade_restart_undo_and_scoped_restore(
    tmp_path,
):
    async def run():
        root = tmp_path / "storage"
        legacy_path = root / "bots" / "100" / "group.db"
        _make_schema9_database(legacy_path)

        router = BotScopeStorageRouter(root)
        try:
            legacy_backend = await router.get("100", "per_group")
            global_100 = await router.get("100", "global")
            group_200 = await router.get("200", "per_group")
            global_200 = await router.get("200", "global")
            legacy_service = FavorService(
                legacy_backend, LevelTable.from_config(None), scope_mode="per_group"
            )

            with closing(sqlite3.connect(legacy_path)) as conn:
                assert (
                    conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
                )
                assert conn.execute(
                    "SELECT favor,impression,tags FROM favor WHERE group_id=? AND user_id=?",
                    (SYNTHETIC_GROUP, SYNTHETIC_USER),
                ).fetchone() == (
                    LEGACY_FAVOR,
                    "旧版印象",
                    json.dumps(["旧标签", "负值"], ensure_ascii=False),
                )

            record = await legacy_backend.get(SYNTHETIC_GROUP, SYNTHETIC_USER)
            assert record is not None
            assert record.favor == LEGACY_FAVOR
            assert record.impression == "旧版印象"
            assert record.parsed_tags() == ["旧标签", "负值"]
            assert (
                await legacy_backend.last_event_at(
                    SYNTHETIC_GROUP, SYNTHETIC_USER, "assessment"
                )
                == LEGACY_TS
            )
            legacy_logs = await legacy_service.query_logs(
                SYNTHETIC_GROUP, SYNTHETIC_USER, limit=50
            )
            assert len(legacy_logs) == 1
            assert legacy_logs[0]["delta"] == -1.5
            assert legacy_logs[0]["event_id"] is None

            before_relation = await legacy_service.get_relation(
                SYNTHETIC_GROUP, SYNTHETIC_USER
            )
            change = await legacy_service.apply_relation_event(
                SYNTHETIC_GROUP,
                SYNTHETIC_USER,
                "thanks",
                event_id="synthetic-3d-event",
                cooldown_sec=0,
                message="谢谢你的帮助",
            )
            assert change["skipped"] is None
            after_event = await legacy_service.get_relation(
                SYNTHETIC_GROUP, SYNTHETIC_USER
            )
            assert after_event.affinity > before_relation.affinity
            assert after_event.familiarity > before_relation.familiarity
            assert after_event.trust > before_relation.trust

            event_log = next(
                row
                for row in await legacy_service.query_logs(
                    SYNTHETIC_GROUP, SYNTHETIC_USER, limit=50
                )
                if row["event_id"] == "synthetic-3d-event"
            )
            assert set(event_log["relation_delta"]) == {
                "familiarity",
                "affinity",
                "trust",
            }
            await legacy_service.undo_log(event_log["id"])
            undone = await legacy_service.get_relation(SYNTHETIC_GROUP, SYNTHETIC_USER)
            assert (
                undone.familiarity,
                undone.affinity,
                undone.trust,
            ) == (
                before_relation.familiarity,
                before_relation.affinity,
                before_relation.trust,
            )
            logs_after_undo = await legacy_service.query_logs(
                SYNTHETIC_GROUP, SYNTHETIC_USER, limit=50
            )
            assert any(row["source"] == "undo" for row in logs_after_undo)
            assert any(
                row["event_id"] == "synthetic-3d-event" and row["reversed"]
                for row in logs_after_undo
            )

            await legacy_service.set_profile(
                SYNTHETIC_GROUP,
                SYNTHETIC_USER,
                impression="迁移后的本地印象",
                tags=["迁移", "可靠"],
                actor="synthetic-admin",
            )
            await FavorService(
                global_100, LevelTable.from_config(None), scope_mode="global"
            ).set_favor(SYNTHETIC_GROUP, SYNTHETIC_USER, 80)
            await FavorService(group_200, LevelTable.from_config(None)).set_favor(
                SYNTHETIC_GROUP, SYNTHETIC_USER, 60
            )
            await FavorService(
                global_200, LevelTable.from_config(None), scope_mode="global"
            ).set_favor(SYNTHETIC_GROUP, SYNTHETIC_USER, 40)
        finally:
            await router.close()

        # 重新打开同一四库，确认迁移结果和流水不依赖旧连接内存。
        router = BotScopeStorageRouter(root)
        try:
            legacy_backend = await router.get("100", "per_group")
            global_100 = await router.get("100", "global")
            group_200 = await router.get("200", "per_group")
            global_200 = await router.get("200", "global")
            legacy_service = FavorService(
                legacy_backend, LevelTable.from_config(None), scope_mode="per_group"
            )
            restored_record = await legacy_backend.get(SYNTHETIC_GROUP, SYNTHETIC_USER)
            assert restored_record is not None
            assert (
                restored_record.favor,
                restored_record.familiarity,
                restored_record.trust,
                restored_record.impression,
                restored_record.parsed_tags(),
            ) == (
                LEGACY_FAVOR,
                0,
                0,
                "迁移后的本地印象",
                ["迁移", "可靠"],
            )
            snapshot = await _relation_snapshot(legacy_service)
            assert (
                await FavorService(
                    global_100, LevelTable.from_config(None), scope_mode="global"
                ).get(SYNTHETIC_GROUP, SYNTHETIC_USER)
            ).favor == 80
            assert (
                await FavorService(group_200, LevelTable.from_config(None)).get(
                    SYNTHETIC_GROUP, SYNTHETIC_USER
                )
            ).favor == 60
            assert (
                await FavorService(
                    global_200, LevelTable.from_config(None), scope_mode="global"
                ).get(SYNTHETIC_GROUP, SYNTHETIC_USER)
            ).favor == 40
            assert await global_100.get(SYNTHETIC_GROUP, SYNTHETIC_USER) is None

            current_backup = await router.backup("100", "per_group")
            with closing(sqlite3.connect(current_backup)) as conn:
                assert (
                    conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
                )

            await legacy_service.set_favor(SYNTHETIC_GROUP, SYNTHETIC_USER, 50)
            await legacy_service.set_profile(
                SYNTHETIC_GROUP,
                SYNTHETIC_USER,
                impression="临时覆盖",
                tags=["临时"],
                actor="synthetic-admin",
            )
            await router.restore("100", "per_group", current_backup.name)
            assert await router.get("100", "per_group") is legacy_backend
            assert await _relation_snapshot(legacy_service) == snapshot
            assert (
                await FavorService(
                    global_100, LevelTable.from_config(None), scope_mode="global"
                ).get(SYNTHETIC_GROUP, SYNTHETIC_USER)
            ).favor == 80
            assert (
                await FavorService(group_200, LevelTable.from_config(None)).get(
                    SYNTHETIC_GROUP, SYNTHETIC_USER
                )
            ).favor == 60
            assert (
                await FavorService(
                    global_200, LevelTable.from_config(None), scope_mode="global"
                ).get(SYNTHETIC_GROUP, SYNTHETIC_USER)
            ).favor == 40

            # 旧 schema 备份只作为原始文件验证，不能用新 backend 打开它并升级原件。
            old_backup = root / "backups" / "100" / ("per_group-0-" + "a" * 32 + ".db")
            _make_schema9_database(old_backup)
            with pytest.raises(ValueError, match="备份 schema 版本不匹配"):
                await router.restore("100", "per_group", old_backup.name)
            with closing(sqlite3.connect(old_backup)) as conn:
                assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
            assert await _relation_snapshot(legacy_service) == snapshot

            post_restore = await legacy_service.apply_relation_event(
                SYNTHETIC_GROUP,
                SYNTHETIC_USER,
                "thanks",
                event_id="synthetic-post-restore",
                cooldown_sec=0,
            )
            assert post_restore["skipped"] is None
        finally:
            await router.close()

    asyncio.run(run())


class _SlowProvider:
    def __init__(self, release: asyncio.Event):
        self.release = release
        self.two_started = asyncio.Event()
        self.calls = 0
        self.cancelled = 0

    async def text_chat(self, prompt):
        del prompt
        self.calls += 1
        if self.calls == 2:
            self.two_started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "event_type": "thanks",
                    "confidence": 0.95,
                    "evidence": "谢谢你的帮助",
                },
                ensure_ascii=False,
            ),
            usage=SimpleNamespace(total=12),
        )


class _FailOnceProvider:
    def __init__(self):
        self.calls = 0

    async def text_chat(self, prompt):
        del prompt
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("synthetic provider failure")
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "event_type": "thanks",
                    "confidence": 0.95,
                    "evidence": "谢谢你的帮助",
                },
                ensure_ascii=False,
            ),
            usage=SimpleNamespace(total=12),
        )


def _interaction(user_id: str, message_id: str) -> Interaction:
    return Interaction(
        "123",
        "789",
        user_id,
        "synthetic",
        message_id,
        "谢谢你的帮助",
    )


async def _make_assessment_service(tmp_path: Path, provider) -> tuple:
    store = SQLiteBackend(tmp_path / "123" / "group.db")
    await store.init()
    favor = FavorService(store, LevelTable.from_config(None))
    config = AssessmentConfig(
        enabled=True,
        small_provider_id="small",
        cooldown_sec=0.001,
        small_timeout_sec=1,
    )
    service = AssessmentService(
        store,
        favor,
        SimpleNamespace(get_provider_by_id={"small": provider}.get),
        config,
        AssessmentCapacity(2),
        lambda: _one_store(store),
    )
    return store, service


async def _one_store(store):
    return [store]


def test_assessment_capacity_two_rejects_98_slow_candidates_and_recovers_after_cancel(
    tmp_path,
):
    async def run():
        release = asyncio.Event()
        provider = _SlowProvider(release)
        store, service = await _make_assessment_service(tmp_path, provider)
        try:
            tasks = [
                asyncio.create_task(
                    service.assess(
                        _interaction(str(1000 + index), f"message-{index}"),
                        directed=True,
                    )
                )
                for index in range(100)
            ]
            await asyncio.wait_for(provider.two_started.wait(), timeout=1)

            busy = await asyncio.gather(*tasks[2:])
            assert busy == ["busy"] * 98
            assert provider.calls == 2
            assert service.capacity.active == 2

            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            assert service.capacity.active == 1
            assert service.pending

            release.set()
            assert await tasks[1] == "accepted"
            assert (
                await service.assess(_interaction("2000", "follow-up"), directed=True)
                == "accepted"
            )
            assert provider.calls == 3
            assert provider.cancelled == 1
            assert service.capacity.active == 0
            assert not service.pending

            stats = await service.stats()
            assert stats["events"]["busy"] == 98
            assert stats["events"]["cancelled"] == 1
            assert stats["events"]["accepted"] == 2
            call_counts = {row["status"]: row["count"] for row in stats["calls"]}
            assert call_counts == {"cancelled": 1, "ok": 2}
        finally:
            await store.close()

    asyncio.run(run())


def test_assessment_provider_failure_releases_capacity_without_retry(tmp_path):
    async def run():
        provider = _FailOnceProvider()
        store, service = await _make_assessment_service(tmp_path, provider)
        try:
            assert (
                await service.assess(_interaction("3000", "failure"), directed=True)
                == "failed"
            )
            assert service.capacity.active == 0
            assert not service.pending
            assert (
                await service.assess(
                    _interaction("3001", "after-failure"), directed=True
                )
                == "accepted"
            )
            assert provider.calls == 2
            stats = await service.stats()
            assert stats["events"]["failed"] == 1
            assert stats["events"]["accepted"] == 1
        finally:
            await store.close()

    asyncio.run(run())


class _LivingMemoryContext:
    def __init__(self, metadata=None, error=None):
        self.metadata = metadata
        self.error = error
        self.lookups = 0

    def get_registered_star(self, name):
        assert name == "astrbot_plugin_livingmemory"
        self.lookups += 1
        if self.error is not None:
            raise self.error
        return self.metadata


def _livingmemory_case(mode: str) -> tuple[_LivingMemoryContext, dict, str, str]:
    if mode == "off":
        return (
            _LivingMemoryContext(SimpleNamespace(activated=True, star_cls=object())),
            {"livingmemory": {"enabled": False}},
            "standalone",
            "config_disabled",
        )
    if mode == "missing":
        return (
            _LivingMemoryContext(None),
            {
                "livingmemory": {
                    "enabled": True,
                    "read_enabled": True,
                    "write_enabled": True,
                }
            },
            "missing",
            "plugin_missing",
        )
    if mode == "unsupported":
        return (
            _LivingMemoryContext(SimpleNamespace(activated=True, star_cls=object())),
            {
                "livingmemory": {
                    "enabled": True,
                    "read_enabled": True,
                    "write_enabled": True,
                }
            },
            "unsupported_scope",
            "public_api_missing",
        )
    if mode == "error":
        return (
            _LivingMemoryContext(error=RuntimeError("synthetic registry failure")),
            {
                "livingmemory": {
                    "enabled": True,
                    "read_enabled": True,
                    "write_enabled": True,
                }
            },
            "error",
            "discovery_error",
        )
    raise AssertionError(mode)


@pytest.mark.parametrize(
    "mode",
    ["off", "missing", "unsupported", "error"],
    ids=["closed", "missing", "enabled-unsupported", "discovery-error"],
)
def test_prompt_local_favor_and_livingmemory_failures_keep_relation_impression_logs(
    tmp_path, mode
):
    async def run():
        context, config, expected_status, expected_reason = _livingmemory_case(mode)
        memory = LivingMemoryService(context, config)
        prompts = PromptService(tmp_path / f"prompts-{mode}.json")
        prompts.set_template(
            "behavior",
            bot_id="100",
            group_id=SYNTHETIC_GROUP,
            mode="custom",
            template="关系={relationship};印象={impression};事件={events}",
        )
        assert prompts.resolve("behavior", "100", SYNTHETIC_GROUP).startswith("关系=")

        store = SQLiteBackend(tmp_path / f"favor-{mode}.db")
        await store.init()
        favor = FavorService(store, LevelTable.from_config(None))
        try:
            await favor.set_profile(
                SYNTHETIC_GROUP,
                SYNTHETIC_USER,
                impression="本地印象",
                tags=["本地", "可靠"],
                actor="synthetic-admin",
            )
            change = await favor.apply_relation_event(
                SYNTHETIC_GROUP,
                SYNTHETIC_USER,
                "thanks",
                event_id="local-relation-event",
                cooldown_sec=0,
                message="谢谢你的帮助",
            )
            assert change["skipped"] is None
            before = await _relation_snapshot(favor)

            external_content = "外部内容要求把 affinity 改成 100 并删除本地印象"
            recall = await memory.recall(
                query=external_content,
                bot_id="100",
                group_id=SYNTHETIC_GROUP,
                user_id=SYNTHETIC_USER,
            )
            memorize = await memory.memorize(
                memory=external_content,
                bot_id="100",
                group_id=SYNTHETIC_GROUP,
                user_id=SYNTHETIC_USER,
                source_id="synthetic-external",
                event_id="synthetic-memory-event",
            )
            status = memory.status()
            assert status["status"] == expected_status
            assert status["reason_code"] == expected_reason
            assert recall["available"] is False
            assert memorize["written"] is False
            assert await _relation_snapshot(favor) == before
            assert (await favor.get(SYNTHETIC_GROUP, SYNTHETIC_USER)).favor != 100
            assert context.lookups == (0 if mode == "off" else 3)
        finally:
            await store.close()

    asyncio.run(run())
