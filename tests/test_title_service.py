"""真实 SQLite + 可控 Provider；不向外发送人格或群聊资料。"""

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_policy import RelationResolver, RelationState
from astrbot_plugin_bond.core.relation_titles import (
    TitleConfig,
    allowed_titles,
    parse_title,
)
from astrbot_plugin_bond.core.taskregistry import TaskRegistry
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.services.title_service import TitleCapacity, TitleService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend


class Event:
    unified_msg_origin = "test:GroupMessage:789"

    def get_self_id(self):
        return "123"

    def get_group_id(self):
        return "789"

    def get_sender_id(self):
        return "456"

    def get_platform_name(self):
        return "test"


class Judge:
    persona = "完整人格：从容而亲切。明确任务可靠完成；不承诺排他关系。"

    async def resolve_persona(self, umo, platform):
        return "合成人格", self.persona


class Provider:
    def __init__(self):
        self.calls, self.inputs, self.pause, self.output = 0, [], None, None
        self.started = asyncio.Event()

    async def text_chat(self, prompt):
        self.calls += 1
        payload = json.loads(prompt.split("\n", 1)[1])
        self.inputs.append(payload)
        self.started.set()
        if self.pause:
            await self.pause.wait()
        return SimpleNamespace(
            completion_text=self.output
            or json.dumps({"title": payload["allowed_titles"][-1]})
        )


async def setup(tmp_path, *, scope="per_group", **opts):
    db = SQLiteBackend(tmp_path / "123" / (scope + ".db"))
    await db.init()
    favor = FavorService(db, LevelTable.from_config(None), scope_mode=scope)
    await favor.set_relation(
        "789", "456", familiarity=50, affinity=25, trust=25, event_id="seed"
    )
    provider, judge, registry = Provider(), Judge(), TaskRegistry()
    service = TitleService(
        db,
        favor,
        judge,
        SimpleNamespace(get_provider_by_id=lambda _: provider),
        registry,
        TitleConfig(mode="llm", provider_id="title", **opts),
        TitleCapacity(),
        None,
    )
    favor.bind_titles(service)
    return db, favor, service, provider, judge


def test_cached_query_restart_persona_and_negative_invalidation(tmp_path):
    async def run():
        db, favor, service, provider, judge = await setup(tmp_path)
        event = Event()
        assert (await favor.read_relation_title("789", "456", event=event))[
            "source"
        ] == "fixed"
        assert provider.calls == 0
        await service._generate(event, 0)
        first = await favor.read_relation_title("789", "456", event=event)
        assert first["source"] == "cache"
        assert provider.inputs[0]["persona"] == judge.persona
        await db.close()
        await db.init()
        assert await favor.read_relation_title("789", "456", event=event) == first
        await service._generate(event, 0)
        assert provider.calls == 1
        judge.persona += "新版人格"
        assert (await favor.read_relation_title("789", "456", event=event))[
            "source"
        ] == "fixed"
        await service._generate(event, 0)
        assert provider.calls == 1  # 冷却可以阻止生成，不能阻止旧缓存失效。
        await favor.set_relation(
            "789", "456", familiarity=80, affinity=65, trust=-60, event_id="guarded"
        )
        assert await favor.relation_title("789", "456", event=event) == "亲密却戒备"
        assert provider.calls == 1
        await db.close()

    asyncio.run(run())


def test_no_cross_context_or_global_impression(tmp_path):
    async def run():
        db, favor, service, provider, _ = await setup(tmp_path, scope="global")
        await db.set_impression(favor.group_key("789"), "456", "另一个群的私人内容", [])
        await service._generate(Event(), 0)
        assert provider.inputs[0]["impression"] == ""
        assert (await favor.read_relation_title("789", "456"))["source"] == "fixed"
        other = Event()
        other.unified_msg_origin = "another:GroupMessage:789"
        assert (await favor.read_relation_title("789", "456", event=other))[
            "source"
        ] == "fixed"
        other.get_self_id = lambda: "124"
        assert (await favor.read_relation_title("789", "456", event=other))[
            "source"
        ] == "fixed"
        await db.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "change", ["score", "persona", "temporary", "invalidate", "restore", "clear"]
)
def test_inflight_result_cannot_overwrite_new_state(tmp_path, change):
    async def run():
        db, favor, service, provider, judge = await setup(tmp_path)
        provider.pause = asyncio.Event()
        service.schedule(Event())
        await provider.started.wait()
        service.schedule(Event())
        assert service.registry.size == 1
        if change == "score":
            await favor.set_relation(
                "789", "456", familiarity=55, affinity=25, trust=25, event_id="changed"
            )
        elif change == "persona":
            judge.persona = "新的完整人格"
        elif change == "temporary":
            await favor.set_temporary(
                "789", "456", dict(familiarity=50, affinity=25, trust=25), 60, "test"
            )
        elif change == "restore":
            backup = tmp_path / "title-backup.db"
            await db._backup_to(backup)
            await db._restore_from(backup)
        elif change == "clear":
            await db.clear_title_cache()
        else:
            service.invalidate()
        provider.pause.set()
        while service.capacity.active:
            await asyncio.sleep(0)
        assert (await favor.read_relation_title("789", "456", event=Event()))[
            "source"
        ] == "fixed"
        assert provider.calls == 1
        await db.close()

    asyncio.run(run())


@pytest.mark.parametrize("fail_clear", [False, True])
def test_restore_discards_saved_titles_or_rolls_back_whole_database(
    tmp_path, fail_clear
):
    async def run():
        db, favor, service, _, _ = await setup(tmp_path)
        await service._generate(Event(), 0)
        cached = await favor.read_relation_title("789", "456", event=Event())
        assert cached["source"] == "cache"
        backup = tmp_path / "with-title.db"
        await db._backup_to(backup)
        await favor.set_relation(
            "789", "456", familiarity=60, affinity=25, trust=25, event_id="after-backup"
        )
        original_open = db._open_connection
        opened = 0

        def open_with_failure():
            nonlocal opened
            conn = original_open()
            opened += 1
            if fail_clear and opened == 1:
                conn.set_authorizer(
                    lambda action, table, *args: (
                        sqlite3.SQLITE_DENY
                        if action == sqlite3.SQLITE_DELETE
                        and table == "relation_title_cache"
                        else sqlite3.SQLITE_OK
                    )
                )
            return conn

        with patch.object(db, "_open_connection", side_effect=open_with_failure):
            if fail_clear:
                with pytest.raises(sqlite3.DatabaseError):
                    await db._restore_from(backup)
                assert (await favor.get_relation("789", "456")).familiarity == 60
                assert (await favor.read_relation_title("789", "456", event=Event()))[
                    "source"
                ] == "cache"
            else:
                await db._restore_from(backup)
                assert (await favor.get_relation("789", "456")).familiarity == 50
                assert (await favor.read_relation_title("789", "456", event=Event()))[
                    "source"
                ] == "fixed"
        await db.close()

    asyncio.run(run())


def test_revocation_and_temporary_end_do_not_resurrect_cache(tmp_path):
    async def run():
        db, favor, service, _, _ = await setup(tmp_path)
        await service._generate(Event(), 0)
        assert (await favor.read_relation_title("789", "456", event=Event()))[
            "source"
        ] == "cache"
        await favor.set_temporary(
            "789", "456", dict(familiarity=50, affinity=25, trust=25), 60, "test"
        )
        await favor.set_temporary("789", "456", None, 0, "test")
        assert (await favor.read_relation_title("789", "456", event=Event()))[
            "source"
        ] == "fixed"
        await db.clear_title_cache()
        await service._generate(Event(), 0)
        logs = await favor.query_logs("789", "456")
        await db.mark_reversed(logs[0]["id"])
        assert (await favor.read_relation_title("789", "456", event=Event()))[
            "source"
        ] == "fixed"
        await db.close()

    asyncio.run(run())


def test_failed_calls_and_bot_wide_budget_persist(tmp_path):
    async def run():
        db, favor, service, provider, _ = await setup(
            tmp_path, daily_generation_limit=1
        )
        provider.output = '{"title":"主人", "trust":100}'
        await service._generate(Event(), 0)
        day = datetime.now(timezone.utc).date().isoformat()
        assert await db.title_day_calls(day) == 1
        assert (await favor.read_relation_title("789", "456", event=Event()))[
            "source"
        ] == "fixed"
        other_db, _, other, second, _ = await setup(
            tmp_path, scope="global", daily_generation_limit=1
        )

        async def stores():
            return [db, other_db]

        other.budget_stores = stores
        other.capacity = service.capacity
        await other._generate(Event(), 0)
        assert second.calls == 0
        await db.close()
        await db.init()
        assert await db.title_day_calls(day) == 1
        await db.close()
        await other_db.close()

    asyncio.run(run())


def test_matching_cached_title_does_not_spend_budget_on_new_logs(tmp_path):
    async def run():
        db, favor, service, provider, _ = await setup(tmp_path)
        await service._generate(Event(), 0)
        first = await favor.read_relation_title("789", "456", event=Event())
        with patch("time.time", return_value=time.time() + 7 * 3600):
            await favor.set_relation(
                "789", "456", familiarity=55, affinity=26, trust=26, event_id="new-fact"
            )
            await service._generate(Event(), 0)
            assert await favor.read_relation_title("789", "456", event=Event()) == first
        assert provider.calls == 1
        await db.close()

    asyncio.run(run())


@pytest.mark.parametrize("whole_group", [False, True])
def test_reset_erases_titles_but_keeps_spent_call_budget(tmp_path, whole_group):
    async def run():
        db, favor, service, provider, _ = await setup(tmp_path)
        await service._generate(Event(), 0)
        await favor.set_relation(
            "789", "457", familiarity=50, affinity=25, trust=25, event_id="other-member"
        )
        other = Event()
        other.get_sender_id = lambda: "457"
        await service._generate(other, 0)
        await db.reset("789", None if whole_group else "456")
        rows = db._c().execute("SELECT user_id FROM relation_title_cache").fetchall()
        assert rows == ([] if whole_group else [("457",)])
        assert (
            await db.title_day_calls(datetime.now(timezone.utc).date().isoformat()) == 2
        )
        assert provider.calls == 2
        await db.close()

    asyncio.run(run())


@pytest.mark.parametrize("revoke", ["mark", "undo"])
def test_new_confirmed_impression_can_follow_a_revocation(tmp_path, revoke):
    async def run():
        db, favor, service, provider, _ = await setup(tmp_path)
        await favor.apply_relation_event(
            "789", "456", "normal_question", event_id="old-fact"
        )
        log = (await favor.query_logs("789", "456"))[0]
        await db.set_impression("789", "456", "待复核的旧印象", [])
        await service._generate(Event(), 0)
        if revoke == "mark":
            await db.mark_reversed(log["id"])
        else:
            await favor.undo_log(log["id"])
        assert (await db.title_state("789", "456", "789"))["impression"] == ""
        assert (await db.get("789", "456")).impression == "待复核的旧印象"
        await db.set_profile_audited("789", "456", "重新核对后的印象", [], "test-admin")
        with patch("time.time", return_value=time.time() + 7 * 3600):
            await service._generate(Event(), 0)
        assert provider.calls == 2
        assert provider.inputs[-1]["impression"] == "重新核对后的印象"
        assert (await favor.read_relation_title("789", "456", event=Event()))[
            "source"
        ] == "cache"
        await db.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [
        '{"title":"某人的私密地址"}',
        '{"title":"永远只属于你的恋人"}',
        '{"title":"可信伙伴","affinity":90}',
        '```json\n{"title":"熟人"}\n```',
    ],
)
def test_output_contract_rejects_private_fabricated_and_wrong_format(text):
    desc = RelationResolver().describe(RelationState(50, 25, 25))
    with pytest.raises(ValueError):
        parse_title(text, desc)


def test_mixed_relationships_never_lose_guard_or_distance():
    for state in ((70, 60, -20), (70, -20, 60), (80, -45, 0), (50, -20, -50)):
        assert allowed_titles(RelationResolver().describe(RelationState(*state))) == ()
