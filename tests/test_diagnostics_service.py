"""只使用合成身份与指标，不接触消息、关系库或网络。"""

import asyncio
import json
import threading
from pathlib import Path

import pytest

from astrbot_plugin_bond.core.taskregistry import TaskRegistry
from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder

BOT, OTHER_BOT = "synthetic-bot-900001", "synthetic-bot-900002"
GROUP, OTHER_GROUP = "synthetic-group-800001", "synthetic-group-800002"
USER = "synthetic-user-700001"
TEXT = "SYNTHETIC_MESSAGE_MUST_NEVER_APPEAR"


def emit(recorder, bot=BOT, mode="per_group", group=GROUP, **fields):
    return recorder.emit(bot, mode, group, USER, "assessment", **fields)


def test_disabled_zero_io_or_tasks(tmp_path, monkeypatch):
    async def run():
        def forbidden(*args, **kwargs):
            raise AssertionError("disabled 产生副作用")

        registry = TaskRegistry()
        root = tmp_path / "disabled"
        with monkeypatch.context() as patch:
            for method in ("mkdir", "exists", "is_symlink", "iterdir", "open", "stat"):
                patch.setattr(Path, method, forbidden)
            patch.setattr(registry, "spawn", forbidden)
            patch.setattr("secrets.token_bytes", forbidden)
            patch.setattr("secrets.token_hex", forbidden)
            recorder = DiagnosticRecorder(root, registry)
            assert not emit(recorder, text=object())
            await recorder.flush()
            await recorder.close()
            assert recorder.stats()["enabled"] is False
            assert recorder.stats()["counters"]["accepted"] == 0
        assert not root.exists()
        assert registry.size == 0

    asyncio.run(run())


def test_allowlist_pseudonyms_and_input_snapshot(tmp_path):
    async def run():
        recorder = DiagnosticRecorder(tmp_path / "logs", TaskRegistry(), enabled=True)
        before = {"affinity": 10, "familiarity": 2.5, "trust": -1}
        bands = [0, 1, 2]
        assert emit(
            recorder,
            status="ok",
            reason="no_change",
            before=before,
            after={"affinity": 11},
            delta={"affinity": 1},
            bands_before=bands,
            bands_after=(1, 2, 3),
            term_before="r01",
            term_after="r02",
            config_hash="a" * 64,
            elapsed_ms=12.5,
            tokens=17,
            confidence=0.7,
            temporary=True,
            policy_version="0.2.5",
            lexicon_version="1",
            event_id="b" * 64,
            text=TEXT,
            message=TEXT,
            error=TEXT,
            api_key=TEXT,
            provider_url="https://synthetic.invalid/secret",
        )
        before["affinity"] = 999
        bands[0] = 4
        result = await recorder.export(BOT, "per_group")
        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert row["fields"]["before"]["affinity"] == 10
        assert row["fields"]["bands_before"] == [0, 1, 2]
        assert row["fields"]["term_after"] == "r02"
        assert row["fields"]["event_id"] != "b" * 64
        assert row["fields"]["config_hash"] == "a" * 64
        assert result["missing"]["filtered_fields"] == 5
        assert result["stats"]["gap"]
        serialized = json.dumps(result) + "".join(
            path.name + path.read_text() for path in recorder.root.iterdir()
        )
        for forbidden in (
            BOT,
            GROUP,
            USER,
            TEXT,
            "synthetic.invalid",
            "provider_url",
            "api_key",
        ):
            assert forbidden not in serialized
        await recorder.close()

    asyncio.run(run())


def test_untrusted_field_values_are_not_stringified(tmp_path):
    class Poison:
        def __str__(self):
            raise AssertionError("不可读取正文对象")

        __repr__ = __str__

    async def run():
        recorder = DiagnosticRecorder(tmp_path / "logs", TaskRegistry(), True)
        assert emit(
            recorder,
            reason=TEXT,
            status=TEXT,
            before={"affinity": Poison()},
            bands_before=[0, 1, TEXT],
            term_after=TEXT,
            elapsed_ms=float("inf"),
            tokens=True,
            config_hash=TEXT,
            error=Poison(),
            text=Poison(),
        )
        result = await recorder.export(BOT, "per_group")
        assert result["rows"][0]["fields"] == {}
        assert result["missing"]["filtered_fields"] == 10
        assert not recorder.emit(BOT, "per_group", GROUP, USER, TEXT)
        assert not recorder.emit(BOT, "per_group", Poison(), USER, "assessment")
        assert emit(recorder, before={"affinity": 10**10000})
        assert TEXT not in json.dumps(await recorder.export(BOT, "per_group"))
        await recorder.close()

    asyncio.run(run())


def test_emit_has_no_disk_io_and_registry_owns_one_writer(tmp_path, monkeypatch):
    async def run():
        registry = TaskRegistry()
        recorder = DiagnosticRecorder(tmp_path / "logs", registry, True)
        with monkeypatch.context() as patch:
            patch.setattr(
                recorder, "_files", lambda *a, **kw: pytest.fail("emit 不应访问磁盘")
            )
            assert emit(recorder)
            assert emit(recorder)
            assert registry.size == 1
        await asyncio.sleep(0)
        await recorder.close()
        assert recorder.stats()["counters"]["written"] == 2
        assert registry.size == 0

    asyncio.run(run())


def test_queue_full_is_visible_and_bounded(tmp_path):
    async def run():
        recorder = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, queue_limit=2
        )
        assert emit(recorder)
        assert emit(recorder)
        assert not emit(recorder)
        stats = recorder.stats(BOT, "per_group")
        assert stats["queued"] == 2
        assert stats["counters"]["queue_full"] == stats["missing"]["dropped"] == 1
        await recorder.close()
        assert len((await recorder.export(BOT, "per_group"))["rows"]) == 2

    asyncio.run(run())


def test_write_failure_counts_and_recovery(tmp_path):
    async def run():
        root = tmp_path / "blocked"
        root.write_text("synthetic sentinel")
        recorder = DiagnosticRecorder(root, TaskRegistry(), True)
        assert emit(recorder)
        await recorder.flush()
        result = await recorder.export(BOT, "per_group")
        assert result["rows"] == []
        assert result["missing"]["dropped"] == result["missing"]["write_errors"] == 1
        assert TEXT not in json.dumps(result)
        root.unlink()
        assert emit(recorder, status="ok")
        await recorder.close()
        assert len((await recorder.export(BOT, "per_group"))["rows"]) == 1

    asyncio.run(run())


def test_no_running_loop_and_registry_failure_are_safe(tmp_path):
    recorder = DiagnosticRecorder(tmp_path / "logs", TaskRegistry(), True)
    assert not emit(recorder)
    assert recorder.stats()["missing"]["dropped"] == 1
    assert not recorder.root.exists()

    class BrokenRegistry:
        def spawn(self, coro, name=""):
            raise RuntimeError(TEXT)

    async def run():
        recorder = DiagnosticRecorder(tmp_path / "other", BrokenRegistry(), True)
        assert not emit(recorder)
        assert not recorder.root.exists()
        assert TEXT not in json.dumps(recorder.stats())
        await recorder.close()

    asyncio.run(run())


def test_namespaces_members_and_modes_are_isolated(tmp_path):
    async def run():
        recorder = DiagnosticRecorder(tmp_path / "logs", TaskRegistry(), True)
        for bot, mode, group in (
            (BOT, "per_group", GROUP),
            (BOT, "per_group", OTHER_GROUP),
            (BOT, "global", GROUP),
            (BOT, "global", OTHER_GROUP),
            (OTHER_BOT, "per_group", GROUP),
        ):
            assert emit(recorder, bot, mode, group)
        per_group = await recorder.export(BOT, "per_group")
        global_mode = await recorder.export(BOT, "global")
        other = await recorder.export(OTHER_BOT, "per_group")
        assert list(
            map(lambda result: len(result["rows"]), (per_group, global_mode, other))
        ) == [2, 2, 1]
        rows = per_group["rows"] + global_mode["rows"] + other["rows"]
        assert len({row["namespace"] for row in rows}) == 3
        assert (
            len({row["partition"] for row in rows})
            == len({row["member"] for row in rows})
            == 5
        )
        assert recorder.stats(BOT)["counters"]["accepted"] == 4
        assert recorder.stats(mode="per_group")["counters"]["accepted"] == 3
        await recorder.clear(BOT, "per_group")
        assert not (await recorder.export(BOT, "per_group"))["rows"]
        assert len((await recorder.export(BOT, "global"))["rows"]) == 2
        assert len((await recorder.export(OTHER_BOT, "per_group"))["rows"]) == 1
        await recorder.close()

    asyncio.run(run())


def test_rotation_global_quota_and_expiry(tmp_path):
    async def run():
        now = [2_000_000_000.0]
        recorder = DiagnosticRecorder(
            tmp_path / "logs",
            TaskRegistry(),
            True,
            max_bytes=1500,
            clock=lambda: now[0],
        )
        for index in range(8):
            assert emit(recorder, BOT if index % 2 else OTHER_BOT, elapsed_ms=index)
            await recorder.flush()
            assert sum(path.stat().st_size for path in recorder.root.iterdir()) <= 1500
            now[0] += 1
        assert recorder.stats()["missing"]["evicted_rows"] > 0
        assert all(path.stat().st_size <= 1500 for path in recorder.root.iterdir())
        now[0] += 7 * 86400 + 1
        assert (await recorder.export(BOT, "per_group"))["rows"] == []
        assert recorder.stats()["missing"]["expired_rows"] > 0
        assert list(recorder.root.iterdir()) == []
        await recorder.close()

    asyncio.run(run())


def test_retention_includes_previous_sessions_without_linking_identities(tmp_path):
    async def run():
        now = [2_000_000_000.0]
        root = tmp_path / "logs"
        first = DiagnosticRecorder(root, TaskRegistry(), True, clock=lambda: now[0])
        assert emit(first)
        await first.close()
        first_row = (await first.export(BOT, "per_group"))["rows"][0]
        now[0] += 1
        second = DiagnosticRecorder(root, TaskRegistry(), True, clock=lambda: now[0])
        assert len((await second.export(BOT, "per_group"))["rows"]) == 1
        assert emit(second)
        row = (await second.export(BOT, "per_group"))["rows"][-1]
        assert first_row["session"] != row["session"]
        assert first_row["namespace"] != row["namespace"]
        assert first_row["member"] != row["member"]
        now[0] += 7 * 86400 + 1
        await second.export(BOT, "per_group")
        assert not list(root.iterdir())
        assert second.stats()["missing"]["expired_rows"] == 2
        await second.close()

    asyncio.run(run())


def test_identity_counters_and_configuration_limits_are_bounded(tmp_path):
    async def run():
        recorder = DiagnosticRecorder(
            tmp_path / "logs", TaskRegistry(), True, queue_limit=10**9, max_bytes=10**12
        )
        assert recorder.max_bytes == 20 * 1024 * 1024
        assert recorder.queue_limit == 256
        for index in range(recorder.MAX_NAMESPACES + 10):
            emit(recorder, bot=f"synthetic-{index}")
        stats = recorder.stats()
        assert stats["tracked_namespaces"] == stats["namespace_limit"] == 128
        assert stats["counters"]["namespace_limit"] == 10
        assert (
            recorder.stats("synthetic-999", "per_group")["missing"]["unattributed"]
            == 10
        )
        assert stats["queued"] == 128
        await recorder.close()

    asyncio.run(run())


def test_close_drains_is_idempotent_and_refuses_new_events(tmp_path):
    async def run():
        registry = TaskRegistry()
        recorder = DiagnosticRecorder(tmp_path / "logs", registry, True)
        assert emit(recorder)
        await asyncio.wait_for(recorder.close(), timeout=5)
        await recorder.close()
        assert registry.size == 0
        assert recorder.stats()["closed"]
        assert not emit(recorder)
        result = await recorder.export(BOT, "per_group")
        assert len(result["rows"]) == 1
        assert result["missing"]["dropped"] == 1

    asyncio.run(run())


def test_clear_queued_rows_cannot_refill_and_does_not_touch_other_scope(tmp_path):
    async def run():
        recorder = DiagnosticRecorder(tmp_path / "logs", TaskRegistry(), True)
        relation = tmp_path / "relationships.sqlite"
        relation.write_bytes(b"synthetic relation sentinel")
        assert emit(recorder, elapsed_ms=1)
        assert emit(recorder, OTHER_BOT, elapsed_ms=2)
        await recorder.clear(BOT, "per_group")
        await recorder.flush()
        assert (await recorder.export(BOT, "per_group"))["rows"] == []
        assert len((await recorder.export(OTHER_BOT, "per_group"))["rows"]) == 1
        assert relation.read_bytes() == b"synthetic relation sentinel"
        assert emit(recorder, elapsed_ms=3)
        await recorder.close()
        assert [
            row["fields"]["elapsed_ms"]
            for row in (await recorder.export(BOT, "per_group"))["rows"]
        ] == [3]

    asyncio.run(run())


def test_clear_waits_for_inflight_flush_and_removes_old_pending(tmp_path, monkeypatch):
    async def run():
        registry = TaskRegistry()
        recorder = DiagnosticRecorder(tmp_path / "logs", registry, True)
        started, release = threading.Event(), threading.Event()
        persist = recorder._persist

        def delayed(batch):
            started.set()
            if not release.wait(5):
                raise OSError("synthetic test timeout")
            return persist(batch)

        monkeypatch.setattr(recorder, "_persist", delayed)
        assert emit(recorder, elapsed_ms=1)
        for _ in range(500):
            if started.is_set():
                break
            await asyncio.sleep(0.002)
        assert started.is_set()
        assert emit(recorder, elapsed_ms=2)
        clear_task = registry.spawn(recorder.clear(BOT, "per_group"), "test-clear")
        await asyncio.sleep(0)
        assert not clear_task.done()
        release.set()
        await asyncio.wait_for(clear_task, 5)
        await recorder.close()
        assert (await recorder.export(BOT, "per_group"))["rows"] == []
        assert recorder.stats(BOT, "per_group")["counters"]["cleared_rows"] == 2

    asyncio.run(run())


def test_cancellation_waits_for_disk_before_clear(tmp_path, monkeypatch):
    async def run():
        registry = TaskRegistry()
        recorder = DiagnosticRecorder(tmp_path / "logs", registry, True)
        started, release = threading.Event(), threading.Event()
        persist = recorder._persist

        def delayed(batch):
            started.set()
            release.wait(5)
            return persist(batch)

        monkeypatch.setattr(recorder, "_persist", delayed)
        assert emit(recorder)
        for _ in range(500):
            if started.is_set():
                break
            await asyncio.sleep(0.002)
        assert started.is_set()
        recorder._task.cancel()
        clear_task = registry.spawn(recorder.clear(BOT, "per_group"), "test-clear")
        await asyncio.sleep(0)
        assert not clear_task.done()
        release.set()
        await asyncio.wait_for(clear_task, 5)
        await recorder.close()
        assert (await recorder.export(BOT, "per_group"))["rows"] == []
        assert recorder.stats()["gap"]

    asyncio.run(run())


def test_corrupt_disk_rows_are_never_exported(tmp_path):
    async def run():
        recorder = DiagnosticRecorder(tmp_path / "logs", TaskRegistry(), True)
        assert emit(recorder)
        await recorder.close()
        path = next(recorder.root.iterdir())
        row = json.loads(path.read_text())
        row["fields"]["text"] = TEXT
        path.write_text(json.dumps(row) + "\n" + "{broken\n")
        result = await recorder.export(BOT, "per_group")
        assert result["rows"] == []
        assert result["missing"]["corrupt_lines"] == 2
        assert TEXT not in json.dumps(result)

    asyncio.run(run())


def test_dedicated_root_rejects_foreign_file_without_deletion(tmp_path):
    async def run():
        root = tmp_path / "logs"
        root.mkdir()
        sentinel = root / "relationships.sqlite"
        sentinel.write_bytes(b"synthetic sentinel")
        recorder = DiagnosticRecorder(root, TaskRegistry(), True)
        assert emit(recorder)
        await recorder.close()
        result = await recorder.export(BOT, "per_group")
        cleared = await recorder.clear(BOT, "per_group")
        assert not cleared["clear_succeeded"]
        assert result["rows"] == []
        assert recorder.stats()["missing"]["write_errors"] >= 1
        assert sentinel.read_bytes() == b"synthetic sentinel"
        assert list(root.iterdir()) == [sentinel]

    asyncio.run(run())


def test_symlink_root_is_rejected(tmp_path):
    target, link = tmp_path / "outside", tmp_path / "link"
    target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 身份不能创建符号链接")

    async def run():
        recorder = DiagnosticRecorder(link / "logs", TaskRegistry(), True)
        assert emit(recorder)
        await recorder.close()
        assert recorder.stats()["missing"]["write_errors"] == 1
        assert not list(target.iterdir())

    asyncio.run(run())


def test_none_fields_are_omitted_without_filtering_gap(tmp_path):
    async def run():
        recorder = DiagnosticRecorder(tmp_path / "logs", TaskRegistry(), True)
        assert recorder.emit(
            BOT,
            "per_group",
            GROUP,
            USER,
            "relation",
            event_type=None,
            event_id=None,
            tokens=None,
            confidence=None,
            status="committed",
            reason="manual",
            policy_version="0.2.5",
        )
        result = await recorder.export(BOT, "per_group")
        assert result["rows"][0]["fields"] == {
            "status": "committed",
            "reason": "manual",
            "policy_version": "0.2.5",
        }
        assert result["missing"]["filtered_fields"] == 0
        assert not result["stats"]["gap"]
        assert result["missing"]["historical_counters_unknown"] is True
        await recorder.close()

    asyncio.run(run())


def test_requested_integration_codes_and_partial_events(tmp_path):
    async def run():
        recorder = DiagnosticRecorder(tmp_path / "logs", TaskRegistry(), True)
        statuses = ("committed", "duplicate", "cooldown", "injected", "budget")
        reasons = ("automatic", "manual", "undo", "reset", "temporary", "temporary_end")
        for status in statuses:
            assert recorder.emit(
                BOT,
                "per_group",
                GROUP,
                USER,
                "call",
                status=status,
                tier="small",
                tokens=3,
                elapsed_ms=1.5,
            )
        for reason in reasons:
            assert recorder.emit(BOT, "per_group", GROUP, USER, "entry", reason=reason)
        assert recorder.emit(
            BOT,
            "per_group",
            GROUP,
            USER,
            "render",
            after={"affinity": 0, "familiarity": 0, "trust": 0},
            bands_after=(0, 0, 0),
            term_after="legacy",
        )
        result = await recorder.export(BOT, "per_group")
        assert len(result["rows"]) == len(statuses) + len(reasons) + 1
        assert result["missing"]["filtered_fields"] == 0
        assert result["rows"][-1]["fields"]["term_after"] == "legacy"
        await recorder.close()

    asyncio.run(run())


def test_event_hash_is_resalted_per_bot_mode_and_group(tmp_path):
    async def run():
        recorder = DiagnosticRecorder(tmp_path / "logs", TaskRegistry(), True)
        original = "1234abcd" * 8
        for bot, mode, group in (
            (BOT, "per_group", GROUP),
            (BOT, "per_group", OTHER_GROUP),
            (BOT, "global", GROUP),
            (OTHER_BOT, "per_group", GROUP),
        ):
            assert emit(recorder, bot, mode, group, event_id=original)
        results = [
            await recorder.export(BOT, "per_group"),
            await recorder.export(BOT, "global"),
            await recorder.export(OTHER_BOT, "per_group"),
        ]
        ids = [
            row["fields"]["event_id"] for result in results for row in result["rows"]
        ]
        assert len(set(ids)) == 4
        assert original not in json.dumps(results)
        selector = recorder._selector(BOT, "per_group")
        assert selector not in json.dumps(results)
        await recorder.close()

    asyncio.run(run())


def test_disabled_admin_can_export_and_clear_history_without_task(tmp_path):
    async def run():
        root = tmp_path / "logs"
        recorder = DiagnosticRecorder(root, TaskRegistry(), True)
        assert emit(recorder)
        assert emit(recorder, OTHER_BOT)
        await recorder.close()
        registry = TaskRegistry()
        disabled = DiagnosticRecorder(root, registry, False)
        result = await disabled.export(BOT, "per_group")
        assert len(result["rows"]) == 1
        assert result["stats"]["enabled"] is False
        assert result["stats"]["session"] is None
        assert result["stats"]["namespace"] is None
        stats = await disabled.clear(BOT, "per_group")
        assert stats["counters"]["cleared_rows"] == 1
        assert not (await disabled.export(BOT, "per_group"))["rows"]
        assert len((await disabled.export(OTHER_BOT, "per_group"))["rows"]) == 1
        assert registry.size == 0
        await disabled.close()
        empty = DiagnosticRecorder(tmp_path / "missing", registry, False)
        assert not (await empty.export(BOT, "per_group"))["rows"]
        await empty.clear(BOT, "per_group")
        assert not empty.root.exists()
        assert registry.size == 0

    asyncio.run(run())


def test_clear_deletes_selected_history_from_all_sessions(tmp_path):
    async def run():
        root = tmp_path / "logs"
        first = DiagnosticRecorder(root, TaskRegistry(), True)
        assert emit(first)
        assert emit(first, mode="global")
        await first.close()
        second = DiagnosticRecorder(root, TaskRegistry(), True)
        assert emit(second)
        assert emit(second, OTHER_BOT)
        assert len((await second.export(BOT, "per_group"))["rows"]) == 2
        result = await second.clear(BOT, "per_group")
        assert result["counters"]["cleared_rows"] == 2
        assert not (await second.export(BOT, "per_group"))["rows"]
        assert len((await second.export(BOT, "global"))["rows"]) == 1
        assert len((await second.export(OTHER_BOT, "per_group"))["rows"]) == 1
        await second.close()

    asyncio.run(run())


def test_global_quota_includes_files_from_previous_recorders(tmp_path):
    async def run():
        root = tmp_path / "logs"
        first = DiagnosticRecorder(root, TaskRegistry(), True)
        for index in range(5):
            assert emit(first, elapsed_ms=index)
            await first.flush()
        await first.close()
        assert sum(path.stat().st_size for path in root.iterdir()) > 1000
        second = DiagnosticRecorder(root, TaskRegistry(), True, max_bytes=1000)
        assert emit(second, OTHER_BOT)
        await second.close()
        assert sum(path.stat().st_size for path in root.iterdir()) <= 1000
        assert second.stats()["missing"]["evicted_rows"] >= 1

    asyncio.run(run())


def test_export_rows_are_bounded_and_truncation_is_reported(tmp_path):
    async def run():
        recorder = DiagnosticRecorder(tmp_path / "logs", TaskRegistry(), True)
        for batch in range(33):
            for index in range(128):
                assert emit(recorder, elapsed_ms=batch * 128 + index)
            await recorder.flush()
        await recorder.close()
        result = await recorder.export(BOT, "per_group")
        assert len(result["rows"]) == 4096
        assert result["missing"]["export_truncated"] == 33 * 128 - 4096
        assert result["stats"]["gap"]
        assert result["rows"][-1]["fields"]["elapsed_ms"] == 33 * 128 - 1
        assert all(
            len(line) <= recorder.MAX_LINE
            for path in recorder.root.iterdir()
            for line in path.read_bytes().splitlines(keepends=True)
        )

    asyncio.run(run())


def test_events_emitted_during_clear_are_new_and_survive(tmp_path, monkeypatch):
    async def run():
        registry = TaskRegistry()
        recorder = DiagnosticRecorder(tmp_path / "logs", registry, True)
        started, release = threading.Event(), threading.Event()
        erase = recorder._erase

        def delayed(selector):
            started.set()
            release.wait(5)
            return erase(selector)

        assert emit(recorder, elapsed_ms=1)
        monkeypatch.setattr(recorder, "_erase", delayed)
        clearing = registry.spawn(recorder.clear(BOT, "per_group"), "test-clear")
        for _ in range(500):
            if started.is_set():
                break
            await asyncio.sleep(0.002)
        assert started.is_set()
        assert emit(recorder, elapsed_ms=2)
        release.set()
        await asyncio.wait_for(clearing, 5)
        await recorder.close()
        rows = (await recorder.export(BOT, "per_group"))["rows"]
        assert [row["fields"]["elapsed_ms"] for row in rows] == [2]

    asyncio.run(run())


def test_write_exception_text_never_escapes(tmp_path, monkeypatch):
    async def run():
        recorder = DiagnosticRecorder(tmp_path / "logs", TaskRegistry(), True)
        original = Path.open

        def failing(path, mode="r", *args, **kwargs):
            if mode == "xb":
                raise OSError(TEXT)
            return original(path, mode, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "open", failing)
            assert emit(recorder)
            await recorder.flush()
        result = await recorder.export(BOT, "per_group")
        assert result["rows"] == []
        assert result["missing"]["write_errors"] == 1
        assert result["missing"]["dropped"] == 1
        assert TEXT not in json.dumps(result)
        await recorder.close()

    asyncio.run(run())


def test_path_link_guard_without_windows_symlink_privilege(tmp_path, monkeypatch):
    async def run():
        recorder = DiagnosticRecorder(
            tmp_path / "linked" / "logs", TaskRegistry(), True
        )
        original = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda path: path == recorder.root.parent or original(path),
        )
        assert emit(recorder)
        await recorder.close()
        assert recorder.stats()["missing"]["write_errors"] == 1
        assert not recorder.root.exists()

    asyncio.run(run())
