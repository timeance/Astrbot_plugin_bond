"""有界上下文、模型语义弃权和持久化调用预算；全部使用合成平台与 Provider。"""

import asyncio
import json
import sqlite3
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from astrbot_plugin_bond.core.assessment import (
    Interaction,
    locally_relevant,
    parse_verdict,
)
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.services.assessment_service import (
    AssessmentCapacity,
    AssessmentService,
)
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage import assessment_ledger as ledger
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend
from astrbot_plugin_bond.tests.test_assessment import (
    Provider,
    interaction,
    setup,
    verdict,
)


@pytest.mark.parametrize("value", [False, True])
def test_optional_should_update_is_strict_boolean_and_needs_current_evidence(value):
    assert (
        parse_verdict(verdict(should_update=value), interaction()).should_update
        is value
    )
    assert parse_verdict(verdict(), interaction()).should_update is True
    with pytest.raises(ValueError):
        parse_verdict(verdict(should_update="false"), interaction())
    with pytest.raises(ValueError):
        parse_verdict(verdict(text="历史中说过", should_update=False), interaction())


def test_model_abstention_and_distinct_substantive_turns_in_same_window(tmp_path):
    async def run():
        provider = Provider(verdict(text="晚安", should_update=False))
        store, favor, service, _ = await setup(tmp_path, provider)
        try:
            # 同一句话不由框架判为语义重复，模型建议弃权后绝不改分。
            for index in range(2):
                assert (
                    await service.assess(
                        interaction("晚安", message=str(index)), directed=True
                    )
                    == "abstained"
                )
            assert await favor.query_logs("789", "456") == []
            provider.result = verdict(text="请解释事务", kind="normal_question")
            assert (
                await service.assess(
                    interaction("请解释事务", message="question-1"), directed=True
                )
                == "accepted"
            )
            provider.result = verdict(text="那么回滚呢", kind="normal_question")
            assert (
                await service.assess(
                    interaction("那么回滚呢", message="question-2"), directed=True
                )
                == "accepted"
            )
            state = await favor.get_relation("789", "456")
            assert state.familiarity > 0 and state.affinity == state.trust == 0
            assert provider.calls == 4
        finally:
            await store.close()

    asyncio.run(run())


def test_frozen_context_is_identical_for_review_and_isolated(tmp_path):
    async def run():
        seen = []

        class Capture(Provider):
            async def text_chat(self, prompt):
                seen.append(prompt)
                return await super().text_chat(prompt)

        small = Capture()
        large = Capture(verdict(text="你真笨", kind="insult"))
        store, _, service, _ = await setup(
            tmp_path, small, large, large_provider_id="large"
        )
        try:
            first = interaction(message="prior")
            assert await service.assess(first, directed=True) == "accepted"
            service.remember_bot_reply(first, "这是本群给你的实际回复")
            # 同一成员其他群、同群其他成员、其他 Bot 都不能混入。
            service._remember(
                replace(first, group_id="790"), "user", "其他群隐私", "secret"
            )
            service._remember(
                replace(first, user_id="457"), "user", "其他成员隐私", "secret"
            )
            service._remember(
                replace(first, bot_id="124"), "user", "其他Bot隐私", "secret"
            )
            small.result = verdict(text="你真笨", kind="insult")
            current = interaction("你真笨", message="current")
            current = replace(
                current,
                reply_message_id="source-1",
                reply_sender_id="123",
                reply_text="可信引用原文",
            )
            assert await service.assess(current, directed=True) == "accepted"
            assert seen[-1] == seen[-2]
            payload = json.loads(seen[-1].split("输入：", 1)[1])
            assert payload["trusted_metadata"]["reply"]["message_id"] == "source-1"
            assert payload["trusted_metadata"]["reply"]["text"] == "可信引用原文"
            assert [t["role"] for t in payload["recent_turns"]] == ["user", "bot"]
            assert payload["scored_events"]
            assert all(
                secret not in seen[-1]
                for secret in ("其他群隐私", "其他成员隐私", "其他Bot隐私")
            )
        finally:
            await store.close()

    asyncio.run(run())


def test_window_and_call_dedup_span_scopes_groups_and_restart(tmp_path):
    async def run():
        store, _, service, providers = await setup(tmp_path)
        other = SQLiteBackend(tmp_path / "123" / "global.db")
        await other.init()

        async def stores():
            return [store, other]

        service.budget_stores = stores
        other_service = AssessmentService(
            other,
            FavorService(other, LevelTable.from_config(None), scope_mode="global"),
            service.context,
            service.config,
            service.capacity,
            stores,
        )
        try:
            for index in range(5):
                current = replace(
                    interaction(message=str(index)), group_id=str(800 + index)
                )
                selected = service if index % 2 else other_service
                assert await selected.assess(current, directed=True) == "accepted"
            assert (
                await service.assess(interaction(message="sixth"), directed=True)
                == "failed"
            )
            assert providers["small"].calls == 5
            await store.close()
            await other.close()
            await store.init()
            await other.init()
            # 重建内存容量不能清空持久化滚动窗口。
            service.capacity = AssessmentCapacity(2)
            assert (
                await service.assess(
                    interaction(message="after-restart"), directed=True
                )
                == "failed"
            )
            assert providers["small"].calls == 5
        finally:
            await store.close()
            await other.close()

    asyncio.run(run())


def test_call_budget_is_atomic_across_concurrent_scopes(tmp_path):
    async def run():
        gate = asyncio.Event()
        provider = Provider(pause=gate)
        store, _, service, _ = await setup(
            tmp_path, provider, per_bot_daily_small_cap=1
        )
        other = SQLiteBackend(tmp_path / "123" / "global.db")
        await other.init()

        async def stores():
            return [store, other]

        service.budget_stores = stores
        service2 = AssessmentService(
            other,
            FavorService(other, LevelTable.from_config(None), scope_mode="global"),
            service.context,
            service.config,
            service.capacity,
            stores,
        )
        try:
            task = asyncio.create_task(service.assess(interaction(), directed=True))
            await asyncio.sleep(0.01)
            assert (
                await service2.assess(
                    interaction(user="457", message="other"), directed=True
                )
                == "failed"
            )
            assert provider.calls == 1
            gate.set()
            assert await task == "accepted"
        finally:
            gate.set()
            await store.close()
            await other.close()

    asyncio.run(run())


def test_missing_provider_and_filtered_message_do_not_consume_calls(tmp_path):
    async def run():
        store, _, service, _ = await setup(tmp_path)
        try:
            service.context = SimpleNamespace(get_provider_by_id=lambda _: None)
            assert await service.assess(interaction(), directed=True) == "failed"
            assert (
                await service.assess(
                    interaction(message="not-directed"), directed=False
                )
                == "filtered"
            )
            assert (await service.stats())["calls"] == []
        finally:
            await store.close()

    asyncio.run(run())


def test_ledger_window_crosses_utc_day_and_daily_budget_survives_reopen(tmp_path):
    path = tmp_path / "calls.db"
    conn = sqlite3.connect(path)
    lock = threading.RLock()
    conn.execute(
        "CREATE TABLE assessment_call(id INTEGER PRIMARY KEY,event_id TEXT,group_id TEXT,user_id TEXT,day TEXT,tier TEXT,provider_id TEXT,status TEXT,tokens INTEGER,started_at REAL DEFAULT 0)"
    )
    conn.commit()
    try:
        assert ledger.reserve_call(
            conn, lock, "a", "1", "2", "small", "p", 30, 300, now=86399
        )
        assert ledger.call_counts(conn, lock, "2", "small", now=86401) == (0, 0, 1)
        assert (
            ledger.reserve_call(
                conn,
                lock,
                "b",
                "1",
                "2",
                "small",
                "p",
                30,
                300,
                now=86401,
                window_max_calls=1,
            )
            is None
        )
        call = ledger.reserve_call(
            conn, lock, "c", "1", "2", "small", "p", 1, 300, now=86700
        )
        assert call
        ledger.finish_call(conn, lock, call, "timeout", None)
        conn.close()
        conn = sqlite3.connect(path)
        assert ledger.call_counts(conn, lock, "2", "small", now=87001) == (1, 1, 0)
        assert (
            ledger.reserve_call(
                conn, lock, "d", "1", "2", "small", "p", 1, 300, now=87001
            )
            is None
        )
        # 失败也记账；复核不占初评窗口但有独立额度。
        assert ledger.reserve_call(
            conn, lock, "c", "1", "2", "large", "p", 8, 100, now=87001
        )
        assert ledger.call_counts(conn, lock, "2", "large", now=87001) == (1, 1, 0)
    finally:
        conn.close()


def test_context_memory_is_bounded_and_does_not_semantically_dedup(tmp_path):
    async def run():
        store, _, service, _ = await setup(tmp_path)
        try:
            for index in range(130):
                current = interaction(user=str(1000 + index))
                service._remember(current, "user", "重复文字", str(index))
            assert len(service.capacity.recent) == 128
            current = interaction()
            for index in range(30):
                service._remember(current, "user", "完全相同的文字", str(index))
            turns = service.capacity.recent[("123", "789", "456")]
            assert len(turns) == 20
        finally:
            await store.close()

    asyncio.run(run())


def test_listener_freezes_trusted_reply_text_and_bot_response():
    from astrbot_plugin_bond.tests.research_support import Event, offline_listener

    with offline_listener() as (listener, components, _):
        event = Event(
            {
                "bot": "10001",
                "text": "这次回答不对",
                "message_id": "current",
                "chain": [
                    (
                        "Reply",
                        {
                            "sender_id": "10001",
                            "id": "source",
                            "message_str": "Bot 原文",
                        },
                    )
                ],
            },
            components,
        )
        assert listener._reply_metadata(event) == dict(
            reply_message_id="source", reply_sender_id="10001", reply_text="Bot 原文"
        )
        captured = []
        assessment = SimpleNamespace(
            config=SimpleNamespace(enabled=True),
            remember_bot_reply=lambda *args: captured.append(args),
        )
        deps = SimpleNamespace(assessment=assessment)
        frozen = Interaction(
            "10001", event.group, event.user, "synthetic", "current", event.message_str
        )
        event._bond_assessment_binding = (deps, frozen)
        asyncio.run(
            listener.on_llm_response(
                deps, event, SimpleNamespace(completion_text="Bot 后续回复")
            )
        )
        assert captured[0][0].bot_id == "10001"
        assert captured[0][1] == "Bot 后续回复"
        event.message_obj.message = [
            SimpleNamespace(sender_id="10001", id="source", message_str="伪组件")
        ]
        assert listener._reply_metadata(event) == {}
        asyncio.run(
            listener.on_llm_response(
                SimpleNamespace(assessment=assessment),
                event,
                SimpleNamespace(completion_text="不能进入"),
            )
        )
        assert len(captured) == 1


def test_context_limits_cover_history_and_current_reply(tmp_path):
    async def run():
        store, _, service, _ = await setup(tmp_path)
        try:
            service.config = replace(service.config, context_chars=200, context_turns=2)
            prior = interaction(message="prior")
            service._remember(prior, "user", "过长历史" * 100, prior.message_id)
            payload = json.loads(
                await service._snapshot(interaction(message="current"))
            )
            assert payload["recent_turns"] == []
            assert payload["text"] == "谢谢你的帮助"
            current = replace(
                interaction(message="reply"),
                reply_message_id="source",
                reply_sender_id="123",
                reply_text="原文" * 1000,
            )
            payload = json.loads(await service._snapshot(current))
            assert len(payload["trusted_metadata"]["reply"]["text"]) == 200
            assert payload["recent_turns"] == [] and payload["scored_events"] == []
        finally:
            await store.close()

    asyncio.run(run())


def test_stable_call_id_cannot_be_replayed_by_switching_scope(tmp_path):
    async def run():
        store, _, service, providers = await setup(tmp_path)
        other = SQLiteBackend(tmp_path / "123" / "global.db")
        await other.init()

        async def stores():
            return [store, other]

        service.budget_stores = stores
        second = AssessmentService(
            other,
            FavorService(other, LevelTable.from_config(None), scope_mode="global"),
            service.context,
            service.config,
            service.capacity,
            stores,
        )
        try:
            assert await service.assess(interaction(), directed=True) == "accepted"
            assert await second.assess(interaction(), directed=True) == "failed"
            assert providers["small"].calls == 1
            assert await other.get("789", "456") is None
        finally:
            await store.close()
            await other.close()

    asyncio.run(run())


def test_different_bots_have_independent_daily_budgets(tmp_path):
    async def run():
        store, _, service, providers = await setup(tmp_path, per_bot_daily_small_cap=1)
        other = SQLiteBackend(tmp_path / "124" / "group.db")
        await other.init()

        async def stores():
            return [other]

        second = AssessmentService(
            other,
            FavorService(other, LevelTable.from_config(None)),
            service.context,
            service.config,
            service.capacity,
            stores,
        )
        try:
            assert await service.assess(interaction(), directed=True) == "accepted"
            assert (
                await service.assess(interaction(message="over"), directed=True)
                == "failed"
            )
            assert (
                await second.assess(interaction(bot="124"), directed=True) == "accepted"
            )
            assert providers["small"].calls == 2
        finally:
            await store.close()
            await other.close()

    asyncio.run(run())


def test_response_before_background_uses_frozen_admission(tmp_path):
    from astrbot_plugin_bond.tests.research_support import Event, offline_listener

    async def run():
        store, _, service, _ = await setup(tmp_path)
        pending = []
        registry = SimpleNamespace(
            spawn=lambda coroutine, **kw: pending.append(coroutine)
        )
        try:
            with offline_listener() as (listener, components, _):
                event = Event(
                    dict(
                        bot="123",
                        group="789",
                        user="456",
                        text="谢谢你的帮助",
                        message_id="before-bg",
                        chain=[("At", dict(qq="123"))],
                    ),
                    components,
                )
                deps = SimpleNamespace(
                    assessment=service,
                    favor=service.favor,
                    registry=registry,
                    impressions=None,
                )
                await listener.on_group_message(deps, event)
                assert event._bond_assessment_binding[0] is deps
                assert pending
                # 模式/原事件变化不能让响应被重新归到另一群、成员或 Bot。
                event.group, event.user, event.bot = "790", "457", "124"
                await listener.on_llm_response(
                    deps, event, SimpleNamespace(completion_text="先于后台到达的回复")
                )
                assert [
                    t["role"] for t in service.capacity.recent[("123", "789", "456")]
                ] == ["user", "bot"]
                snapshot = json.loads(
                    await service._snapshot(interaction(message="before-bg"))
                )
                # 当前评估不使用对当前消息刚生成的回复，避免反馈污染。
                assert snapshot["recent_turns"] == []
                next_turn = json.loads(
                    await service._snapshot(interaction(message="next"))
                )
                assert any(
                    t["text"] == "先于后台到达的回复" for t in next_turn["recent_turns"]
                )
        finally:
            for coroutine in pending:
                coroutine.close()
            await store.close()

    asyncio.run(run())


def test_cross_day_repeated_thanks_uses_scored_context_and_model_abstention(
    tmp_path, monkeypatch
):
    async def run():
        clock = [1767225600.0]
        monkeypatch.setattr(ledger.time, "time", lambda: clock[0])
        seen = []

        class RepeatProvider(Provider):
            async def text_chat(self, prompt):
                seen.append(json.loads(prompt.split("输入：", 1)[1]))
                return await super().text_chat(prompt)

        provider = RepeatProvider()
        store, favor, service, _ = await setup(tmp_path, provider)
        try:
            assert (
                await service.assess(interaction(message="day-1"), directed=True)
                == "accepted"
            )
            before = await favor.get_relation("789", "456")
            clock[0] += 86400
            provider.result = verdict(should_update=False)
            assert (
                await service.assess(interaction(message="day-2"), directed=True)
                == "abstained"
            )
            assert seen[-1]["scored_events"][0]["message"] == "谢谢"
            after = await favor.get_relation("789", "456")
            assert (
                after.familiarity == before.familiarity and after.trust == before.trust
            )
            assert (
                provider.calls == 2 and len(await favor.query_logs("789", "456")) == 1
            )
        finally:
            await store.close()

    asyncio.run(run())


def test_insult_then_apology_changes_affinity_without_changing_trust(tmp_path):
    async def run():
        provider = Provider(verdict("你真笨", "insult"))
        store, favor, service, _ = await setup(
            tmp_path,
            provider,
            Provider(verdict("你真笨", "insult")),
            large_provider_id="large",
        )
        try:
            assert (
                await service.assess(
                    interaction("你真笨", message="insult"), directed=True
                )
                == "accepted"
            )
            before = await favor.get_relation("789", "456")
            assert before.affinity < 0
            assert before.trust == 0
            provider.result = verdict("对不起", "apology")
            assert (
                await service.assess(
                    interaction("对不起", message="apology"), directed=True
                )
                == "accepted"
            )
            after = await favor.get_relation("789", "456")
            assert after.affinity > before.affinity
            assert after.trust == before.trust
        finally:
            await store.close()

    asyncio.run(run())


def test_initial_timeout_consumes_initial_daily_budget(tmp_path):
    async def run():
        provider = Provider(pause=asyncio.Event())
        store, _, service, _ = await setup(
            tmp_path, provider, small_timeout_sec=0.01, per_user_daily_small_cap=1
        )
        try:
            assert await service.assess(interaction(), directed=True) == "failed"
            assert (await service.stats())["calls"][0]["status"] == "timeout"
            assert (
                await service.assess(
                    interaction(message="after-timeout"), directed=True
                )
                == "failed"
            )
            assert provider.calls == 1
            assert (await store.assessment_call_counts("456", "small"))[:2] == (1, 1)
        finally:
            await store.close()

    asyncio.run(run())
