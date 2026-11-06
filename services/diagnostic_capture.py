"""诊断观察点：冻结可信身份与清除代次，不参与评分或回复决策。"""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass

CURRENT = ContextVar("bond_diagnostic_trace", default=None)


@dataclass(frozen=True)
class Capture:
    recorder: object
    bot: str
    mode: str
    group: str
    user: str
    trace_id: str
    generation: object

    def content(self, stage, content):
        try:
            self.recorder.emit_content(
                self.bot,
                self.mode,
                self.group,
                self.user,
                trace_id=self.trace_id,
                stage=stage,
                content=content,
                generation=self.generation,
            )
        except Exception:
            pass


def create_capture(favor, bot, group, user, platform, message_id):
    try:
        recorder = favor.diagnostics
        if recorder is None or not recorder.enabled:
            return None
        from ..core.scope import account_id

        for identity in (bot, group, user):
            account_id(identity)
        if not message_id:
            return None
        trace = hashlib.sha256(
            json.dumps(
                [platform, bot, group, user, message_id], ensure_ascii=False
            ).encode()
        ).hexdigest()
        return Capture(
            recorder,
            bot,
            favor.scope_mode,
            group,
            user,
            trace,
            recorder.capture_token(bot, favor.scope_mode),
        )
    except Exception:
        return None


def interaction_capture(favor, interaction):
    current = CURRENT.get()
    if current is not None and current.trace_id == interaction.event_id:
        return current
    return create_capture(
        favor,
        interaction.bot_id,
        interaction.group_id,
        interaction.user_id,
        interaction.platform,
        interaction.message_id,
    )


def event_capture(deps, event):
    try:
        identity = (
            str(event.get_self_id() or ""),
            str(event.get_group_id() or ""),
            str(event.get_sender_id() or ""),
            str(event.get_platform_name() or ""),
            str(getattr(getattr(event, "message_obj", None), "message_id", "") or ""),
        )
        binding = getattr(event, "_bond_diagnostic_capture", None)
        if isinstance(binding, tuple) and binding[0] == identity:
            return binding[1]
        capture = create_capture(deps.favor, *identity)
        event._bond_diagnostic_capture = (identity, capture)
        return capture
    except Exception:
        return None


def capture_content(stage, content):
    capture = CURRENT.get()
    if capture is not None:
        capture.content(stage, content)


def request_content(req):
    """只选择模型请求内容；不遍历 Provider、配置或任意对象属性。"""
    content = {}
    for key in ("system_prompt", "prompt", "contexts", "extra_user_content_parts"):
        value = getattr(req, key, None)
        if isinstance(value, (str, list, dict)):
            content[key] = value
    tools = getattr(req, "func_tool", None)
    if tools is not None:
        try:
            content["tools"] = tools.openai_schema()
        except Exception:
            content["tools_unavailable"] = True
    results = getattr(req, "tool_calls_result", None)
    if results is not None:
        results = results if isinstance(results, list) else [results]
        safe_results = []
        for item in results[:64]:
            if isinstance(item, dict):
                safe_results.append(item)
            else:
                try:
                    safe_results.append(item.to_openai_messages())
                except Exception:
                    safe_results.append({"unavailable": True})
        content["tool_results"] = safe_results
    for field, label in (
        ("image_urls", "images_count"),
        ("audio_urls", "audios_count"),
    ):
        value = getattr(req, field, None)
        if isinstance(value, (list, tuple)) and value:
            content[label] = len(value)
    return content


def record_bound_response(event, response):
    """只补充同一可信消息已有的诊断，不因响应钩子另建采集范围。"""
    try:
        binding = getattr(event, "_bond_diagnostic_capture", None)
        identity = (
            str(event.get_self_id() or ""),
            str(event.get_group_id() or ""),
            str(event.get_sender_id() or ""),
            str(event.get_platform_name() or ""),
            str(getattr(getattr(event, "message_obj", None), "message_id", "") or ""),
        )
        if (
            isinstance(binding, tuple)
            and binding[0] == identity
            and isinstance(binding[1], Capture)
        ):
            binding[1].content(
                "response", {"text": getattr(response, "completion_text", None)}
            )
    except Exception:
        pass
