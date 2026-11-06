"""真实 AstrBot 4.28 runner → 离线记录 Provider；全程合成数据，禁止网络。"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BOT, GROUP, USER = "10001", "7001", "8001"


async def check(runtime_dir: Path) -> None:
    import astrbot
    from mcp.types import CallToolResult, TextContent

    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.message_components import At, Plain
    from astrbot.api.provider import LLMResponse, ProviderRequest
    from astrbot.core.agent.hooks import BaseAgentRunHooks
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
    from astrbot.core.agent.tool import FunctionTool, ToolSet
    from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor
    from astrbot.core.exceptions import EmptyModelOutputError
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    from astrbot.core.provider.provider import Provider

    from astrbot_plugin_bond.api.listeners import Deps
    from astrbot_plugin_bond.core.levels import LevelTable
    from astrbot_plugin_bond.core.taskregistry import TaskRegistry
    from astrbot_plugin_bond.main import BondPlugin
    from astrbot_plugin_bond.services.favor_service import FavorService
    from astrbot_plugin_bond.services.inject_service import InjectService
    from astrbot_plugin_bond.services.prompt_service import PromptService
    from astrbot_plugin_bond.storage.router import BotScopeStorageRouter

    assert astrbot.__version__ == "4.28.0", astrbot.__version__
    router = BotScopeStorageRouter(runtime_dir / "synthetic")
    levels = LevelTable.from_config(None)
    deps = Deps(
        favor=FavorService(await router.get(BOT, "per_group"), levels),
        judge=None,
        inject=InjectService(levels, ""),
        registry=TaskRegistry(),
        prompts=PromptService(runtime_dir / "prompts.json"),
        bot_id=BOT,
    )

    async def for_event(event):
        return deps

    plugin = SimpleNamespace(
        config={},
        _text_wake_enabled=False,
        _runtime=SimpleNamespace(
            for_event=for_event, observe_bot=lambda _: None, known_bot_ids=lambda: [BOT]
        ),
    )

    def event(message_id):
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE
        message.self_id, message.group_id, message.message_id = BOT, GROUP, message_id
        message.sender = MessageMember(USER, "合成发言者")
        message.message_str = "请帮我查一下合成资料"
        message.message = [At(qq=BOT), Plain(text=message.message_str)]
        message.raw_message = {}
        return AstrMessageEvent(
            message.message_str,
            message,
            PlatformMetadata("synthetic", "离线", "synthetic"),
            GROUP,
        )

    async def seed(high, reason):
        await deps.favor.set_relation(
            GROUP,
            USER,
            familiarity=80 if high else 0,
            affinity=80 if high else 0,
            trust=80 if high else 0,
            event_id=reason,
            reason=reason,
        )
        await deps.favor._storage.set_impression(
            GROUP,
            USER,
            "后台新印象" if high else "消息入场前印象",
            [],
        )

    class RecordingProvider(Provider):
        def __init__(self, mode):
            super().__init__({"id": "offline-recording", "max_context_tokens": 0}, {})
            self.mode, self.requests = mode, []
            self.set_model("synthetic-no-network")

        def get_current_key(self):
            return ""

        def set_key(self, key):
            raise AssertionError("离线 Provider 不接受凭据")

        async def get_models(self):
            return [self.get_model()]

        async def text_chat(self, **kwargs):
            # runner 真正传给 Provider 的是序列化上下文；记录该边界，
            # 不用请求钩子中尚未经过 runner 的 system_prompt 代替验收。
            contexts = [
                (
                    row.model_dump(mode="json", exclude_none=True)
                    if hasattr(row, "model_dump")
                    else copy.deepcopy(row)
                )
                for row in kwargs["contexts"]
            ]
            self.requests.append(
                ProviderRequest(
                    contexts=contexts,
                    model=kwargs.get("model"),
                    session_id=kwargs.get("session_id"),
                )
            )
            if len(self.requests) == 1:
                await seed(True, self.mode + "-background-after-capture")
                if self.mode == "retry":
                    raise EmptyModelOutputError("合成空输出，触发真实 runner 重试")
                if self.mode == "tool":
                    return LLMResponse(
                        role="assistant",
                        tools_call_name=["synthetic_lookup"],
                        tools_call_args=[{}],
                        tools_call_ids=["synthetic-call"],
                    )
            return LLMResponse(role="assistant", completion_text="合成完成")

    class OfflineExecutor(BaseFunctionToolExecutor):
        @classmethod
        async def execute(cls, tool, run_context, **tool_args):
            assert tool.name == "synthetic_lookup" and not tool_args
            yield CallToolResult(
                content=[TextContent(type="text", text="合成工具结果")]
            )

    def text(row):
        value = row.get("content")
        if isinstance(value, str):
            return value
        return "".join(part.get("text", "") for part in (value or []))

    results = []
    try:
        for mode in ("initial", "tool", "retry"):
            await seed(False, mode + "-before-capture")
            current = event(mode)
            async for _ in BondPlugin._on_group_msg(plugin, current):
                pass
            while deps.registry.size:
                await asyncio.gather(*list(deps.registry._tasks))
            req = ProviderRequest(
                prompt=current.message_str,
                system_prompt="人格：克制、可靠。其他插件：保留原有策略。",
                contexts=[{"role": "user", "content": "合成既有上下文"}],
                model="synthetic-no-network",
            )
            if mode == "tool":
                req.func_tool = ToolSet(
                    [
                        FunctionTool(
                            name="synthetic_lookup",
                            description="离线合成查询",
                            parameters={"type": "object", "properties": {}},
                        )
                    ]
                )
            await BondPlugin._on_llm_req(plugin, current, req)
            frozen = req.system_prompt
            assert "消息入场前印象" in frozen
            assert '"bot_id": "10001"' in frozen
            provider = RecordingProvider(mode)
            runner = ToolLoopAgentRunner()
            await runner.reset(
                provider=provider,
                request=req,
                run_context=ContextWrapper(context=SimpleNamespace(event=current)),
                tool_executor=OfflineExecutor(),
                agent_hooks=BaseAgentRunHooks(),
            )
            async for _ in runner.step_until_done(max_step=3):
                pass
            assert runner.done()
            assert runner.get_final_llm_resp().completion_text == "合成完成"
            assert len(provider.requests) == (1 if mode == "initial" else 2)
            for delivered in provider.requests:
                systems = [
                    text(row) for row in delivered.contexts if row["role"] == "system"
                ]
                assert systems == [frozen], (mode, systems)
                serialized = json.dumps(delivered.contexts, ensure_ascii=False)
                assert mode + "-background-after-capture" not in serialized
                assert "后台新印象" not in serialized
                assert "合成既有上下文" in serialized
                assert current.message_str in serialized
            if mode == "tool":
                assert any(
                    row["role"] == "tool" for row in provider.requests[1].contexts
                )
                assert "合成工具结果" in json.dumps(
                    provider.requests[1].contexts, ensure_ascii=False
                )
            if mode == "retry":
                assert provider.requests[0].contexts == provider.requests[1].contexts
            # 后台已写入，但同事件的新 ProviderRequest 仍使用冻结资料。
            retry = ProviderRequest(
                prompt=current.message_str,
                system_prompt="人格：克制、可靠。其他插件：保留原有策略。",
            )
            await BondPlugin._on_llm_req(plugin, current, retry)
            assert retry.system_prompt == frozen
            next_req = ProviderRequest(prompt="独立下一轮")
            await BondPlugin._on_llm_req(plugin, event(mode + "-next"), next_req)
            assert "后台新印象" in next_req.system_prompt
            results.append(dict(mode=mode, provider_calls=len(provider.requests)))
    finally:
        await deps.registry.cancel_and_wait_all()
        await router.close()
    print("PASS AstrBot 4.28.0 actual ToolLoopAgentRunner → offline Provider:", results)
    print(
        "LIMIT: 实际主消息/请求钩子直接调用；未启动完整平台 pipeline、真实模型或网络。"
    )


def main() -> None:
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

    async def guarded_check():
        # Windows 事件循环先建立自身 socketpair，再禁止 smoke 内的网络连接。
        with (
            patch.object(
                socket.socket, "connect", side_effect=AssertionError("禁止网络连接")
            ),
            patch.object(
                socket, "create_connection", side_effect=AssertionError("禁止网络连接")
            ),
        ):
            await check(runtime_dir)

    asyncio.run(guarded_check())


if __name__ == "__main__":
    main()
