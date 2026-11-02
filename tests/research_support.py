"""合成关系回放；运行实际入口/服务/SQLite，Provider 输出与金标准独立。"""

from __future__ import annotations

import ast
import asyncio
import datetime as dt
import hashlib
import json
import logging
import sys
import time
import types
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_bond.core.assessment import Interaction, SYSTEM_PROMPT
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_policy import RelationPolicy, DEFAULT_RULES
from astrbot_plugin_bond.core.taskregistry import TaskRegistry
from astrbot_plugin_bond.services.assessment_service import (
    AssessmentCapacity,
    AssessmentConfig,
    AssessmentService,
)
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.services.inject_service import InjectService
from astrbot_plugin_bond.services.runtime_service import RuntimeService
from astrbot_plugin_bond.storage.router import BotScopeStorageRouter

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "research" / "scenarios.json"
DIMENSIONS = ("familiarity", "affinity", "trust")
ZERO = dict.fromkeys(DIMENSIONS, 0.0)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def load_fixture():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    ids = [case["id"] for case in data["scenarios"]]
    assert len(ids) == len(set(ids))
    for case in data["scenarios"]:
        for step in case["steps"]:
            if step.get("action", "message") == "message":
                assert set(step["gold"]["direction"]) == set(DIMENSIONS)
                assert step["gold"]["trusted_sender"] == step.get("user", "8001")
    return data


class Config(dict):
    def save_config(self):
        self.saved = deepcopy(self)


class Event:
    def __init__(self, step, components):
        self.bot = step.get("bot", "10001")
        self.group = step.get("group", "7001")
        self.user = step.get("user", "8001")
        self.message_str = step["text"]
        self.name = step.get("nickname", "合成同名用户")
        self.unified_msg_origin = "synthetic:group:" + self.group
        self.message_obj = SimpleNamespace(
            message_id=step["message_id"],
            message=[
                components[kind](**values) for kind, values in step.get("chain", [])
            ],
            raw_message={"sender": {"nickname": self.name, "card": ""}},
        )

    def get_self_id(self):
        return self.bot

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.user

    def get_sender_name(self):
        return self.name

    def get_platform_name(self):
        return "synthetic"


@contextmanager
def offline_listener():
    """框架类型仅替身；执行仓库真实监听器和主消息钩子的方法体。"""
    modules = {}
    for name in (
        "astrbot",
        "astrbot.api",
        "astrbot.api.event",
        "astrbot.api.provider",
        "astrbot.api.star",
        "astrbot.api.message_components",
    ):
        modules[name] = types.ModuleType(name)
    modules["astrbot.api"].logger = logging.getLogger("bond-replay")
    modules["astrbot.api.event"].AstrMessageEvent = Event
    modules["astrbot.api.provider"].ProviderRequest = SimpleNamespace
    modules["astrbot.api.star"].Context = SimpleNamespace
    components = {}
    for name in ("At", "Reply"):
        components[name] = type(name, (SimpleNamespace,), {})
        setattr(modules["astrbot.api.message_components"], name, components[name])
    with patch.dict(sys.modules, modules):
        # 强制本次监听器使用本次替身，不受其他测试先前导入的类型影响。
        sys.modules.pop("astrbot_plugin_bond.api.listeners", None)
        import importlib

        listeners = importlib.import_module("astrbot_plugin_bond.api.listeners")
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        cls = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "BondPlugin"
        )
        hook = deepcopy(
            next(
                n
                for n in cls.body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "_on_group_msg"
            )
        )
        hook.decorator_list = []
        module = ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__", names=[ast.alias(name="annotations")], level=0
                ),
                hook,
            ],
            type_ignores=[],
        )
        namespace = dict(
            logger=logging.getLogger("bond-replay"),
            on_group_message=listeners.on_group_message,
        )
        exec(
            compile(
                ast.fix_missing_locations(module), "<Bond production hook>", "exec"
            ),
            namespace,
        )
        yield listeners, components, namespace["_on_group_msg"]


class Clock:
    def __init__(self):
        self.value = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()

    @contextmanager
    def frozen(self):
        original = dt.datetime
        clock = self

        class FrozenDatetime(original):
            @classmethod
            def now(cls, tz=None):
                return original.fromtimestamp(clock.value, tz)

        # wall time 和 UTC 预算换日共同推进；不修改 asyncio 的 monotonic。
        with patch("time.time", lambda: self.value), patch(
            "datetime.datetime", FrozenDatetime
        ):
            yield


class World:
    def __init__(self, path, case, rules, listener, components, hook):
        self.path, self.case, self.rules = path, case, rules
        self.listener, self.components, self.hook = listener, components, hook
        self.clock = Clock()
        self.config = Config(
            default_scope_mode=case.get("scope", "per_group"),
            bot_scope_modes="{}",
            group_whitelist=[],
        )
        self.plans, self.calls, self.rows, self.timings = {}, [], [], []
        self.seeded = set()
        self.registry = TaskRegistry()
        self.capacity = AssessmentCapacity(2)
        self.router = BotScopeStorageRouter(path)
        self.runtime = RuntimeService(self.router, self.config, self.factory)
        self.plugin = SimpleNamespace(
            _runtime=self.runtime, config=self.config, _text_wake_enabled=False
        )
        for bot in ("10001", "10002"):
            self.runtime.observe_bot(SimpleNamespace(get_self_id=lambda b=bot: b))
        self.blocks = []

    def factory(self, store, mode):
        favor = FavorService(
            store,
            LevelTable.from_config(None),
            scope_mode=mode,
            relation_policy=RelationPolicy(rules=self.rules),
            decay_enabled=self.case.get("decay", False),
        )
        cfg = AssessmentConfig(
            enabled=True,
            small_provider_id="synthetic-small",
            large_provider_id=self.case.get("large_provider", "synthetic-large"),
            cooldown_sec=self.case.get("cooldown", 120),
            small_timeout_sec=self.case.get("timeout", 2.0),
            large_timeout_sec=self.case.get("timeout", 2.0),
            per_user_daily_large_cap=self.case.get("user_large_cap", 2),
            per_bot_daily_large_cap=self.case.get("bot_large_cap", 20),
        )

        self.config_snapshot = asdict(cfg)

        async def budgets():
            return [
                await self.router.get(store.bot_id, m) for m in ("per_group", "global")
            ]

        def provider(provider_id):
            tier = "large" if provider_id.endswith("large") else "small"
            return SimpleNamespace(
                text_chat=lambda prompt: self.call(store.bot_id, tier, prompt)
            )

        assessment = AssessmentService(
            store,
            favor,
            SimpleNamespace(get_provider_by_id=provider),
            cfg,
            self.capacity,
            budgets,
        )
        return self.listener.Deps(
            favor=favor,
            judge=None,
            inject=InjectService(LevelTable.from_config(None), ""),
            registry=self.registry,
            assessment=assessment,
            bot_id=store.bot_id,
            prompts=SimpleNamespace(
                resolve=lambda *args: "{relationship}\n{impression}\n{events}"
            ),
        )

    async def call(self, bot, tier, prompt):
        payload = json.loads(prompt.rsplit("\n输入：", 1)[1])
        plan = self.plans[(bot, payload["sender"], payload["text"])]
        response = plan.get(tier, plan.get("response", {}))
        self.calls.append(
            dict(
                bot=bot,
                tier=tier,
                sender=payload["sender"],
                text=payload["text"],
                tokens=response.get("tokens"),
            )
        )
        entered = plan.get("_entered")
        if entered is not None:
            entered.set()
            await plan["_release"].wait()
        outcome = response.get("outcome", "ok")
        if outcome == "failed":
            raise RuntimeError("synthetic provider failure")
        if outcome == "timeout":
            await asyncio.Event().wait()
        raw = response.get("raw")
        if raw is None:
            raw = json.dumps(
                dict(
                    event_type=response.get("event_type", "uncertain"),
                    confidence=response.get("confidence", 0.95),
                    evidence=response.get("evidence", payload["text"]),
                ),
                ensure_ascii=False,
            )
        return SimpleNamespace(
            completion_text=raw, usage=SimpleNamespace(total=response.get("tokens"))
        )

    async def drain(self):
        while self.registry.size:
            tasks = list(self.registry._tasks)
            await asyncio.gather(*tasks)
            await asyncio.sleep(0)

    async def prepare(self, step):
        event = Event(step, self.components)
        deps = await self.runtime.for_event(event)
        key = (
            event.bot,
            deps.favor.scope_mode,
            deps.favor.group_key(event.group),
            event.user,
        )
        if key not in self.seeded:
            self.seeded.add(key)
            initial = self.case.get("initial", ZERO)
            if any(initial.values()):
                await deps.favor.set_relation(
                    event.group, event.user, **initial, event_id="seed-" + digest(key)
                )
        return event, deps

    async def message(self, step):
        event, deps = await self.prepare(step)
        before = await deps.favor.formal_relation(event.group, event.user)
        title_before = await deps.favor.relation_title(event.group, event.user)
        self.plans[(event.bot, event.user, event.message_str)] = step
        interaction = Interaction(
            event.bot,
            event.group,
            event.user,
            "synthetic",
            step["message_id"],
            event.message_str,
        )
        before_calls = sum(
            c["bot"] == event.bot
            and c["sender"] == event.user
            and c["text"] == event.message_str
            for c in self.calls
        )
        last_log_id = (
            deps.favor._storage._c()
            .execute("SELECT COALESCE(MAX(id),0) FROM favor_log")
            .fetchone()[0]
        )
        started = time.perf_counter()
        async for _ in self.hook(self.plugin, event):
            pass
        self.timings.append((time.perf_counter() - started) * 1000)
        if step.get("inflight"):
            await asyncio.wait_for(step["_entered"].wait(), 1)
            await deps.favor.set_temporary(
                event.group,
                event.user,
                dict(familiarity=80, affinity=80, trust=80),
                60,
                "synthetic-admin",
            )
            await deps.favor.set_temporary(
                event.group, event.user, None, None, "synthetic-admin"
            )
            step["_release"].set()
        await self.drain()
        after = await deps.favor.formal_relation(event.group, event.user)
        title_after = await deps.favor.relation_title(event.group, event.user)
        logs = await deps.favor.query_logs(event.group, event.user, limit=10000)
        matching = [
            r
            for r in logs
            if r["event_id"] == interaction.event_id and r["id"] > last_log_id
        ]
        delta = dict(ZERO)
        for row in matching:
            for dimension in DIMENSIONS:
                delta[dimension] += row["relation_delta"].get(dimension, 0)
        status_row = (
            deps.favor._storage._c()
            .execute(
                "SELECT status FROM assessment_attempt WHERE event_id=?",
                (interaction.event_id,),
            )
            .fetchone()
        )
        changed = any(delta.values())
        gold = step["gold"]
        self.rows.append(
            dict(
                message_id=step["message_id"],
                bot=event.bot,
                group=event.group,
                user=event.user,
                scope=deps.favor.scope_mode,
                time=self.clock.value,
                text=event.message_str,
                chain=step.get("chain", []),
                gold=gold,
                before=before,
                after=after,
                delta=delta,
                changed=changed,
                title_before=title_before,
                title_after=title_after,
                status=status_row[0] if status_row else "entry_skipped",
                provider_called=sum(
                    c["bot"] == event.bot
                    and c["sender"] == event.user
                    and c["text"] == event.message_str
                    for c in self.calls
                )
                > before_calls,
                event_id=interaction.event_id,
                known_limit=step.get("known_limit"),
            )
        )

    async def run(self):
        steps = self.case["steps"]
        if "turns" in self.case:
            steps = [
                dict(
                    deepcopy(steps[i % len(steps)]),
                    message_id=f"{self.case['id']}-{i:04d}",
                )
                for i in range(self.case["turns"])
            ]
        for index, raw in enumerate(steps):
            step = deepcopy(raw)
            self.clock.value += step.get("advance_seconds", 86400 if index else 0)
            action = step.get("action", "message")
            if action == "burst":
                messages = deepcopy(step["messages"])
                for message in messages:
                    message["_entered"], message["_release"] = (
                        asyncio.Event(),
                        asyncio.Event(),
                    )
                tasks = [asyncio.create_task(self.message(m)) for m in messages]
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*(m["_entered"].wait() for m in messages[:2])), 2
                    )
                finally:
                    for message in messages:
                        message["_release"].set()
                    await asyncio.gather(*tasks)
                continue
            if action == "wait":
                continue
            if action == "switch":
                await self.runtime.switch_mode(step.get("bot", "10001"), step["scope"])
                continue
            if action == "restart":
                await self.drain()
                await self.router.close()
                self.router = BotScopeStorageRouter(self.path)
                self.runtime = RuntimeService(self.router, self.config, self.factory)
                self.plugin._runtime = self.runtime
                for bot in ("10001", "10002"):
                    self.runtime.observe_bot(
                        SimpleNamespace(get_self_id=lambda b=bot: b)
                    )
                continue
            if action == "profile":
                deps = await self.runtime.select(step.get("bot", "10001"))
                await deps.favor.set_profile(
                    step.get("group", "7001"),
                    "8001",
                    impression=step["impression"],
                    tags=[],
                    actor="synthetic-admin",
                )
                continue
            if action == "inject":
                deps = await self.runtime.select(step.get("bot", "10001"))
                event = Event(
                    dict(
                        text="合成问题",
                        message_id="injection",
                        **{k: step[k] for k in ("bot", "group", "user") if k in step},
                    ),
                    self.components,
                )
                request = SimpleNamespace(
                    system_prompt=step["persona"],
                    prompt="Angel Heart 合成改写",
                    contexts=[{"role": "user", "content": "保留历史"}],
                )
                await self.listener.on_llm_request(deps, event, request)
                assert request.system_prompt.startswith(step["persona"])
                assert request.prompt == "Angel Heart 合成改写"
                assert request.contexts == [{"role": "user", "content": "保留历史"}]
                assert (
                    "私密合成印象" not in request.system_prompt
                    if step.get("private")
                    else True
                )
                self.blocks.append(
                    dict(persona=step["persona"], block=request.system_prompt)
                )
                continue
            if action == "temporary":
                deps = await self.runtime.select(step.get("bot", "10001"))
                await deps.favor.set_temporary(
                    "7001",
                    "8001",
                    dict(familiarity=90, affinity=90, trust=90),
                    60,
                    "synthetic-admin",
                )
                continue
            if step.get("inflight"):
                step["_entered"], step["_release"] = asyncio.Event(), asyncio.Event()
            await self.message(step)
        # 数据库重开后的正式值须与最后一次真实读取一致。
        final = {}
        for bot, mode, group, user in sorted(self.seeded):
            deps = await self.runtime.select(bot, mode)
            formal = await deps.favor.formal_relation(group, user)
            store = deps.favor._storage
            await store.close()
            await store.init()
            assert await deps.favor.formal_relation(group, user) == formal
            final["/".join((bot, mode, group, user))] = formal
        # 穷尽当前场景打开的分库，防止只查预期用户而漏报错写主体。
        expected = {
            r["event_id"]: (
                r["bot"],
                r["scope"],
                "__global__" if r["scope"] == "global" else r["group"],
                r["user"],
            )
            for r in self.rows
        }
        for (bot, mode), store in self.router._backends.items():
            for row in store._c().execute(
                "SELECT event_id,group_id,user_id FROM favor_log WHERE source='judge'"
            ):
                actual = (bot, mode, row[1], row[2])
                assert expected.get(row[0]) == actual, (
                    "发现未跟踪或错主体持久化",
                    row[0],
                    actual,
                )
        stats = []
        for store in self.router._backends.values():
            stats.append(await store.assessment_stats())
        return dict(
            id=self.case["id"],
            tags=self.case["tags"],
            rows=sorted(self.rows, key=lambda r: (r["time"], r["message_id"])),
            final=final,
            blocks=self.blocks,
            stats=stats,
            config=self.config_snapshot,
        )


def ratio(numerator, denominator):
    return dict(
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator if denominator else None,
    )


def summarize(results):
    rows = [r for case in results for r in case["rows"]]
    forbidden = [r for r in rows if not r["gold"]["should_update"]]
    wrong_target = [r for r in rows if r["gold"]["target"] != r["bot"]]
    no_trust = [r for r in rows if r["gold"]["direction"]["trust"] == "zero"]
    certain_wrong_target = [
        r for r in wrong_target if not r["gold"]["target_uncertain"]
    ]
    uncertain_target = [r for r in rows if r["gold"]["target_uncertain"]]
    wrong_quote = [
        r
        for r in rows
        if r["gold"]["quote_source"] not in (None, r["user"])
        and not r["gold"]["should_update"]
    ]
    admitted = [r for r in rows if r["provider_called"]]
    abstain = [r for r in rows if r["gold"]["abstain"]]
    calls = [
        c for result in results for stats in result["stats"] for c in stats["calls"]
    ]
    tokens_known = all(c["tokens"] is not None for c in calls)
    statuses = {}
    for row in rows:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
    neutral = [r for r in rows if r["gold"]["social_act"] in ("neutral", "criticism")]
    return dict(
        events=len(rows),
        persistent_false_update_rate=ratio(
            sum(r["changed"] for r in forbidden), len(forbidden)
        ),
        target_false_update_rate=ratio(
            sum(r["changed"] for r in wrong_target), len(wrong_target)
        ),
        certain_target_false_update_rate=ratio(
            sum(r["changed"] for r in certain_wrong_target), len(certain_wrong_target)
        ),
        uncertain_target_false_update_rate=ratio(
            sum(r["changed"] for r in uncertain_target), len(uncertain_target)
        ),
        quote_false_update_rate=ratio(
            sum(r["changed"] for r in wrong_quote), len(wrong_quote)
        ),
        trust_false_update_rate=ratio(
            sum(r["delta"]["trust"] != 0 for r in no_trust), len(no_trust)
        ),
        target_precision=ratio(
            sum(r["gold"]["target"] == r["bot"] for r in admitted), len(admitted)
        ),
        abstention_coverage=ratio(sum(not r["changed"] for r in abstain), len(abstain)),
        abstention_status_coverage=ratio(
            sum(r["status"] == "abstained" for r in abstain), len(abstain)
        ),
        abstention_outcomes={
            status: sum(r["status"] == status for r in abstain)
            for status in sorted({r["status"] for r in abstain})
        },
        unscored_trust_events=sum(
            r["gold"]["direction"]["trust"] == "any" for r in rows
        ),
        neutral_drift={
            d: round(sum(r["delta"][d] for r in neutral), 4) for d in DIMENSIONS
        },
        neutral_absolute_drift={
            d: round(sum(abs(r["delta"][d]) for r in neutral), 4) for d in DIMENSIONS
        },
        title_flips_per_100_turns=(
            100 * sum(r["title_before"] != r["title_after"] for r in rows) / len(rows)
            if rows
            else None
        ),
        provider_calls=sum(c["count"] for c in calls),
        simulated_tokens=sum(c["tokens"] for c in calls) if tokens_known else None,
        statuses=statuses,
        failures=sum(c["count"] for c in calls if c["status"] == "failed"),
        timeouts=sum(c["count"] for c in calls if c["status"] == "timeout"),
        busy=statuses.get("busy", 0),
        real_provider_tokens=None,
        main_reply_p50_ms=None,
        main_reply_p95_ms=None,
        semantic_accuracy=None,
    )


async def replay(path, arms=("baseline", "model-a"), selected=None):
    data = load_fixture()
    output, timing = {}, {}
    cases = [c for c in data["scenarios"] if selected is None or c["id"] in selected]
    with offline_listener() as (listener, components, hook):
        for arm in arms:
            rules = deepcopy(
                data["baseline_rules"] if arm != "current" else DEFAULT_RULES
            )
            if arm == "model-a":
                for kind in ("thanks", "friendly", "apology"):
                    rules[kind]["trust"] = 0
            results, durations = [], []
            for case in cases:
                world = World(
                    Path(path) / arm / case["id"],
                    case,
                    rules,
                    listener,
                    components,
                    hook,
                )
                try:
                    with world.clock.frozen():
                        results.append(await world.run())
                    durations.extend(world.timings)
                finally:
                    await world.registry.cancel_and_wait_all()
                    await world.router.close()
            output[arm] = dict(
                rules=rules,
                rules_sha256=digest(rules),
                scenarios=results,
                metrics=summarize(results),
            )
            timing[arm] = dict(offline_hook_ms=durations, real_main_reply_ms=None)
    return (
        dict(
            schema_version=1,
            fixture_sha256=digest(data),
            baseline_commit=data["baseline_commit"],
            protocol_sha256=digest(SYSTEM_PROMPT),
            evaluation="synthetic_scripted_provider_not_semantic_accuracy",
            routing="current_checkout_production_hook_and_listener",
            arms=output,
        ),
        timing,
    )
