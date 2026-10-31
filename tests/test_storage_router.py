"""PR-1 storage router and scope-isolation tests."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from astrbot_plugin_bond.core.scope import GLOBAL_GROUP  # noqa: E402
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter  # noqa: E402


def test_four_database_isolation_and_existing_storage_behaviors(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            bot_a_group = await router.get("100", "per_group")
            bot_a_global = await router.get("100", "global")
            bot_b_group = await router.get("200", "per_group")
            bot_b_global = await router.get("200", "global")

            await bot_a_group.set_value("123", "900", 10)
            await bot_a_global.set_value(GLOBAL_GROUP, "900", 20)
            await bot_b_group.set_value("123", "900", 30)
            await bot_b_global.set_value(GLOBAL_GROUP, "900", 40)
            await bot_a_group.set_impression("123", "900", "known", ["tag"])
            await bot_a_group.touch_event("123", "900", "cooldown", 12.5)
            await bot_a_group.add_log(
                "123", "900", 1.5, 10, 11.5, "reason", "test", 12.5, "message"
            )

            assert (await bot_a_group.get("123", "900")).favor == 10
            record = await bot_a_group.get("123", "900")
            assert record.parsed_tags() == ["tag"]
            assert record.impression == "known"
            assert await bot_a_group.last_event_at("123", "900", "cooldown") == 12.5
            assert len(await bot_a_group.query_logs("123", "900")) == 1
            assert (await bot_a_global.get(GLOBAL_GROUP, "900")).favor == 20
            assert (await bot_b_group.get("123", "900")).favor == 30
            assert (await bot_b_global.get(GLOBAL_GROUP, "900")).favor == 40
            assert await bot_a_group.get(GLOBAL_GROUP, "900") is None
            assert await bot_a_global.get("123", "900") is None

            assert (tmp_path / "bots" / "100" / "group.db").is_file()
            assert (tmp_path / "bots" / "100" / "global.db").is_file()
            assert (tmp_path / "bots" / "200" / "group.db").is_file()
            assert (tmp_path / "bots" / "200" / "global.db").is_file()
        finally:
            await router.close()

    asyncio.run(run())


def test_concurrent_get_returns_single_initialized_instances(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            group_results = await asyncio.gather(
                *(router.get("300", "per_group") for _ in range(16))
            )
            global_results = await asyncio.gather(
                *(router.get("300", "global") for _ in range(16))
            )
            assert all(result is group_results[0] for result in group_results)
            assert all(result is global_results[0] for result in global_results)
            assert group_results[0] is not global_results[0]

            def get_from_thread():
                return asyncio.run(router.get("301", "per_group"))

            with ThreadPoolExecutor(max_workers=8) as executor:
                threaded = list(executor.map(lambda _item: get_from_thread(), range(8)))
            assert all(result is threaded[0] for result in threaded)
            assert (tmp_path / "bots" / "301" / "group.db").is_file()
            assert (tmp_path / "bots" / "301" / "global.db").is_file()
        finally:
            await router.close()

    asyncio.run(run())


def test_path_validation_and_internal_aliases(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            for bot_id in ("0", "", "１２３", "1" * 33, "../1", "1/2", "abc"):
                with pytest.raises(ValueError):
                    await router.get(bot_id, "per_group")
            with pytest.raises(ValueError):
                await router.get("123", "unknown")

            valid_dir = tmp_path / "bots" / "123"
            invalid_dir = tmp_path / "bots" / "invalid"
            valid_dir.mkdir(parents=True)
            invalid_dir.mkdir()
            (tmp_path / "bots" / "456").write_text("not a directory", encoding="ascii")
            assert router.known_bot_ids() == ["123"]

            outside = tmp_path / "outside"
            outside.mkdir()
            bot_alias = tmp_path / "bots" / "999"
            try:
                bot_alias.symlink_to(valid_dir, target_is_directory=True)
            except OSError:
                bot_alias = None
            if bot_alias is not None:
                with pytest.raises(ValueError):
                    await router.get("999", "per_group")
                assert "999" not in router.known_bot_ids()

            db_dir = tmp_path / "bots" / "124"
            db_dir.mkdir()
            (db_dir / "global.db").touch()
            db_alias = db_dir / "group.db"
            try:
                db_alias.symlink_to(db_dir / "global.db")
            except OSError:
                db_alias = None
            if db_alias is not None:
                with pytest.raises(ValueError):
                    await router.get("124", "per_group")

            await router.get("125", "per_group")
            backup_parent = tmp_path / "backups"
            try:
                backup_parent.symlink_to(tmp_path / "bots", target_is_directory=True)
            except OSError:
                backup_parent = None
            if backup_parent is not None:
                # The fixed backups parent must not be replaceable by an internal alias.
                with pytest.raises(ValueError):
                    await router.backup("125", "per_group")
        finally:
            await router.close()

    asyncio.run(run())


def test_global_observed_ranking_only_uses_original_group_members(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            backend = await router.get("100", "global")
            await backend.set_value(GLOBAL_GROUP, "u1", 80)
            await backend.set_value(GLOBAL_GROUP, "u2", 80)
            await backend.set_value(GLOBAL_GROUP, "u3", 100)
            await backend.observe_member("group-a", "u2")
            await backend.observe_member("group-a", "u1")
            await backend.observe_member("group-b", "u3")

            rows = await backend.ranking_observed("group-a", -1)
            assert [(row.user_id, row.favor) for row in rows] == [
                ("u1", 80),
                ("u2", 80),
            ]
            assert [row.user_id for row in await backend.list_observed("group-a")] == [
                "u1",
                "u2",
            ]
            assert await backend.observed_groups() == [
                {"group_id": "group-a", "count": 2},
                {"group_id": "group-b", "count": 1},
            ]
        finally:
            await router.close()

    asyncio.run(run())


def test_backup_restore_targets_one_database_and_keeps_backend_reference(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        try:
            target = await router.get("100", "per_group")
            other_scope = await router.get("100", "global")
            other_bot = await router.get("200", "per_group")
            await target.set_value("123", "900", 10)
            await other_scope.set_value(GLOBAL_GROUP, "900", 70)
            await other_bot.set_value("123", "900", 80)

            backup = await router.backup("100", "per_group")
            assert backup.parent == tmp_path / "backups" / "100"
            with sqlite3.connect(backup) as conn:
                assert (
                    conn.execute(
                        "SELECT favor FROM favor WHERE group_id=? AND user_id=?",
                        ("123", "900"),
                    ).fetchone()[0]
                    == 10
                )

            await target.set_value("123", "900", 90)
            global_backup = await router.backup("100", "global")
            with pytest.raises(ValueError):
                await router.restore("100", "per_group", global_backup.name)
            with pytest.raises(ValueError):
                await router.restore("100", "per_group", str(backup))

            await router.restore("100", "per_group", backup.name)
            assert await router.get("100", "per_group") is target
            assert (await target.get("123", "900")).favor == 10
            assert (await other_scope.get(GLOBAL_GROUP, "900")).favor == 70
            assert (await other_bot.get("123", "900")).favor == 80
            await target.set_value("123", "900", 11)
            assert (await target.get("123", "900")).favor == 11

            snapshots = list((tmp_path / "backups" / "100").glob("per_group-*.db"))
            assert len(snapshots) >= 2
            found_current_snapshot = False
            for path in snapshots:
                with sqlite3.connect(path) as conn:
                    value = conn.execute(
                        "SELECT favor FROM favor WHERE group_id=? AND user_id=?",
                        ("123", "900"),
                    ).fetchone()[0]
                found_current_snapshot = found_current_snapshot or value == 90
            assert found_current_snapshot
        finally:
            await router.close()

    asyncio.run(run())


def test_restore_refuses_busy_wal_and_keeps_current_backend(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        external = None
        try:
            backend = await router.get("100", "per_group")
            await backend.set_value("123", "900", 10)
            backup = await router.backup("100", "per_group")
            await backend.set_value("123", "900", 90)
            backend._c().execute("PRAGMA busy_timeout=50")

            db_path = tmp_path / "bots" / "100" / "group.db"
            external = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
            external.execute("PRAGMA busy_timeout=50")
            external.execute("BEGIN")
            external.execute(
                "SELECT favor FROM favor WHERE group_id=? AND user_id=?",
                ("123", "900"),
            ).fetchone()

            with pytest.raises(RuntimeError, match="WAL"):
                await router.restore("100", "per_group", backup.name)
            assert await router.get("100", "per_group") is backend
            assert (await backend.get("123", "900")).favor == 90
        finally:
            if external is not None:
                external.rollback()
                external.close()
            await router.close()

    asyncio.run(run())


def test_future_schema_is_rejected_for_init_and_restore(tmp_path):
    async def run():
        init_root = tmp_path / "init"
        future_db = init_root / "bots" / "100" / "group.db"
        future_db.parent.mkdir(parents=True)
        with sqlite3.connect(future_db) as conn:
            conn.execute("PRAGMA user_version(11)")

        init_router = BotScopeStorageRouter(init_root)
        try:
            with pytest.raises(ValueError, match="未来 schema"):
                await init_router.get("100", "per_group")
        finally:
            await init_router.close()

        router = BotScopeStorageRouter(tmp_path / "restore")
        try:
            backend = await router.get("100", "per_group")
            await backend.set_value("123", "900", 10)
            backup_dir = tmp_path / "restore" / "backups" / "100"
            backup_dir.mkdir(parents=True)
            future_backup = backup_dir / ("per_group-1-" + "0" * 32 + ".db")
            with sqlite3.connect(future_backup) as conn:
                conn.execute("PRAGMA user_version(11)")
            with pytest.raises(ValueError, match="未来 schema"):
                await router.restore("100", "per_group", future_backup.name)
            assert (await backend.get("123", "900")).favor == 10
        finally:
            await router.close()

    asyncio.run(run())
