"""在场缓存与真实监听器契约；使用离线框架类型，不连接平台或模型。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.core.passive_familiarity import PassiveFamiliarityConfig
from astrbot_plugin_bond.core.taskregistry import TaskRegistry
from astrbot_plugin_bond.core.settings import snapshot, validate_patch
from astrbot_plugin_bond.services.passive_activity import ActivityBatch, PassiveActivity
from astrbot_plugin_bond.services.runtime_service import RuntimeService
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter
from astrbot_plugin_bond.tests.research_support import Event, offline_listener
from astrbot_plugin_bond.tests.test_relation_snapshot import drain, make_deps

NOW = 1_800_000_000.0


def observe(service, user="8001", group="7001", mid="m1", **kw):
    return service.observe(
        group,
        user,
        platform="synthetic",
        message_id=mid,
        mentioned=kw.pop("mentioned", False),
        has_content=kw.pop("has_content", True),
        **kw,
    )


def test_window_unique_members_and_group_isolation():
    service = PassiveActivity("10001", enabled=True)
    with patch("time.time", return_value=NOW):
        observe(service)
        observe(service, mid="m2")
        observe(service, user="8002", group="7002", mid="m3")
    with patch("time.time", return_value=NOW + 600):
        batch = observe(service, user="8003", mid="trigger", mentioned=True)
        assert batch.members == (("8001", NOW),)
    with patch("time.time", return_value=NOW + 600.001):
        batch = observe(service, user="8004", mid="later", mentioned=True)
        assert batch.members == (("8003", NOW + 600),)


@pytest.mark.parametrize("enabled", [False, True])
def test_ignored_empty_invalid_and_missing_id(enabled):
    service = PassiveActivity("10001", enabled=enabled)
    with patch("time.time", return_value=NOW):
        observe(service, user="10001")
        observe(service, user="not-id")
        observe(service, has_content=False)
        observe(service, user="8002", ignored_ids={"8002"})
        assert not service._members
        observe(service, mid="")  # 缺消息编号可以记录发言。
        assert bool(service._members) is enabled
        assert observe(service, user="8003", mid="", mentioned=True) is None
        assert observe(service, user="8003", mid="0", mentioned=True) is None
        batch = observe(service, user="8003", mid="valid", mentioned=True)
        assert bool(batch) is enabled


def test_replayed_messages_do_not_refresh_activity_and_new_ignored_ids_filtered():
    service = PassiveActivity("10001", enabled=True)
    with patch("time.time", return_value=NOW):
        observe(service)
    with patch("time.time", return_value=NOW + 300):
        observe(service)
        assert service._members[("7001", "8001")] == NOW
        assert (
            observe(
                service,
                user="8002",
                mid="trigger",
                mentioned=True,
                ignored_ids={"8001"},
            )
            is None
        )


def test_cache_bound_and_clock_rollback():
    service = PassiveActivity("10001", enabled=True)
    service.MAX_MEMBERS, service.MAX_MESSAGES = 3, 4
    with patch("time.time", return_value=NOW):
        for i in range(10):
            observe(service, user=str(8000 + i), mid=str(i + 1))
        assert len(service._members) == 3 and len(service._messages) == 4
    with patch("time.time", return_value=NOW - 1):
        assert observe(service, user="8100", mid="rollback", mentioned=True) is None
        assert len(service._members) == 1


def test_deterministic_trigger_identity_is_bot_and_group_scoped():
    ids = []
    with patch("time.time", return_value=NOW):
        for bot, group in [("10001", "7001"), ("10002", "7001"), ("10001", "7002")]:
            service = PassiveActivity(bot, enabled=True)
            observe(service, group=group)
            batch = observe(
                service, user="8002", group=group, mid="trigger", mentioned=True
            )
            ids.append(batch.trigger_id)
    assert len(set(ids)) == 3


def test_batches_merge_new_members_and_continue_after_failure():
    async def run():
        service = PassiveActivity("10001", enabled=True)
        registry = TaskRegistry()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def apply(group, user, **kw):
            calls.append((group, user, kw))
            if user == "8001":
                entered.set()
                await release.wait()
                raise OSError("合成存储失败")

        favor = SimpleNamespace(apply_passive_familiarity=apply)
        service.schedule(
            ActivityBatch("7001", "a", (("8001", NOW),), NOW), favor, registry
        )
        await entered.wait()
        service.schedule(
            ActivityBatch("7001", "b", (("8002", NOW),), NOW), favor, registry
        )
        assert registry.size == 1
        release.set()
        while registry.size:
            await asyncio.gather(*list(registry._tasks))
            await asyncio.sleep(0)
        assert [x[1] for x in calls] == ["8001", "8002"]
        assert calls[1][2] == {
            "trigger_id": "b",
            "observed_at": NOW,
            "triggered_at": NOW,
        }
        assert not service._pending

    asyncio.run(run())


def test_cancellation_before_start_releases_queue():
    async def run():
        service = PassiveActivity("10001", enabled=True)
        registry = TaskRegistry()
        service.schedule(
            ActivityBatch("7001", "a", (("8001", NOW),), NOW), None, registry
        )
        await registry.cancel_and_wait_all()
        await asyncio.sleep(0)
        assert not service._pending

    asyncio.run(run())


def test_listener_freezes_before_passive_write_and_does_not_call_model(tmp_path):
    async def run(listener, components):
        router = BotScopeStorageRouter(tmp_path)
        deps = await make_deps(listener, router, tmp_path)
        deps.passive_activity = PassiveActivity("10001", enabled=True)
        calls = []
        trigger = None

        async def apply(group, user, **kw):
            assert trigger._bond_relation_binding[2].done()
            calls.append((group, user, kw))

        deps.favor.apply_passive_familiarity = apply
        ordinary = Event(
            dict(text="群聊正文", message_id="ordinary", user="8002"), components
        )
        await listener.on_group_message(deps, ordinary)
        await drain(deps)
        assert not calls
        # 引用 Bot 不触发，但属于正常发言。
        reply = Event(
            dict(
                text="引用",
                message_id="reply",
                user="8003",
                chain=[("Reply", {"sender_id": "10001", "id": "q"})],
            ),
            components,
        )
        await listener.on_group_message(deps, reply)
        await drain(deps)
        assert not calls
        trigger = Event(
            dict(text="你好", message_id="trigger", chain=[("At", {"qq": "10001"})]),
            components,
        )
        before = trigger.message_str
        await listener.on_group_message(deps, trigger)
        await drain(deps)
        assert [call[1] for call in calls] == ["8002", "8003"]
        assert trigger.message_str == before
        assert deps.assessment is None
        await listener.on_group_message(deps, trigger)
        await drain(deps)
        assert len(calls) == 2
        await router.close()

    with (
        offline_listener() as (listener, components, _),
        patch("time.time", return_value=NOW),
    ):
        asyncio.run(run(listener, components))


def test_main_route_excludes_private_whitelist_and_bots(tmp_path):
    async def run(listener, components, hook):
        router = BotScopeStorageRouter(tmp_path)
        deps = await make_deps(listener, router, tmp_path)
        deps.passive_activity = PassiveActivity("10001", enabled=True)
        config = {"group_whitelist": ["7001"], "ignored_bot_ids": ["8009"]}
        runtime = RuntimeService(router, config, lambda *_: deps)
        plugin = SimpleNamespace(
            _runtime=runtime, config=config, _text_wake_enabled=False
        )
        cases = [
            ("8001", ""),
            ("8002", "7002"),
            ("8009", "7001"),
            ("10001", "7001"),
            ("8003", "7001"),
        ]
        for i, (user, group) in enumerate(cases):
            event = Event(
                dict(text="合成消息", message_id=f"m{i}", user=user, group=group),
                components,
            )
            async for _ in hook(plugin, event):
                pytest.fail("观察不应发送消息")
        await drain(deps)
        assert list(deps.passive_activity._members) == [("7001", "8003")]
        await router.close()

    with (
        offline_listener() as (listener, components, hook),
        patch("time.time", return_value=NOW),
    ):
        asyncio.run(run(listener, components, hook))


def test_setting_default_and_validation():
    assert snapshot({})["values"]["passive_familiarity"] is True
    validate_patch({"passive_familiarity": True})
    with pytest.raises(ValueError):
        validate_patch({"passive_familiarity": "true"})


def test_large_batch_yields_to_other_tasks():
    async def run():
        service = PassiveActivity("10001", enabled=True)
        registry = TaskRegistry()
        calls, checkpoints = [], []

        async def apply(group, user, **kw):
            calls.append(user)

        async def sibling():
            checkpoints.append(len(calls))

        service.schedule(
            ActivityBatch(
                "7001", "trigger", tuple((str(8000 + i), NOW) for i in range(40)), NOW
            ),
            SimpleNamespace(apply_passive_familiarity=apply),
            registry,
        )
        registry.spawn(sibling())
        while registry.size:
            await asyncio.gather(*list(registry._tasks))
            await asyncio.sleep(0)
        assert 0 < checkpoints[0] < 40
        assert len(calls) == 40

    asyncio.run(run())


def test_eligibility_is_frozen_at_trigger_and_coalesces_newer_waiting_candidate():
    async def run():
        service = PassiveActivity("10001", enabled=True)
        registry = TaskRegistry()
        calls = []

        async def apply(group, user, **kw):
            calls.append((user, kw))

        favor = SimpleNamespace(apply_passive_familiarity=apply)
        with patch("time.time", return_value=NOW):
            observe(service)
        with patch("time.time", return_value=NOW + 599):
            first = observe(service, user="8002", mid="first", mentioned=True)
            service.schedule(first, favor, registry)
        with patch("time.time", return_value=NOW + 600):
            observe(service, user="8003", mid="after-trigger")
        with patch("time.time", return_value=NOW + 601):
            await drain(SimpleNamespace(registry=registry))
        assert calls == [
            (
                "8001",
                dict(
                    trigger_id=first.trigger_id, observed_at=NOW, triggered_at=NOW + 599
                ),
            )
        ]
        calls.clear()
        # 尚未处理的候选使用较新的有效触发，不能让过旧的首个请求挡住它。
        service.schedule(
            ActivityBatch("7001", "older", (("8001", NOW),), NOW), favor, registry
        )
        service.schedule(
            ActivityBatch("7001", "newer", (("8001", NOW + 10),), NOW + 10),
            favor,
            registry,
        )
        await drain(SimpleNamespace(registry=registry))
        assert calls[0][1] == dict(
            trigger_id="newer", observed_at=NOW + 10, triggered_at=NOW + 10
        )

    asyncio.run(run())


def test_listener_rejects_mismatched_bot_and_freezes_mutable_event(tmp_path):
    async def run(listener, components):
        router = BotScopeStorageRouter(tmp_path)
        deps = await make_deps(listener, router, tmp_path)
        deps.passive_activity = PassiveActivity("10001", enabled=True)
        wrong = Event(
            dict(text="错配消息", message_id="wrong", bot="10002", user="8002"),
            components,
        )
        await listener.on_group_message(deps, wrong)
        await drain(deps)
        assert not deps.passive_activity._members
        ordinary = Event(
            dict(text="普通消息", message_id="ordinary", user="8002"), components
        )
        await listener.on_group_message(deps, ordinary)
        await drain(deps)
        entered, release = asyncio.Event(), asyncio.Event()
        original = deps.favor.read_relation_snapshot
        calls = []

        async def delayed(*args, **kw):
            entered.set()
            await release.wait()
            return await original(*args, **kw)

        async def apply(group, user, **kw):
            calls.append((group, user, kw))

        deps.favor.read_relation_snapshot = delayed
        deps.favor.apply_passive_familiarity = apply
        event = Event(
            dict(text="触发", message_id="trigger", chain=[("At", {"qq": "10001"})]),
            components,
        )
        task = deps.registry.spawn(listener.on_group_message(deps, event))
        await entered.wait()
        event.group, event.user, event.bot = "7002", "8009", "10002"
        event.message_obj.message_id = "mutated"
        release.set()
        await task
        await drain(deps)
        assert [(group, user) for group, user, _ in calls] == [("7001", "8002")]
        await router.close()

    with (
        offline_listener() as (listener, components, _),
        patch("time.time", return_value=NOW),
    ):
        asyncio.run(run(listener, components))


@pytest.mark.parametrize("member_limit", [0, 3])
@pytest.mark.parametrize("minutes", [1, 20])
def test_configured_activity_window_preserves_boundary(minutes, member_limit):
    service = PassiveActivity(
        "10001",
        config=PassiveFamiliarityConfig(
            window_minutes=minutes, member_limit=member_limit
        ),
    )
    with patch("time.time", return_value=NOW):
        observe(service)
    with patch("time.time", return_value=NOW + minutes * 60):
        assert observe(
            service, user="8003", mid="boundary", mentioned=True
        ).members == (("8001", NOW),)
    with patch("time.time", return_value=NOW + minutes * 60 + 0.01):
        batch = observe(service, user="8004", mid="outside", mentioned=True)
        assert all(user != "8001" for user, _ in batch.members)


@pytest.mark.parametrize(
    "member_limit,expected",
    [
        (0, ["8001", "8003", "8004", "8005", "8002"]),
        (3, ["8004", "8005", "8002"]),
    ],
)
def test_member_limit_uses_last_distinct_speakers_excluding_trigger(
    member_limit, expected
):
    service = PassiveActivity(
        "10001", config=PassiveFamiliarityConfig(member_limit=member_limit)
    )
    with patch("time.time", return_value=NOW):
        for index, user in enumerate(
            ["8006", "8001", "8002", "8003", "8004", "8005", "8002"]
        ):
            observe(service, user=user, mid=f"m{index}")
        observe(service, user="8009", group="7002", mid="other-group")
        batch = observe(service, user="8006", mid="trigger", mentioned=True)
    assert [user for user, _ in batch.members] == expected


def test_member_limit_is_applied_before_ignored_member_eligibility():
    service = PassiveActivity("10001")
    with patch("time.time", return_value=NOW):
        for index in range(1, 6):
            observe(service, user=str(8000 + index), mid=f"m{index}")
        batch = observe(
            service,
            user="8006",
            mid="trigger",
            mentioned=True,
            ignored_ids={"8004"},
        )
    assert [user for user, _ in batch.members] == ["8003", "8005"]


def test_limited_batches_freeze_before_later_messages_and_merge_without_refill():
    async def run():
        service = PassiveActivity("10001")
        registry = TaskRegistry()
        calls = []

        async def apply(group, user, **kw):
            calls.append((user, kw))
            return user != "8004"

        favor = SimpleNamespace(apply_passive_familiarity=apply)
        with patch("time.time", return_value=NOW):
            for index in range(1, 6):
                observe(service, user=str(8000 + index), mid=f"m{index}")
            first = observe(service, user="8006", mid="first", mentioned=True)
        with patch("time.time", return_value=NOW + 1):
            observe(service, user="8007", mid="future")
            service.schedule(first, favor, registry)
            second = observe(service, user="8008", mid="second", mentioned=True)
            service.schedule(second, favor, registry)
        assert [user for user, _ in first.members] == ["8003", "8004", "8005"]
        assert [user for user, _ in second.members] == ["8005", "8006", "8007"]
        assert registry.size == 1
        await drain(SimpleNamespace(registry=registry))
        assert [user for user, _ in calls] == ["8003", "8004", "8005", "8006", "8007"]
        for user, metadata in calls:
            batch = first if user in ("8003", "8004") else second
            assert metadata == dict(
                trigger_id=batch.trigger_id,
                observed_at=dict(batch.members)[user],
                triggered_at=batch.triggered_at,
            )
        assert not service._pending

    asyncio.run(run())
