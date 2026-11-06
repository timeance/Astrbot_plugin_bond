"""详细诊断仅使用合成数据，验证内容分档、容量和清除边界。"""

import asyncio
import json

import pytest

from astrbot_plugin_bond.core.taskregistry import TaskRegistry
from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder

B, G, U, M = "synthetic-bot", "synthetic-group", "synthetic-user", "per_group"
TRACE = "a" * 32


def content(recorder, stage="chat", **kwargs):
    return recorder.emit_content(B, M, G, U, trace_id=TRACE, stage=stage, **kwargs)


@pytest.mark.parametrize(
    "level,stages",
    [
        ("assessment", []),
        ("raw", ["chat", "response"]),
        ("evaluation", ["chat", "response", "evaluation"]),
        ("full", ["chat", "response", "evaluation", "request"]),
    ],
)
def test_levels_and_trace_join(tmp_path, level, stages):
    async def run():
        r = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level=level
        )
        for stage in r.STAGES:
            assert content(r, stage, content={"text": "合成原文"}) == (stage in stages)
        assert r.emit(B, M, G, U, "assessment", trace_id=TRACE)
        result = await r.export(B, M)
        assert len(result["rows"]) == len(stages) + 1
        assert len({row["fields"]["trace_id"] for row in result["rows"]}) == 1
        assert TRACE not in json.dumps(result)
        await r.close()

    asyncio.run(run())


def test_snapshot_redacts_credential_and_media_not_objects(tmp_path):
    class Poison:
        def __str__(self):
            raise AssertionError("不可转储任意对象")

    async def run():
        r = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="full"
        )
        original = {
            "text": "合成对话",
            "api_key": "synthetic-secret",
            "image_url": "synthetic-media",
            "other": Poison(),
            "nested": {"authorization": "synthetic-auth"},
            "link": "https://example.invalid/item?signature=synthetic-signed",
            "credential_text": "Bearer synthetic-token-abcdef",
        }
        assert content(r, "request", content=original)
        original["text"] = "不应改写"
        rows = (await r.export(B, M))["rows"]
        assert len(rows) == 1
        assert rows[0]["content"]["data"]["text"] == "合成对话"
        text = json.dumps(rows)
        for forbidden in [
            "synthetic-secret",
            "synthetic-media",
            "synthetic-auth",
            "synthetic-signed",
            "synthetic-token-abcdef",
        ]:
            assert forbidden not in text
        assert rows[0]["content"]["omitted"]
        await r.close()

    asyncio.run(run())


def test_truncation_capacity_queue_and_export_bytes(tmp_path):
    async def run():
        r = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="raw"
        )
        assert r.max_bytes == 100_000_000
        r.MAX_QUEUE_BYTES = 40_000
        assert content(r, content={"text": "文" * 100_000})
        assert not content(r, content={"text": "文" * 100_000})
        await r.flush()
        rows = (await r.export(B, M))["rows"]
        assert rows[0]["content"]["truncated"]
        r.MAX_EXPORT_BYTES = 40_000
        for _ in range(4):
            assert content(r, content={"text": "文" * 100_000})
            await r.flush()
        result = await r.export(B, M)
        assert len(result["rows"]) == 1
        assert result["missing"]["export_truncated"] == 4
        await r.close()

    asyncio.run(run())


def test_clear_invalidates_inflight_and_preserves_other_namespace(tmp_path):
    async def run():
        r = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="raw"
        )
        old = r.capture_token(B, M)
        assert content(r, content="before", generation=old)
        assert r.emit_content(
            "other", M, G, U, trace_id=TRACE, stage="chat", content="other"
        )
        await r.flush()
        result = await r.clear(B, M)
        assert result["clear_succeeded"]
        assert result["disk_bytes"] == 0
        assert result["total_disk_bytes"] > 0
        assert not content(r, content="late", generation=old)
        assert not r.emit(B, M, G, U, "assessment", generation=old)
        assert content(r, content="after", generation=r.capture_token(B, M))
        result = await r.export(B, M)
        assert [row["content"]["data"] for row in result["rows"]] == ["after"]
        await r.close()

    asyncio.run(run())


def test_disk_size_survives_restart_and_unavailable_is_not_zero(tmp_path):
    async def run():
        root = tmp_path / "logs"
        r = DiagnosticRecorder(root, TaskRegistry(), True, detail_level="raw")
        assert content(r, content="saved")
        await r.close()
        disabled = DiagnosticRecorder(root, TaskRegistry())
        stats = await disabled.status(B, M)
        assert stats["disk_bytes"] == stats["total_disk_bytes"] > 0
        assert stats["size_available"]
        (root / "foreign.txt").write_text("unrecognized")
        stats = await disabled.status(B, M)
        assert stats["disk_bytes"] is None
        assert not stats["size_available"]
        await disabled.close()

    asyncio.run(run())


def test_start_and_periodic_cleanup_when_disabled(tmp_path):
    async def run():
        now = [2_000_000_000]
        root = tmp_path / "logs"
        r = DiagnosticRecorder(
            root, TaskRegistry(), True, detail_level="raw", clock=lambda: now[0]
        )
        assert content(r, content="saved")
        await r.close()
        registry = TaskRegistry()
        disabled = DiagnosticRecorder(root, registry, clock=lambda: now[0])
        disabled.MAINTENANCE_SECONDS = 0.01
        await disabled.start()
        assert (await disabled.status(B, M))["disk_bytes"] > 0
        now[0] += 8 * 86400
        await asyncio.sleep(0.05)
        assert (await disabled.status(B, M))["disk_bytes"] == 0
        await disabled.close()
        assert registry.size == 0

    asyncio.run(run())


def test_v1_is_readable_and_capacity_rotates(tmp_path):
    async def run():
        r = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="raw", max_bytes=2500
        )
        assert r.emit(B, M, G, U, "assessment", status="ok")
        await r.flush()
        path = next(r.root.iterdir())
        row = json.loads(path.read_text())
        row["version"] = 1
        path.write_text(json.dumps(row) + "\n")
        assert (await r.export(B, M))["rows"][0]["version"] == 1
        for _ in range(10):
            assert content(r, content="x" * 500)
            await r.flush()
        assert (await r.status(B, M))["total_disk_bytes"] <= 2500
        await r.close()

    asyncio.run(run())


def test_control_characters_truncate_by_serialized_size(tmp_path):
    async def run():
        r = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="raw"
        )
        assert content(r, content={"text": "\x00" * 100_000, "extra": "\x01" * 100_000})
        rows = (await r.export(B, M))["rows"]
        assert len(rows) == 1
        assert rows[0]["content"]["truncated"]
        await r.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "replacement", [10**15, -(10**15), float("inf"), float("nan"), object()]
)
def test_replaced_values_near_budget_survive_disk_and_export(tmp_path, replacement):
    async def run():
        r = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="full"
        )
        payload = {"id": replacement, **{key: "x" * 32766 for key in "abcd"}}
        assert content(r, "request", content=payload)
        await r.flush()
        disk_rows = [
            json.loads(line)
            for path in r.root.iterdir()
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        assert len(disk_rows) == 1
        data = disk_rows[0]["content"]["data"]
        assert data["id"] is None
        assert r._content_snapshot(data)["data"] == data
        exported = await r.export(B, M)
        assert exported["rows"] == disk_rows
        assert exported["missing"]["corrupt_lines"] == 0
        assert exported["rows"][0]["content"]["omitted"]
        assert exported["rows"][0]["content"]["truncated"]
        await r.close()

    asyncio.run(run())


def test_default_and_invalid_detail_levels_preserve_assessment_scope(tmp_path):
    from astrbot_plugin_bond.core.settings import snapshot

    async def run():
        legacy = snapshot({"diagnostics": {"enabled": True}})["values"]
        assert legacy["diagnostics.enabled"] is True
        assert legacy["diagnostics.detail_level"] == "assessment"
        for index, options in enumerate(({}, {"detail_level": "unknown"})):
            r = DiagnosticRecorder(
                tmp_path / str(index), TaskRegistry(), True, **options
            )
            assert r.detail_level == "assessment"
            for stage in r.STAGES:
                assert not content(r, stage, content={"text": "must not collect"})
            assert r.emit(B, M, G, U, "assessment", status="ok")
            result = await r.export(B, M)
            assert [row["kind"] for row in result["rows"]] == ["assessment"]
            await r.close()
        for level in ("raw", "evaluation", "full"):
            saved = snapshot({"diagnostics": {"enabled": True, "detail_level": level}})[
                "values"
            ]
            assert saved["diagnostics.detail_level"] == level

    asyncio.run(run())


def test_supported_omitted_deep_and_wide_content_is_bounded_and_idempotent(tmp_path):
    class Poison:
        def __str__(self):
            raise AssertionError("不得字符串化对象")

        __repr__ = __str__

    class ForeignDict(dict):
        def items(self):
            raise AssertionError("不得遍历未知字典子类")

    async def run():
        r = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="full"
        )
        cycle = {}
        cycle["child"] = cycle
        payload = {
            "supported": (
                None,
                True,
                False,
                0,
                -1.5,
                "plain text",
                {"safe": "普通内容"},
            ),
            "unsupported": [
                Poison(),
                ForeignDict(secret="synthetic"),
                b"bytes",
                {"set"},
            ],
            "depth": cycle,
            "wide": list(range(200)),
            "controls": "\x00" * 100000,
            "tail": {key: "文" * 20000 for key in "abcd"},
        }
        assert content(r, "request", content=payload)
        await r.flush()
        disk = [
            json.loads(line)
            for path in r.root.iterdir()
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        assert len(disk) == 1
        snapshot = disk[0]["content"]
        data = snapshot["data"]
        assert data["supported"] == [
            None,
            True,
            False,
            0,
            -1.5,
            "plain text",
            {"safe": "普通内容"},
        ]
        assert data["unsupported"] == [None] * 4
        assert len(data["wide"]) == 128
        nested = data["depth"]
        for _ in range(8):
            nested = nested["child"]
        assert nested is None
        assert snapshot["omitted"] and snapshot["truncated"]
        assert len(json.dumps(data, ensure_ascii=False).encode()) <= r.CONTENT_BYTES
        assert r._content_snapshot(data)["data"] == data
        result = await r.export(B, M)
        assert result["rows"] == disk
        assert result["missing"]["corrupt_lines"] == 0
        await r.close()

    asyncio.run(run())


def test_empty_nested_containers_consume_budget_and_survive_export(tmp_path):
    async def run():
        recorder = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="full"
        )
        payload = []
        for _ in range(3):
            payload = [payload] * 128
        snapshot = recorder._content_snapshot(payload)
        assert snapshot["truncated"]
        assert len(json.dumps(snapshot["data"]).encode()) <= recorder.CONTENT_BYTES
        assert recorder._content_snapshot(snapshot["data"])["data"] == snapshot["data"]
        assert content(recorder, "request", content=payload)
        await recorder.flush()
        disk = [
            json.loads(line)
            for path in recorder.root.iterdir()
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        exported = await recorder.export(B, M)
        assert len(disk) == 1 and exported["rows"] == disk
        assert exported["missing"]["corrupt_lines"] == 0
        await recorder.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "serialized",
    [
        json.dumps({"password": 123456, "text": "x" * 32768}),
        '{"password":123456,',
        "[" * 40 + "0" + "]" * 40,
        json.dumps([[]] * 5000),
    ],
)
def test_unparseable_or_oversized_json_is_omitted_and_exportable(tmp_path, serialized):
    async def run():
        recorder = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, detail_level="full"
        )
        assert content(recorder, "request", content={"text": serialized})
        await recorder.flush()
        path = next(recorder.root.iterdir())
        disk = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]
        snapshot = disk[0]["content"]
        assert snapshot["data"] == {"text": "[JSON content omitted]"}
        assert snapshot["truncated"] and snapshot["omitted"]
        exported = await recorder.export(B, M)
        assert exported["rows"] == disk
        assert exported["missing"]["corrupt_lines"] == 0
        await recorder.close()

    asyncio.run(run())
