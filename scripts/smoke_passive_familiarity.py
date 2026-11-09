"""真实 AstrBot 的被动熟悉度钩子检查；合成事件、SQLite，禁止网络。"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import socket
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from smoke_astrbot import _context, check_framework_version

BOT, GROUP, ACTIVE, DIRECT = "10001", "7001", "8001", "8002"
NOW = 1_800_000_000.0


async def check(plugin_dir: Path, runtime_dir: Path):
    import astrbot
    from astrbot.api import AstrBotConfig
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.message_components import At, Plain
    from astrbot.api.provider import LLMResponse, ProviderRequest
    from astrbot.api.star import Context
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    from astrbot.core.provider.provider import Provider
    from astrbot.core.star.star_handler import (
        StarHandlerRegistry,
        star_handlers_registry,
    )

    from astrbot_plugin_bond.main import BondPlugin
    from astrbot_plugin_bond.services.assessment_service import AssessmentConfig

    check_framework_version()
    schema = json.loads((plugin_dir / "_conf_schema.json").read_text(encoding="utf-8"))
    config = AstrBotConfig(str(runtime_dir / "config.json"), schema=schema)
    assert config["passive_familiarity"] is True
    config["passive_familiarity_rules"].update(
        amount=0.6, ceiling=50, cooldown_minutes=3, window_minutes=20
    )
    config["inject"]["enabled"] = True
    config["relation_mode"] = "realistic"
    config["group_whitelist"] = [GROUP]
    config.save_config()
    config = AstrBotConfig(str(runtime_dir / "config.json"), schema=schema)
    context = _context(Context, config)
    calls = []

    class OfflineProvider(Provider):
        def __init__(self):
            super().__init__({"id": "synthetic-provider"}, {})
            self.set_model("synthetic")

        def get_current_key(self):
            return ""

        def set_key(self, key):
            raise AssertionError("离线检查不接受凭据")

        async def get_models(self):
            return ["synthetic"]

        async def text_chat(self, **kw):
            calls.append(copy.deepcopy(kw))
            return LLMResponse(
                role="assistant",
                completion_text=json.dumps(
                    dict(
                        event_type="normal_question",
                        confidence=0.95,
                        evidence="请介绍合成测试",
                        should_update=True,
                    ),
                    ensure_ascii=False,
                ),
            )

    context.provider_manager.inst_map = {"synthetic-provider": OfflineProvider()}
    plugin = BondPlugin(context, config)
    await plugin.initialize()

    def event(user, mid, *, directed=False):
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE
        message.self_id, message.group_id, message.message_id = BOT, GROUP, mid
        message.sender = MessageMember(user, "合成成员")
        message.message_str = "请介绍合成测试" if directed else "普通群聊合成内容"
        message.message = [Plain(text=message.message_str)]
        if directed:
            message.message.insert(0, At(qq=BOT))
        message.raw_message = {}
        return AstrMessageEvent(
            message.message_str,
            message,
            PlatformMetadata("synthetic", "离线平台", "synthetic"),
            GROUP,
        )

    async def dispatch(current):
        original = current.message_str
        async for _ in plugin._on_group_msg(current):
            raise AssertionError("被动观察不应发送消息")
        assert current.message_str == original
        while True:
            tasks = [
                task
                for task, name in plugin._registry._tasks.items()
                if name.startswith(("judge:", "passive-familiarity:"))
            ]
            if not tasks:
                break
            await asyncio.gather(*tasks)
            await asyncio.sleep(0)

    try:
        with patch("time.time", return_value=NOW):
            deps = await plugin._runtime.select(BOT)
            assert deps.passive_activity.enabled
            assert not deps.assessment.config.enabled
            await deps.favor.set_relation(
                GROUP,
                ACTIVE,
                familiarity=40,
                affinity=30,
                trust=20,
                event_id="synthetic-seed",
            )
            before = await deps.favor._storage.get(GROUP, ACTIVE)
            await dispatch(event(ACTIVE, "ordinary"))
            trigger = event(DIRECT, "trigger", directed=True)
            await dispatch(trigger)
            after = await deps.favor._storage.get(GROUP, ACTIVE)
            assert after.familiarity == 40.6
            for name in (
                "favor",
                "trust",
                "updated_at",
                "trust_updated_at",
                "affinity_maintained_at",
                "familiarity_maintained_at",
                "trust_maintained_at",
                "affinity_floor_reference",
                "half_life",
            ):
                assert getattr(before, name) == getattr(after, name), name
            invoker = await deps.favor.get_relation(GROUP, DIRECT)
            assert invoker.familiarity == 0
            assert not calls
            req = ProviderRequest(
                system_prompt="人格：安静的档案管理员。其他插件：保留原有策略。",
                prompt="其他插件保留的当前输入",
                contexts=[{"role": "user", "content": "其他插件提供的历史"}],
            )
            prompt, contexts = req.prompt, copy.deepcopy(req.contexts)
            await plugin._on_llm_req(trigger, req)
            assert req.system_prompt.startswith(
                "人格：安静的档案管理员。其他插件：保留原有策略。"
            )
            assert req.prompt == prompt and req.contexts == contexts
            assert '"user_id": "8002"' in req.system_prompt
            # 同一真实事件重试不产生第二次被动奖励。
            await dispatch(trigger)
            assert (await deps.favor._storage.get(GROUP, ACTIVE)).familiarity == 40.6

        # 已有直接评估链仍可计分与保温；仅 Provider 使用离线预置分类。
        with patch("time.time", return_value=NOW + 60):
            deps.assessment.config = AssessmentConfig(
                enabled=True,
                small_provider_id="synthetic-provider",
                assessment_mode="single",
            )
            await dispatch(event(ACTIVE, "direct-next", directed=True))
            direct = await deps.favor._storage.get(GROUP, ACTIVE)
            assert direct.familiarity > after.familiarity
            assert direct.familiarity_maintained_at == NOW + 60
            assert len(calls) == 1
            assert "请介绍合成测试" in calls[0]["prompt"]

        handler = next(
            item
            for item in star_handlers_registry
            if item.handler_module_path == "astrbot_plugin_bond.main"
            and item.handler_name == "_on_group_msg"
        )
        assert handler.extras_configs["priority"] == 1000
        registry = StarHandlerRegistry()
        registry.append(
            replace(
                handler,
                handler_full_name="synthetic.default",
                extras_configs={"priority": 0},
            )
        )
        registry.append(handler)
        registry.append(
            replace(
                handler,
                handler_full_name="synthetic.higher",
                extras_configs={"priority": 1500},
            )
        )
        assert [h.handler_full_name for h in registry] == [
            "synthetic.higher",
            handler.handler_full_name,
            "synthetic.default",
        ]
    finally:
        await plugin.terminate()
    # 宿主先补 schema 默认值，再由插件迁移旧对象，明确关闭不能变为开启。
    for enabled in (False, True):
        legacy_path = runtime_dir / f"legacy-{enabled}.json"
        legacy_path.write_text(
            json.dumps({"passive_familiarity": {"enabled": enabled}}), encoding="utf-8"
        )
        legacy_config = AstrBotConfig(str(legacy_path), schema=schema)
        legacy_plugin = BondPlugin(_context(Context, legacy_config), legacy_config)
        try:
            await legacy_plugin.initialize()
            assert legacy_config["passive_familiarity"] is enabled
            assert (
                await legacy_plugin._runtime.select(BOT)
            ).passive_activity.enabled is enabled
            assert (
                AstrBotConfig(str(legacy_path), schema=schema)["passive_familiarity"]
                is enabled
            )
        finally:
            await legacy_plugin.terminate()
    print(
        f"PASS AstrBot {astrbot.__version__}: schema重载、真实事件/主钩子、自定义被动+0.6、触发者排除、A/T与全部保温不变、请求人格/历史保留、重试、后续直接评估及实际优先级排序。"
    )
    print(
        "LIMIT: 钩子直接调用，Provider为离线预置分类；未连接QQ、真实模型或启动完整平台pipeline/其他插件。高于1000的接管监听器仍可能先执行。"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", required=True)
    args = parser.parse_args()
    runtime_dir = Path(args.runtime_dir).resolve()
    if runtime_dir.exists() and any(runtime_dir.iterdir()):
        parser.error("runtime-dir 必须为空")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ASTRBOT_ROOT"] = str(runtime_dir)
    plugin_dir = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(plugin_dir.parent))

    async def guarded():
        with (
            patch.object(
                socket.socket, "connect", side_effect=AssertionError("禁止网络连接")
            ),
            patch.object(
                socket, "create_connection", side_effect=AssertionError("禁止网络连接")
            ),
        ):
            await check(plugin_dir, runtime_dir)

    asyncio.run(guarded())


if __name__ == "__main__":
    main()
