"""人工资料修改与审计必须一起成功，且不改变关系状态。"""

import asyncio
import sqlite3
import pytest
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend


@pytest.mark.parametrize("broken", [False, True])
def test_manual_profile_transaction_preserves_scores(tmp_path, broken):
    async def run():
        store = SQLiteBackend(tmp_path / "group.db")
        await store.init()
        service = FavorService(store, LevelTable.from_config(None))
        try:
            await service.set_relation(
                "123", "456", familiarity=40, affinity=-20, trust=30, event_id="seed"
            )
            if broken:
                store._c().execute(
                    "CREATE TRIGGER reject_profile BEFORE INSERT ON favor_log WHEN NEW.source='admin_profile' BEGIN SELECT RAISE(ABORT, 'fixture'); END"
                )
                store._c().commit()
                with pytest.raises(sqlite3.IntegrityError):
                    await service.set_profile(
                        "123",
                        "456",
                        impression="熟人",
                        tags=["直率"],
                        actor="fixture-admin",
                    )
            else:
                await service.set_profile(
                    "123",
                    "456",
                    impression="熟人",
                    tags=["直率"],
                    actor="fixture-admin",
                )
            record = await service.get("123", "456")
            assert (record.familiarity, record.affinity, record.trust) == (40, -20, 30)
            assert record.impression == ("" if broken else "熟人")
            logs = await service.query_logs("123", "456")
            assert len(logs) == (1 if broken else 2)
            if not broken:
                assert "fixture-admin" in logs[0]["reason"]
                assert logs[0]["source"] == "admin_profile"
        finally:
            await store.close()

    asyncio.run(run())
