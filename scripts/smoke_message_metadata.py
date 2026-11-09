"""真实 AstrBot 消息元数据取证；仅合成 OneBot 数据，禁止网络。"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

BOT = "10000001"
OTHER = "8002"
SENDER = "8001"
GROUP = "7001"


def _segment(kind: str, **data) -> dict:
    return {"type": kind, "data": data}


def _payload(segments: list, message_id: int, sender: str = SENDER) -> dict:
    return {
        "time": 1_804_003_200,
        "self_id": int(BOT),
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": message_id,
        "group_id": int(GROUP),
        "user_id": int(sender),
        "sender": {"user_id": int(sender), "nickname": f"synthetic-{sender}"},
        "message": copy.deepcopy(segments),
        "raw_message": "".join(
            (
                part["data"]["text"]
                if part["type"] == "text"
                else f"[CQ:{part['type']},"
                + ",".join(f"{key}={value}" for key, value in part["data"].items())
                + "]"
            )
            for part in segments
        ),
        "font": 0,
    }


def _cases() -> list[dict]:
    def text(value):
        return _segment("text", text=value)

    def at(user=BOT):
        return _segment("at", qq=user)

    def reply(message_id):
        return _segment("reply", id=str(message_id))

    # expected_text 仅锁定适配器契约；过滤结果始终调用当前生产函数取证。
    cases = [
        ("plain_greeting", [text("早安")], "早安", False, False),
        ("at_bot_greeting", [at(), text(" 早安")], "早安", True, False),
        ("at_bot_punctuation", [at(), text("早安！")], "早安！", True, False),
        ("at_bot_emoji", [at(), text("早安😊")], "早安😊", True, False),
        ("plain_punctuation", [text("早安！")], "早安！", False, False),
        ("plain_emoji", [text("早安😊")], "早安😊", False, False),
        (
            "repeated_at_contiguous",
            [at(), at(), text("早安")],
            f" @synthetic-bot({BOT}) 早安",
            True,
            False,
        ),
        (
            "repeated_at_separated",
            [at(), text("早安"), at()],
            "早安",
            True,
            False,
        ),
        (
            "at_other",
            [at(OTHER), text("早安")],
            f" @synthetic-other({OTHER}) 早安",
            False,
            False,
        ),
        (
            "mixed_bot_other",
            [at(), at(OTHER), text("早安")],
            f" @synthetic-other({OTHER}) 早安",
            True,
            False,
        ),
        (
            "mixed_other_bot",
            [at(OTHER), at(), text("早安")],
            f" @synthetic-other({OTHER}) 早安",
            True,
            False,
        ),
        (
            "at_bot_substantive",
            [at(), text("谢谢你的帮助")],
            "谢谢你的帮助",
            True,
            False,
        ),
        ("reply_bot_greeting", [reply(9101), text("早安")], "早安", False, True),
        ("reply_bot", [reply(9101), text("谢谢你的帮助")], "谢谢你的帮助", False, True),
        (
            "reply_other",
            [reply(9102), text("谢谢你的帮助")],
            "谢谢你的帮助",
            False,
            False,
        ),
        (
            "reply_unavailable",
            [reply(9103), text("谢谢你的帮助")],
            "谢谢你的帮助",
            False,
            False,
        ),
        (
            "reply_sender_missing",
            [reply(9104), text("谢谢你的帮助")],
            "谢谢你的帮助",
            False,
            False,
        ),
        (
            "reply_other_at_bot",
            [reply(9102), at(), text("谢谢你的帮助")],
            "谢谢你的帮助",
            True,
            False,
        ),
        (
            "reply_unavailable_at_bot",
            [reply(9103), at(), text("谢谢你的帮助")],
            "谢谢你的帮助",
            True,
            False,
        ),
        (
            "reply_sender_missing_at_bot",
            [reply(9104), at(), text("谢谢你的帮助")],
            "谢谢你的帮助",
            True,
            False,
        ),
        (
            "reply_bot_at_other",
            [reply(9101), at(OTHER), text("谢谢你的帮助")],
            f" @synthetic-other({OTHER}) 谢谢你的帮助",
            False,
            True,
        ),
        (
            "reply_bot_at_bot",
            [reply(9101), at(), text("谢谢你的帮助")],
            "谢谢你的帮助",
            True,
            True,
        ),
        (
            "same_reply_twice",
            [reply(9101), reply(9101), text("谢谢你的帮助")],
            "谢谢你的帮助",
            False,
            True,
        ),
        (
            "different_bot_replies",
            [reply(9101), reply(9105), text("谢谢你的帮助")],
            "谢谢你的帮助",
            False,
            True,
        ),
    ]
    return [
        dict(
            name=name,
            segments=segments,
            expected_text=expected,
            expected_at=has_at,
            expected_reply=has_reply,
        )
        for name, segments, expected, has_at, has_reply in cases
    ]


def _install_guard(session_dir: Path) -> list[dict]:
    violations = []

    def reject(event, target):
        violations.append({"event": event, "target": str(target)})
        raise PermissionError(f"metadata smoke 禁止操作: {event}: {target}")

    def check_path(event, value):
        if isinstance(value, int) or value is None:
            return
        target = Path(os.fsdecode(value)).resolve()
        if not target.is_relative_to(session_dir):
            reject(event, target)

    def audit(event, args):
        if (event.startswith("socket.") and event != "socket.gethostname") or event in {
            "subprocess.Popen",
            "os.system",
            "os.exec",
            "os.posix_spawn",
            "os.startfile",
        }:
            reject(event, args[0] if args else "")
        if event == "open":
            path, mode, flags = args
            if (mode and any(mark in mode for mark in "wax+")) or (
                flags
                & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            ):
                check_path(event, path)
        elif event == "sqlite3.connect":
            check_path(event, args[0])
        elif event in {
            "os.mkdir",
            "os.remove",
            "os.rmdir",
            "os.chmod",
            "os.chown",
            "os.utime",
            "os.truncate",
            "os.chdir",
        }:
            check_path(event, args[0])
        elif event in {"os.rename", "os.link", "os.symlink"}:
            check_path(event, args[0])
            check_path(event, args[1])

    sys.addaudithook(audit)
    return violations


async def check() -> dict:
    # 真框架仅在隔离目录、禁止写字节码及审计拦截均已生效后加载。
    import astrbot
    from aiocqhttp import Event
    from aiocqhttp.exceptions import ActionFailed
    from astrbot.api.message_components import At, Reply
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
        AiocqhttpMessageEvent,
    )
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
        AiocqhttpAdapter,
    )

    from astrbot_plugin_bond.api.listeners import _chain_flags
    from astrbot_plugin_bond.core.assessment import locally_relevant

    from smoke_astrbot import check_framework_version

    check_framework_version()
    reply_messages = {
        9101: _payload([_segment("text", text="合成 Bot 上文")], 9101, BOT),
        9102: _payload([_segment("text", text="合成他人上文")], 9102, OTHER),
        9105: _payload([_segment("text", text="合成 Bot 另一上文")], 9105, BOT),
        9104: _payload([_segment("text", text="合成来源缺失上文")], 9104, BOT),
    }
    reply_messages[9104].pop("sender")
    calls = []
    unexpected_actions = []

    async def call_action(action, **params):
        record = {"action": action, "params": copy.deepcopy(params)}
        calls.append(record)
        assert params.get("self_id") == int(BOT), record
        if action == "get_msg":
            message_id = params["message_id"]
            if message_id == 9103:
                record["error"] = "synthetic message not found"
                raise ActionFailed(
                    {"status": "failed", "retcode": 100, "wording": record["error"]}
                )
            response = reply_messages[message_id]
        elif action == "get_group_member_info":
            assert params["group_id"] == int(GROUP), record
            assert str(params["user_id"]) in {BOT, OTHER}, record
            response = {"user_id": params["user_id"], "card": ""}
        elif action == "get_stranger_info":
            user_id = str(params["user_id"])
            assert user_id in {BOT, OTHER}, record
            response = {
                "user_id": int(user_id),
                "nickname": ("synthetic-bot" if user_id == BOT else "synthetic-other"),
            }
        else:
            unexpected_actions.append(record)
            raise AssertionError(f"禁止的 OneBot action: {action}")
        record["response"] = copy.deepcopy(response)
        return copy.deepcopy(response)

    # 只借用真实转换方法，不构造监听服务器或启动适配器。
    adapter = AiocqhttpAdapter.__new__(AiocqhttpAdapter)
    adapter.bot = SimpleNamespace(call_action=call_action)
    rows = []
    for index, case in enumerate(_cases(), 1):
        raw = _payload(case["segments"], 10000 + index)
        call_start = len(calls)
        converted = await adapter.convert_message(
            Event.from_payload(copy.deepcopy(raw))
        )
        assert converted is not None, case["name"]
        event = AiocqhttpMessageEvent(
            converted.message_str,
            converted,
            SimpleNamespace(name="aiocqhttp", id="synthetic-metadata"),
            converted.session_id,
            adapter.bot,
        )
        has_at, is_reply = _chain_flags(event)
        assert converted.message_str == case["expected_text"], (
            case["name"],
            converted.message_str,
            case["expected_text"],
        )
        raw_at = any(isinstance(c, At) and str(c.qq) == BOT for c in converted.message)
        raw_reply = any(
            isinstance(c, Reply) and str(c.sender_id) == BOT for c in converted.message
        )
        assert (raw_at, raw_reply) == (
            case["expected_at"],
            case["expected_reply"],
        ), case["name"]
        blocked = case["name"] in {
            "mixed_bot_other",
            "mixed_other_bot",
            "reply_other_at_bot",
            "reply_unavailable_at_bot",
            "reply_sender_missing_at_bot",
            "reply_bot_at_other",
            "different_bot_replies",
        }
        expected_flags = (False, False) if blocked else (raw_at, raw_reply)
        assert (has_at, is_reply) == expected_flags, case["name"]
        ats = [
            json.loads(part.json())
            for part in converted.message
            if isinstance(part, At)
        ]
        replies = [
            json.loads(part.json())
            for part in converted.message
            if isinstance(part, Reply)
        ]
        assert len(ats) == sum(part["type"] == "at" for part in raw["message"])
        assert len(replies) == sum(part["type"] == "reply" for part in raw["message"])
        if replies:
            reply = replies[0]
            if case["name"] in {
                "reply_unavailable",
                "reply_sender_missing",
                "reply_unavailable_at_bot",
                "reply_sender_missing_at_bot",
            }:
                assert reply["sender_id"] in (None, 0, "", "0"), reply
                assert not reply["chain"] and not reply["message_str"], reply
            else:
                source = BOT if raw_reply else OTHER
                assert str(reply["sender_id"]) == source, reply
                assert str(reply["qq"]) == source, reply
                assert reply["chain"] and reply["message_str"], reply
        directed = has_at or is_reply
        rows.append(
            {
                "name": case["name"],
                "raw_synthetic_event": raw,
                "message_str": event.message_str,
                "At": ats,
                "Reply": replies,
                "has_at_bot": raw_at,
                "is_reply_bot": raw_reply,
                "assessment_at_bot": has_at,
                "assessment_reply_bot": is_reply,
                "directed": directed,
                "locally_relevant": locally_relevant(event.message_str, directed),
                "locally_relevant_if_directed": locally_relevant(
                    event.message_str, True
                ),
                "onebot_calls": copy.deepcopy(calls[call_start:]),
            }
        )
    assert not unexpected_actions, unexpected_actions
    assert {call["action"] for call in calls} == {
        "get_msg",
        "get_group_member_info",
        "get_stranger_info",
    }

    def source_record(value):
        path = Path(inspect.getfile(value)).resolve()
        return {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    return {
        "status": "PASS",
        "astrbot_version": astrbot.__version__,
        "python_version": sys.version,
        "synthetic_data_only": True,
        "sources": {
            "adapter": source_record(AiocqhttpAdapter),
            "chain_flags": source_record(_chain_flags),
            "locally_relevant": source_record(locally_relevant),
        },
        "case_count": len(rows),
        "cases": rows,
        "limits": [
            "仅真实 aiocqhttp 消息转换及当前插件元数据/本地过滤函数；未运行完整 pipeline。",
            "OneBot 三种 action 均为合成响应；未连接平台、网络或真实 Provider。",
            "Python 审计拦截用于本进程取证隔离，不等同于操作系统安全沙箱。",
            "过滤结果反映运行时源码；不证明语义归因正确，也不改变生产逻辑。",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--session-dir", default=os.environ.get("BOND_SMOKE_SESSION_DIR")
    )
    parser.add_argument("--output", help="JSON 路径；相对路径以 session-dir 为基准")
    args = parser.parse_args()
    if not args.session_dir:
        parser.error("必须提供 --session-dir")
    if not __debug__:
        parser.error("不能使用 -O；smoke 依赖断言")
    if any(name == "astrbot" or name.startswith("astrbot.") for name in sys.modules):
        parser.error("须在未加载 AstrBot 的独立进程运行")
    session_dir = Path(args.session_dir).resolve()
    output = Path(args.output) if args.output else Path("message-metadata.json")
    output = (output if output.is_absolute() else session_dir / output).resolve()
    if not output.is_relative_to(session_dir) or output.suffix.lower() != ".json":
        parser.error("--output 必须是 session-dir 内的 .json 文件")
    plugin_dir = Path(__file__).resolve().parent.parent
    session_dir.mkdir(parents=True, exist_ok=True)
    sys.dont_write_bytecode = True
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(session_dir)
    tempfile.tempdir = str(session_dir)
    os.chdir(session_dir)
    # Windows 事件循环自用 socketpair 在拦截前创建；真框架加载后不允许创建 socket。
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    violations = _install_guard(session_dir)
    try:
        runtime_dir = Path(
            tempfile.mkdtemp(prefix="metadata-runtime-", dir=session_dir)
        )
        os.environ["ASTRBOT_ROOT"] = str(runtime_dir)
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        sys.path.insert(0, str(plugin_dir.parent))
        report = loop.run_until_complete(check())
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()
        asyncio.set_event_loop(None)
    report["isolation"] = {
        "session_dir": str(session_dir),
        "runtime_dir": str(runtime_dir),
        "network_blocked": True,
        "subprocess_blocked": True,
        "writes_confined_to_session": True,
        "violations": violations,
    }
    assert not violations, violations
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"PASS AstrBot {report['astrbot_version']}: {report['case_count']} metadata cases; {output}"
    )


if __name__ == "__main__":
    main()
