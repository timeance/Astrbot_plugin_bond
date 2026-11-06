"""高级诊断观察点不改变业务内容，并绑定消息和清除代次。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from astrbot_plugin_bond.services.diagnostic_capture import (
    CURRENT,
    create_capture,
    event_capture,
    interaction_capture,
    request_content,
)
from astrbot_plugin_bond.core.assessment import Interaction


class Sink:
    enabled = True

    def __init__(self):
        self.generation = 0
        self.rows = []

    def capture_token(self, bot, mode):
        return self.generation

    def emit_content(self, *args, **kwargs):
        if kwargs["generation"] == self.generation:
            self.rows.append(kwargs)


def test_identity_trace_and_clear_boundary():
    sink = Sink()
    favor = SimpleNamespace(diagnostics=sink, scope_mode="per_group")
    interaction = Interaction("10001", "20001", "30001", "test", "m1", "hello")
    capture = interaction_capture(favor, interaction)
    assert capture.trace_id == interaction.event_id
    token = CURRENT.set(capture)
    try:
        assert interaction_capture(favor, interaction) is capture
        capture.content("chat", {"text": "hello"})
        sink.generation += 1
        capture.content("response", {"text": "old reply"})
        assert len(sink.rows) == 1
    finally:
        CURRENT.reset(token)


def test_event_reuse_identity_and_frozen_generation():
    sink = Sink()
    deps = SimpleNamespace(
        favor=SimpleNamespace(diagnostics=sink, scope_mode="per_group")
    )
    event = SimpleNamespace(
        get_self_id=lambda: "10001",
        get_group_id=lambda: "20001",
        get_sender_id=lambda: "30001",
        get_platform_name=lambda: "test",
        message_obj=SimpleNamespace(message_id="m1"),
    )
    first = event_capture(deps, event)
    sink.generation += 1
    assert event_capture(deps, event) is first
    event.message_obj.message_id = "m2"
    assert event_capture(deps, event).generation == 1
    event.get_sender_id = lambda: "30002"
    assert event_capture(deps, event).user == "30002"


def test_no_object_dump_and_fail_silent():
    req = SimpleNamespace(
        system_prompt="persona",
        prompt="hello",
        contexts=[],
        api_key="secret",
        image_urls=["private"],
        provider=object(),
    )
    assert request_content(req) == dict(
        system_prompt="persona", prompt="hello", contexts=[], images_count=1
    )
    assert create_capture(SimpleNamespace(), "1", "2", "3", "test", "m") is None


def test_real_listener_flow_uses_one_trace_and_freezes_request(tmp_path):
    from astrbot_plugin_bond.tests.research_support import offline_listener
    from astrbot_plugin_bond.tests.test_relation_snapshot import (
        make_deps,
        message,
        request,
        drain,
    )
    from astrbot_plugin_bond.storage.router import BotScopeStorageRouter
    from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder
    from astrbot_plugin_bond.core.taskregistry import TaskRegistry
    from astrbot_plugin_bond.services.assessment_service import (
        AssessmentService,
        AssessmentConfig,
        AssessmentCapacity,
    )
    import json

    async def run(listener, components):
        router = BotScopeStorageRouter(tmp_path)
        deps = await make_deps(listener, router, tmp_path)
        recorder = DiagnosticRecorder(
            tmp_path / "diagnostics", TaskRegistry(), True, detail_level="full"
        )
        deps.favor.diagnostics = recorder

        async def chat(prompt):
            return SimpleNamespace(
                completion_text=json.dumps(
                    dict(event_type="thanks", confidence=0.95, evidence="谢谢")
                )
            )

        async def stores():
            return [deps.favor._storage]

        deps.assessment = AssessmentService(
            deps.favor._storage,
            deps.favor,
            SimpleNamespace(
                get_provider_by_id=lambda _: SimpleNamespace(text_chat=chat)
            ),
            AssessmentConfig(enabled=True, small_provider_id="small"),
            AssessmentCapacity(),
            stores,
        )
        event = message(components)
        await listener.on_group_message(deps, event)
        req = request()
        await listener.on_llm_request(deps, event, req)
        expected_system = req.system_prompt
        req.system_prompt = "later changes must not rewrite snapshot"
        await listener.on_llm_response(
            deps, event, SimpleNamespace(completion_text="你好，欢迎回来")
        )
        await drain(deps)
        rows = (await recorder.export("10001", "per_group"))["rows"]
        assert {r["fields"]["trace_id"] for r in rows}.__len__() == 1
        content = [r["content"] for r in rows if r["kind"] == "content"]
        assert {c["stage"] for c in content} == {
            "chat",
            "evaluation",
            "request",
            "response",
        }
        assert (
            next(c["data"]["system_prompt"] for c in content if c["stage"] == "request")
            == expected_system
        )
        await recorder.clear("10001", "per_group")
        await listener.on_llm_response(
            deps, event, SimpleNamespace(completion_text="old late response")
        )
        await listener.on_llm_request(deps, event, request())
        assert (await recorder.export("10001", "per_group"))["rows"] == []
        await recorder.close()
        await router.close()

    with offline_listener() as (listener, components, _):
        asyncio.run(run(listener, components))


def test_response_without_assessment_uses_only_existing_identity():
    from astrbot_plugin_bond.services.diagnostic_capture import record_bound_response

    sink = Sink()
    deps = SimpleNamespace(
        favor=SimpleNamespace(diagnostics=sink, scope_mode="per_group")
    )
    event = SimpleNamespace(
        get_self_id=lambda: "10001",
        get_group_id=lambda: "20001",
        get_sender_id=lambda: "30001",
        get_platform_name=lambda: "test",
        message_obj=SimpleNamespace(message_id="one"),
    )
    record_bound_response(event, SimpleNamespace(completion_text="before"))
    assert not sink.rows
    event_capture(deps, event)
    record_bound_response(event, SimpleNamespace(completion_text="reply"))
    assert len(sink.rows) == 1
    event.message_obj.message_id = "two"
    record_bound_response(event, SimpleNamespace(completion_text="unbound"))
    assert len(sink.rows) == 1


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "apiKey",
        "API-KEY",
        "password",
        "access_token",
        "refreshToken",
        "authorization",
        "cookie",
        "secret",
        "credential",
    ],
)
def test_json_tool_credentials_redacted_before_disk_and_export(tmp_path, key):
    from astrbot_plugin_bond.core.taskregistry import TaskRegistry
    from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder

    async def run():
        recorder = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="full"
        )
        favor = SimpleNamespace(diagnostics=recorder, scope_mode="per_group")
        capture = create_capture(
            favor, "10001", "20001", "30001", "synthetic", "json-tool"
        )
        marker = 'synthetic-sensitive-quote"-line\n-end'
        request = SimpleNamespace(
            tool_calls_result=[
                {
                    "role": "tool",
                    "content": json.dumps(
                        {key: marker, "text": "ordinary tool result"}
                    ),
                }
            ]
        )
        capture.content("request", request_content(request))
        await recorder.flush()
        disk_rows = [
            json.loads(line)
            for path in recorder.root.iterdir()
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        assert len(disk_rows) == 1
        assert "synthetic-sensitive" not in json.dumps(disk_rows)
        data = disk_rows[0]["content"]["data"]
        tool_result = json.loads(data["tool_results"][0]["content"])
        assert tool_result == {key: "[redacted]", "text": "ordinary tool result"}
        assert "ordinary tool result" in json.dumps(data)
        assert recorder._content_snapshot(data)["data"] == data
        exported = await recorder.export("10001", "per_group")
        assert exported["rows"] == disk_rows
        assert exported["missing"]["corrupt_lines"] == 0
        assert "synthetic-sensitive" not in json.dumps(exported)
        await recorder.close()

    asyncio.run(run())


def test_export_rejects_persisted_unsanitized_json_tool_content(tmp_path):
    from astrbot_plugin_bond.core.taskregistry import TaskRegistry
    from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder

    async def run():
        recorder = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="full"
        )
        capture = create_capture(
            SimpleNamespace(diagnostics=recorder, scope_mode="per_group"),
            "10001",
            "20001",
            "30001",
            "synthetic",
            "persisted-json",
        )
        capture.content(
            "request",
            request_content(
                SimpleNamespace(
                    tool_calls_result=[{"role": "tool", "content": "ordinary"}]
                )
            ),
        )
        await recorder.flush()
        path = next(recorder.root.iterdir())
        row = json.loads(path.read_text(encoding="utf-8"))
        row["content"]["data"]["tool_results"][0]["content"] = json.dumps(
            {"api_key": "synthetic-sensitive-persisted"}
        )
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        exported = await recorder.export("10001", "per_group")
        assert exported["rows"] == []
        assert exported["missing"]["corrupt_lines"] == 1
        assert "synthetic-sensitive" not in json.dumps(exported)
        await recorder.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "sensitive",
    [123456, {"value": "synthetic-private"}, ["synthetic-private"], True, None],
)
def test_json_tool_sensitive_values_are_redacted_for_every_type(tmp_path, sensitive):
    from astrbot_plugin_bond.core.taskregistry import TaskRegistry
    from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder

    async def run():
        recorder = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="full"
        )
        capture = create_capture(
            SimpleNamespace(diagnostics=recorder, scope_mode="per_group"),
            "10001",
            "20001",
            "30001",
            "synthetic",
            "json-types",
        )
        original = {
            "password": sensitive,
            "secret": sensitive,
            "text": "ordinary tool result",
        }
        serialized = json.dumps(original).replace('"password"', '"pass\\u0077ord"')
        capture.content(
            "request",
            request_content(
                SimpleNamespace(
                    tool_calls_result=[{"role": "tool", "content": serialized}]
                )
            ),
        )
        await recorder.flush()
        path = next(recorder.root.iterdir())
        disk = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]
        data = disk[0]["content"]["data"]
        result = json.loads(data["tool_results"][0]["content"])
        assert result == {
            "password": "[redacted]",
            "secret": "[redacted]",
            "text": "ordinary tool result",
        }
        assert "synthetic-private" not in path.read_text(encoding="utf-8")
        assert "123456" not in path.read_text(encoding="utf-8")
        exported = await recorder.export("10001", "per_group")
        assert exported["rows"] == disk
        assert exported["missing"]["corrupt_lines"] == 0
        disk[0]["content"]["data"]["tool_results"][0]["content"] = serialized
        path.write_text(json.dumps(disk[0]) + "\n", encoding="utf-8")
        exported = await recorder.export("10001", "per_group")
        assert exported["rows"] == []
        assert exported["missing"]["corrupt_lines"] == 1
        await recorder.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "escaped_text",
    [
        r"https:\/\/example.invalid\/tool?signature=synthetic-private",
        r"Bearer synthetic\u002dprivate-token",
        r"data:application\/octet-stream;base64,synthetic-private",
    ],
)
@pytest.mark.parametrize("has_sensitive_key", [True, False])
def test_decoded_json_text_is_filtered_before_disk_and_export(
    tmp_path, escaped_text, has_sensitive_key
):
    from astrbot_plugin_bond.core.taskregistry import TaskRegistry
    from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder

    async def run():
        recorder = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="full"
        )
        capture = create_capture(
            SimpleNamespace(diagnostics=recorder, scope_mode="per_group"),
            "10001",
            "20001",
            "30001",
            "synthetic",
            "escaped-text",
        )
        serialized = (
            '{"text":"'
            + escaped_text
            + '"'
            + (',"password":123' if has_sensitive_key else "")
            + "}"
        )
        capture.content(
            "request",
            request_content(
                SimpleNamespace(
                    tool_calls_result=[{"role": "tool", "content": serialized}]
                )
            ),
        )
        await recorder.flush()
        path = next(recorder.root.iterdir())
        disk = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]
        assert "synthetic-private" not in path.read_text(encoding="utf-8")
        parsed = json.loads(disk[0]["content"]["data"]["tool_results"][0]["content"])
        assert "synthetic-private" not in json.dumps(parsed)
        if has_sensitive_key:
            assert parsed["password"] == "[redacted]"
        exported = await recorder.export("10001", "per_group")
        assert exported["rows"] == disk
        assert exported["missing"]["corrupt_lines"] == 0
        await recorder.close()

    asyncio.run(run())
