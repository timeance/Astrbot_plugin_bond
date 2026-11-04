"""真实群消息入口到印象服务的全局进度、隔离与失败重试。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.scope import GLOBAL_GROUP
from astrbot_plugin_bond.core.taskregistry import TaskRegistry
from astrbot_plugin_bond.services.assessment_service import (
    AssessmentCapacity,
    AssessmentConfig,
    AssessmentService,
)
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.services.impression_service import ImpressionService
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter
from astrbot_plugin_bond.tests.research_support import Clock, Event, offline_listener

USER = "8001"
TEXT = "请解释这个问题"


class Summary:
    def __init__(self):
        self.prompts = []
        self.result = "印象:愿意认真交流\n标签:友善"
        self.error = None

    async def resolve_display_name(self, umo):
        return "合成 Bot"

    async def resolve_summary_provider(self):
        return self

    async def text_chat(self, *, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return SimpleNamespace(completion_text=self.result)


async def make_deps(router, listener, bot="10001", mode="global"):
    store = await router.get(bot, mode)
    favor = FavorService(store, LevelTable.from_config(None), scope_mode=mode)
    registry, summary = TaskRegistry(), Summary()

    async def assess_text(*, prompt):
        return SimpleNamespace(
            completion_text=json.dumps(
                dict(
                    event_type="normal_question",
                    evidence=TEXT,
                    confidence=0.99,
                    should_update=True,
                )
            )
        )

    async def budget_stores():
        return [store]

    assessment = AssessmentService(
        store,
        favor,
        SimpleNamespace(
            get_provider_by_id=lambda _: SimpleNamespace(text_chat=assess_text)
        ),
        AssessmentConfig(enabled=True, small_provider_id="synthetic"),
        AssessmentCapacity(),
        budget_stores,
    )
    return (
        listener.Deps(
            favor=favor,
            judge=summary,
            inject=None,
            registry=registry,
            impressions=ImpressionService(
                store, registry=registry, summarizer=summary, scope_mode=mode
            ),
            assessment=assessment,
            bot_id=bot,
        ),
        store,
    )


async def send(listener, components, clock, deps, group, count=1):
    async def drain():
        while deps.registry.size:
            await asyncio.gather(*tuple(deps.registry._tasks))

    for _ in range(count):
        # 避开评估冷却与调用窗口，让印象阈值接受真实有效评估的检验。
        clock.value += 600
        event = Event(
            dict(
                bot=deps.bot_id,
                group=group,
                user=USER,
                text=TEXT,
                message_id=f"impression-{clock.value}",
                chain=[("At", dict(qq=deps.bot_id))],
            ),
            components,
        )
        await listener.on_group_message(deps, event)
        await asyncio.wait_for(drain(), timeout=5)


def progress(store, group):
    return (
        store._c()
        .execute(
            "SELECT log_id FROM impression_progress WHERE group_id=? AND user_id=?",
            (group, USER),
        )
        .fetchone()
    )


async def seed_previous(store):
    await store.add_log(
        GLOBAL_GROUP, USER, 0, 0, 0, "已汇总", "judge", 1, message="过往已汇总样本"
    )
    log_id = (await store.impression_logs(GLOBAL_GROUP, USER, 1))[0]["id"]
    assert await store.commit_impression(
        GLOBAL_GROUP, USER, "原有印象", ["保留"], [log_id]
    )
    return progress(store, GLOBAL_GROUP)


def test_global_listener_uses_eight_new_valid_judges_across_groups(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        clock = Clock()
        with offline_listener() as (listener, components, _), clock.frozen():
            deps, store = await make_deps(router, listener)
            try:
                previous = await seed_previous(store)
                for source, sample in (
                    ("admin", "管理员操作不作样本"),
                    ("judge", "已撤销评估不作样本"),
                ):
                    await store.add_log(
                        GLOBAL_GROUP, USER, 0, 0, 0, "排除", source, 2, message=sample
                    )
                store._c().execute(
                    "UPDATE favor_log SET reversed=1 WHERE message=?",
                    ("已撤销评估不作样本",),
                )
                store._c().commit()
                await send(listener, components, clock, deps, "7001", 4)
                await send(listener, components, clock, deps, "7002", 3)
                pending = await store.impression_logs(GLOBAL_GROUP, USER, 100)
                assert len(pending) == 7
                assert all(
                    r["source"] == "judge" and not r["reversed"] for r in pending
                )
                assert deps.judge.prompts == []
                assert progress(store, GLOBAL_GROUP) == previous

                await send(listener, components, clock, deps, "7002")
                assert len(deps.judge.prompts) == 1
                prompt = deps.judge.prompts[0]
                assert prompt.count(TEXT) == 8
                for excluded in (
                    "过往已汇总样本",
                    "管理员操作不作样本",
                    "已撤销评估不作样本",
                ):
                    assert excluded not in prompt
                assert await store.impression_logs(GLOBAL_GROUP, USER, 100) == []
                first = await deps.favor.get("7001", USER)
                second = await deps.favor.get("7002", USER)
                assert first.impression == second.impression == "愿意认真交流"
                assert progress(store, GLOBAL_GROUP)[0] > previous[0]

                await send(listener, components, clock, deps, "7001", 7)
                assert len(deps.judge.prompts) == 1
                assert len(await store.impression_logs(GLOBAL_GROUP, USER, 100)) == 7
                await send(listener, components, clock, deps, "7002")
                assert len(deps.judge.prompts) == 2
                assert await store.impression_logs(GLOBAL_GROUP, USER, 100) == []
            finally:
                await deps.registry.cancel_and_wait_all()
                await router.close()

    asyncio.run(run())


def test_listener_impression_progress_isolated_by_bot_and_group_scope(tmp_path):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        clock = Clock()
        with offline_listener() as (listener, components, _), clock.frozen():
            global_a, store_a = await make_deps(router, listener)
            global_b, store_b = await make_deps(router, listener, bot="10002")
            local, local_store = await make_deps(router, listener, mode="per_group")
            try:
                await send(listener, components, clock, global_a, "7001", 7)
                await send(listener, components, clock, global_b, "7001")
                await send(listener, components, clock, local, "7001", 4)
                await send(listener, components, clock, local, "7002", 4)
                assert all(not d.judge.prompts for d in (global_a, global_b, local))

                await send(listener, components, clock, global_a, "7002")
                assert len(global_a.judge.prompts) == 1
                assert progress(store_a, GLOBAL_GROUP) is not None
                assert progress(store_b, GLOBAL_GROUP) is None
                assert progress(local_store, "7001") is None
                assert progress(local_store, "7002") is None
                assert (await local.favor.get("7001", USER)).impression == ""
                assert (await global_b.favor.get("7001", USER)).impression == ""

                await send(listener, components, clock, local, "7001", 4)
                assert len(local.judge.prompts) == 1
                assert progress(local_store, "7001") is not None
                assert progress(local_store, "7002") is None
                assert len(await local_store.impression_logs("7002", USER, 100)) == 4
                await send(listener, components, clock, global_b, "7002", 7)
                assert len(global_b.judge.prompts) == 1
                assert await store_b.impression_logs(GLOBAL_GROUP, USER, 100) == []
            finally:
                for deps in (global_a, global_b, local):
                    await deps.registry.cancel_and_wait_all()
                await router.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["invalid", "provider"])
def test_global_listener_failed_summary_preserves_progress_for_retry(tmp_path, failure):
    async def run():
        router = BotScopeStorageRouter(tmp_path)
        clock = Clock()
        with offline_listener() as (listener, components, _), clock.frozen():
            deps, store = await make_deps(router, listener)
            try:
                previous = await seed_previous(store)
                if failure == "invalid":
                    deps.judge.result = "无法解析的合成输出"
                else:
                    deps.judge.error = RuntimeError("合成模型故障")
                await send(listener, components, clock, deps, "7001", 4)
                await send(listener, components, clock, deps, "7002", 4)
                pending = await store.impression_logs(GLOBAL_GROUP, USER, 100)
                assert len(deps.judge.prompts) == 1
                assert len(pending) == 8
                assert progress(store, GLOBAL_GROUP) == previous
                record = await deps.favor.get("7001", USER)
                assert record.impression == "原有印象"
                assert record.parsed_tags() == ["保留"]

                deps.judge.error = None
                deps.judge.result = "印象:重试成功\n标签:友善"
                await send(listener, components, clock, deps, "7002")
                assert len(deps.judge.prompts) == 2
                assert progress(store, GLOBAL_GROUP) == (max(r["id"] for r in pending),)
                assert len(await store.impression_logs(GLOBAL_GROUP, USER, 100)) == 1
                assert (await deps.favor.get("7002", USER)).impression == "重试成功"
            finally:
                await deps.registry.cancel_and_wait_all()
                await router.close()

    asyncio.run(run())
