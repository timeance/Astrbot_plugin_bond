"""分级诊断；一个专用根目录、一个事件循环、一个 writer。

路径中的 Bot/模式摘要仅用于历史定位，不导出。行内身份使用每次记录会话
的随机 HMAC 盐；详细内容仅由观察点显式提供。统计不持久化，历史缺口明确为未知。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import Counter, deque
from pathlib import Path


class DiagnosticRecorder:
    VERSION = 2
    MAX_BYTES, MAX_LINE, MAX_FILES, MAX_NAMESPACES = 10_000_000_000, 196608, 65536, 128
    MAX_QUEUE_BYTES, MAX_EXPORT_BYTES = 8_000_000, 16_000_000
    CONTENT_BYTES = 128_000
    MAINTENANCE_SECONDS = 3600
    LEVELS = ("assessment", "raw", "evaluation", "full")
    STAGES = {"chat": 1, "response": 1, "evaluation": 2, "request": 3}
    SEGMENT_BYTES, MAX_EXPORT_ROWS = 256 * 1024, 32768
    MODES = ("per_group", "global")
    KINDS = frozenset("assessment call relation render entry content".split())
    ENUMS = {
        "status": frozenset(
            "queued started ok success accepted skipped failed rejected disabled cancelled applied unchanged invalid timeout abstained temporary busy filtered duplicate_or_cooldown committed duplicate cooldown injected budget".split()
        ),
        "reason": frozenset(
            "none unknown disabled cooldown duplicate duplicate_or_cooldown budget capacity timeout invalid error unavailable filtered no_change no_target not_directed insufficient_evidence policy storage queue_full shutdown temporary busy abstained accepted unchanged success failed enabled rendered injected empty legacy missing not_group not_allowed entry automatic manual undo reset temporary_end".split()
        ),
        "event_type": frozenset(
            "normal_question thanks friendly joke criticism insult manipulation apology uncertain".split()
        ),
        "tier": frozenset(("small", "large")),
        "maintenance_strategy": frozenset(("independent", "legacy")),
    }
    PATTERNS = {
        "event_id": r"[0-9a-f]{16,128}",
        "trace_id": r"[0-9a-f]{16,128}",
        "term_before": r"(?:r[0-9]{2}|legacy)",
        "term_after": r"(?:r[0-9]{2}|legacy)",
        "policy_version": r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}",
        "lexicon_version": r"[0-9]{1,6}",
        "config_hash": r"[0-9a-f]{64}",
    }
    DIMENSIONS = frozenset(("affinity", "familiarity", "trust"))
    FIELDS = (
        tuple(ENUMS)
        + tuple(PATTERNS)
        + tuple(
            "elapsed_ms tokens confidence before after delta bands_before bands_after temporary intent adjusted allowed maintenance_at maintenance_policy_at maintenance_before maintenance_after maintenance_qualified maintenance_applied maintenance_reason".split()
        )
    )
    CODES = tuple(
        "accepted written dropped queue_full invalid filtered_fields write_errors read_errors expired_rows evicted_rows cleared_rows corrupt_lines export_truncated namespace_limit closed".split()
    )
    GAPS = tuple(
        "dropped filtered_fields write_errors read_errors expired_rows evicted_rows corrupt_lines export_truncated".split()
    )
    ROW_KEYS = frozenset(
        "version session namespace mode partition member ts kind fields".split()
    )
    FILE = re.compile(r"([0-9a-f]{16})_([0-9a-f]{32})_([0-9a-f]{16})\.jsonl")

    def __init__(
        self,
        root: Path,
        registry,
        enabled=False,
        *,
        max_bytes=100_000_000,
        detail_level="assessment",
        retention_days=7,
        clock=time.time,
        queue_limit=256,
    ):
        self.root, self.registry = Path(os.path.abspath(root)), registry
        self.enabled, self._closed = bool(enabled), False
        self.detail_level = (
            detail_level if detail_level in self.LEVELS else "assessment"
        )
        self._generations = {}
        self._maintenance_task = None
        self._maintenance_stop = asyncio.Event()
        self.max_bytes = max(1, min(int(max_bytes), self.MAX_BYTES))
        self.retention = max(0, min(float(retention_days), 7)) * 86400
        self.queue_limit = max(1, min(int(queue_limit), 256))
        self.clock = clock
        self._salt = secrets.token_bytes(32) if enabled else b""
        self.session = secrets.token_hex(8) if enabled else None
        self._queue, self._counts = deque(), {}
        self._total, self._unattributed = Counter(), Counter()
        self._lock, self._wake = asyncio.Lock(), asyncio.Event()
        self._task, self._loop, self._sequence = None, None, 0

    @staticmethod
    def _identity(value):
        if type(value) is int and value.bit_length() <= 128:
            value = str(value)
        if type(value) is not str or not value or len(value) > 96:
            raise ValueError("无效诊断身份")
        return value

    def _selector(self, bot_id, mode):
        if mode not in self.MODES:
            raise ValueError("无效诊断作用域")
        raw = json.dumps(("bond-diagnostics", self._identity(bot_id), mode)).encode()
        return hashlib.sha256(raw).hexdigest()[:32]

    def _tag(self, *parts):
        return hmac.new(
            self._salt, json.dumps(parts).encode(), hashlib.sha256
        ).hexdigest()[:32]

    @staticmethod
    def _number(value, low=-(10**12), high=10**12):
        return type(value) in (int, float) and low <= value <= high

    @classmethod
    def _fields(cls, fields):
        clean = {}
        for key in cls.FIELDS:
            if key not in fields or fields[key] is None:
                continue
            value = fields[key]
            if key in cls.ENUMS:
                valid = type(value) is str and value in cls.ENUMS[key]
            elif key in cls.PATTERNS:
                valid = (
                    type(value) is str
                    and re.fullmatch(cls.PATTERNS[key], value) is not None
                )
            elif key in (
                "maintenance_qualified",
                "maintenance_applied",
                "maintenance_reason",
            ):
                reasons = {
                    "manual",
                    "original_zero",
                    "rule_zero",
                    "quota",
                    "eligible",
                    "precision",
                    "bound",
                    "legacy",
                    "duplicate",
                    "cooldown",
                }
                valid = (
                    type(value) is dict
                    and set(value) == cls.DIMENSIONS
                    and all(
                        (
                            type(v) is bool
                            if key != "maintenance_reason"
                            else type(v) is str and v in reasons
                        )
                        for v in value.values()
                    )
                )
            elif key in ("maintenance_at", "maintenance_policy_at"):
                valid = cls._number(value, 0, 10**12)
            elif key in (
                "before",
                "after",
                "delta",
                "intent",
                "adjusted",
                "allowed",
                "maintenance_before",
                "maintenance_after",
            ):
                valid = (
                    type(value) is dict
                    and len(value) <= 3
                    and all(
                        type(k) is str and k in cls.DIMENSIONS and cls._number(v)
                        for k, v in value.items()
                    )
                )
            elif key in ("bands_before", "bands_after"):
                valid = (
                    type(value) in (list, tuple)
                    and len(value) == 3
                    and all(type(v) is int and 0 <= v <= 4 for v in value)
                )
            elif key == "temporary":
                valid = type(value) is bool
            elif key == "tokens":
                valid = type(value) is int and 0 <= value <= 10**9
            else:
                valid = cls._number(value, 0, 1 if key == "confidence" else 10**9)
            if valid:
                clean[key] = (
                    dict(value)
                    if type(value) is dict
                    else (list(value) if type(value) in (tuple, list) else value)
                )
        return clean

    def _note(self, selector, code, amount=1):
        target = (
            self._counts[selector][1]
            if selector in self._counts
            else self._unattributed
        )
        for counter in (self._total, target):
            counter[code] = min(2**63 - 1, counter.get(code, 0) + amount)

    def _merge(self, notes):
        for (selector, code), amount in notes.items():
            self._note(selector, code, amount)

    def _reject(self, selector, code):
        self._note(selector, code)
        self._note(selector, "dropped")
        return False

    def note_failure(self, bot_id, mode):
        """观察点准备失败只记缺口，不保存异常或不确定身份。"""
        if not self.enabled:
            return
        try:
            selector = self._selector(bot_id, mode)
        except Exception:
            selector = None
        self._reject(selector, "invalid")

    def emit(
        self,
        bot_id,
        scope_mode,
        group_id,
        user_id,
        kind,
        *,
        generation=None,
        _content=None,
        **fields,
    ):
        """同步无磁盘 IO；身份来自可信事件，返回是否入队。"""
        if not self.enabled:
            return False
        selector = None
        try:
            selector = self._selector(bot_id, scope_mode)
            if generation is not None and generation != self._generations.get(
                selector, 0
            ):
                return False
            if self._closed:
                return self._reject(selector, "closed")
            if selector not in self._counts:
                if len(self._counts) >= self.MAX_NAMESPACES:
                    return self._reject(None, "namespace_limit")
                self._counts[selector] = (scope_mode, Counter())
            if len(self._queue) >= self.queue_limit:
                return self._reject(selector, "queue_full")
            group, user = self._identity(group_id), self._identity(user_id)
            if type(kind) is not str or kind not in self.KINDS:
                raise ValueError("无效诊断事件")
            clean = self._fields(fields)
            self._note(
                selector,
                "filtered_fields",
                sum(v is not None for v in fields.values()) - len(clean),
            )
            now = self.clock()
            if not self._number(now, 0):
                raise ValueError("无效诊断时间")
            namespace = self._tag("namespace", selector)
            partition = self._tag("partition", namespace, group)
            for key in ("event_id", "trace_id"):
                if key in clean:
                    clean[key] = self._tag("event", partition, clean[key])
            row = dict(
                version=self.VERSION,
                session=self.session,
                namespace=namespace,
                mode=scope_mode,
                partition=partition,
                member=self._tag("member", partition, user),
                ts=now,
                kind=kind,
                fields=clean,
            )
            if _content is not None:
                row["content"] = _content
            line = (
                json.dumps(
                    row, separators=(",", ":"), allow_nan=False, ensure_ascii=False
                )
                + "\n"
            ).encode()
            if len(line) > min(self.MAX_LINE, self.max_bytes):
                raise ValueError("诊断行过大")
            if (
                sum(len(item[1]) for item in self._queue) + len(line)
                > self.MAX_QUEUE_BYTES
            ):
                return self._reject(selector, "queue_full")
            loop = asyncio.get_running_loop()
            if self._loop is not None and loop is not self._loop:
                raise ValueError("诊断事件循环不同")
            if self._task is None or self._task.done():
                coro = self._writer()
                try:
                    self._task = self.registry.spawn(coro, name="bond-diagnostics")
                except Exception:
                    coro.close()
                    raise
                self._loop = loop
            self._queue.append((selector, line))
            self._note(selector, "accepted")
            self._wake.set()
            return True
        except Exception:
            return self._reject(selector, "invalid")

    def capture_token(self, bot_id, mode):
        return self._generations.get(self._selector(bot_id, mode), 0)

    def wants(self, stage):
        return (
            self.enabled
            and not self._closed
            and self.LEVELS.index(self.detail_level) >= self.STAGES.get(stage, 99)
        )

    @classmethod
    def _content_snapshot(cls, content):
        """只复制有界 JSON 数据，不调用任意对象的字符串化方法。"""
        remaining = cls.CONTENT_BYTES
        truncated = False
        omitted = False
        denied = re.compile(
            r"(?:api.?key|authorization|credential|password|secret|access.?token|refresh.?token|cookie|base64|image.?url|audio|video|file.?path|local.?path)",
            re.I,
        )
        credential = re.compile(
            r"(?i)(\b(?:Bearer)\s+)[A-Za-z0-9._~+/=-]+|\bsk-[A-Za-z0-9_-]{12,}|(\b(?:api[_-]?key|password|access_token|refresh_token)\s*[:=]\s*)[^\s,;]+"
        )

        # 工具结果常把 JSON 对象放在 content 字符串中；保留字段和合法 JSON 引号。
        quoted_credential = re.compile(
            r'(?i)("(?:api.?key|authorization|credential|password|secret|access.?token|refresh.?token|cookie)"\s*:\s*)"(?:\\.|[^"\\])*"'
        )

        def redact_json(value):
            nonlocal truncated, omitted
            if not value.lstrip().startswith(("{", "[")):
                return value
            # 工具内容本身是 JSON 字符串；限制解析大小、深度与节点数。
            nodes = 4096

            def redact(item, depth=0):
                nonlocal nodes
                nodes -= 1
                if nodes < 0 or depth > 8:
                    raise ValueError("JSON 内容过大")
                if type(item) is dict:
                    return {
                        key: (
                            "[redacted]"
                            if denied.search(key)
                            else redact(child, depth + 1)
                        )
                        for key, child in item.items()
                    }
                if type(item) is list:
                    return [redact(child, depth + 1) for child in item]
                return item

            try:
                if len(value) > 32768:
                    raise ValueError("JSON 内容过大")
                parsed = json.loads(value)
                cleaned = redact(parsed)
                if cleaned != parsed:
                    omitted = True
                    return json.dumps(cleaned, ensure_ascii=False, allow_nan=False)
                return value
            except (ValueError, RecursionError):
                # 解析失败或超限时整段省略，避免部分 JSON 漏出敏感字段。
                truncated = omitted = True
                return "[JSON content omitted]"

        def copy(value, depth=0):
            nonlocal remaining, truncated, omitted
            if remaining <= 0 or depth > 8:
                truncated = True
                remaining -= 8
                return None
            if type(value) is str:
                value = quoted_credential.sub(
                    lambda match: match[1] + '"[redacted]"', value
                )
                value = credential.sub("[redacted]", value)
                value = re.sub(r"data:[^\s]+", "[media omitted]", value)
                value = re.sub(
                    r"https?://[^\s<>\"]+",
                    lambda m: m[0].split("?")[0].split("#")[0] if "?" in m[0] else m[0],
                    value,
                )
                json_value = value.lstrip().startswith(("{", "["))
                value = redact_json(value)
                size = min(32768, remaining)
                if (
                    json_value
                    and len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
                    > size
                ):
                    truncated = omitted = True
                    value = "[JSON content omitted]"
                raw = value.encode("utf-8")
                if len(raw) > size:
                    truncated = True
                    value = raw[:size].decode("utf-8", errors="ignore")
                encoded = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
                while encoded > size and value:
                    truncated = True
                    value = value[: max(0, len(value) * size // encoded - 1)]
                    encoded = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
                remaining -= encoded + 16
                return value
            if value is None or type(value) is bool:
                remaining -= 8
                return value
            if type(value) in (int, float):
                if cls._number(value):
                    remaining -= 32
                    return value
                omitted = True
                # 替代值与再次检查时的 null 使用相同预算，避免导出重新截断。
                remaining -= 8
                return None
            if type(value) is dict:
                remaining -= 16
                result = {}
                for index, (key, item) in enumerate(value.items()):
                    if index >= 128 or remaining <= 0:
                        truncated = True
                        break
                    if type(key) is not str or len(key) > 128 or denied.search(key):
                        omitted = True
                        continue
                    remaining -= (
                        len(json.dumps(key, ensure_ascii=False).encode("utf-8")) + 8
                    )
                    result[key] = copy(item, depth + 1)
                return result
            if type(value) in (list, tuple):
                remaining -= 16
                result = []
                for index, item in enumerate(value):
                    if index >= 128 or remaining <= 0:
                        truncated = True
                        break
                    remaining -= 8
                    result.append(copy(item, depth + 1))
                return result
            omitted = True
            remaining -= 8
            return None

        payload = copy(content)
        return dict(data=payload, truncated=truncated, omitted=omitted)

    def emit_content(
        self,
        bot_id,
        mode,
        group_id,
        user_id,
        *,
        trace_id,
        stage,
        content,
        generation=None,
    ):
        if not self.wants(stage):
            return False
        try:
            snapshot = self._content_snapshot(content)
            snapshot["stage"] = stage
            return self.emit(
                bot_id,
                mode,
                group_id,
                user_id,
                "content",
                trace_id=trace_id,
                generation=generation,
                _content=snapshot,
            )
        except Exception:
            self.note_failure(bot_id, mode)
            return False

    async def start(self):
        """启动保留期限清理；关闭采集时仍清理已有诊断。"""
        if self._closed or self._maintenance_task is not None:
            return
        await self.maintain()
        self._maintenance_task = self.registry.spawn(
            self._maintenance(), name="bond-diagnostics-retention"
        )

    async def maintain(self):
        async with self._lock:
            try:
                self._merge(await self._disk(self._prune))
            except Exception:
                self._note(None, "write_errors")

    async def _maintenance(self):
        while not self._closed:
            try:
                await asyncio.wait_for(
                    self._maintenance_stop.wait(), timeout=self.MAINTENANCE_SECONDS
                )
                break
            except asyncio.TimeoutError:
                await self.maintain()

    async def _writer(self):
        while not self._closed or self._queue:
            await self._wake.wait()
            self._wake.clear()
            await self.flush()

    async def _disk(self, function, *args):
        # 即使重复取消，也须等待线程结束再释放锁，防止 clear 被旧批次回填。
        future = asyncio.get_running_loop().run_in_executor(None, function, *args)
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(future)
                break
            except asyncio.CancelledError:
                if future.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def flush(self):
        if not self.enabled:
            return
        async with self._lock:
            batch = list(self._queue)
            self._queue.clear()
            if batch:
                try:
                    self._merge(await self._disk(self._persist, batch))
                except (Exception, asyncio.CancelledError) as error:
                    for selector, count in Counter(ns for ns, _ in batch).items():
                        self._note(selector, "dropped", count)
                        self._note(selector, "write_errors")
                    if isinstance(error, asyncio.CancelledError):
                        raise

    def _files(self, create=False):
        for path in (self.root, *self.root.parents):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError("诊断路径包含链接")
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.exists():
            return []
        files = []
        for path in self.root.iterdir():
            match = self.FILE.fullmatch(path.name)
            if not match or path.is_symlink() or not path.is_file():
                raise ValueError("诊断根目录须专用且无链接")
            info = path.stat()
            if info.st_size > self.SEGMENT_BYTES or len(files) >= self.MAX_FILES:
                raise ValueError("诊断文件超限")
            files.append(
                (
                    info.st_mtime,
                    int(match[3], 16),
                    path,
                    info.st_size,
                    match[1],
                    match[2],
                )
            )
        return sorted(files)

    def _remove(self, file, code, notes):
        path, selector = file[2], file[5]
        count = path.read_bytes().count(b"\n")
        path.unlink()
        notes[(selector, code)] += count

    def _prune(self, reserve=0, *, new_file=True):
        notes, files = Counter(), self._files(create=bool(reserve))
        total, remaining = sum(f[3] for f in files), len(files)
        for file in files:
            expired = file[0] <= self.clock() - self.retention
            if (
                expired
                or total + reserve > self.max_bytes
                or (reserve and new_file and remaining >= self.MAX_FILES)
            ):
                self._remove(file, "expired_rows" if expired else "evicted_rows", notes)
                total -= file[3]
                remaining -= 1
        return notes

    def _persist(self, batch):
        notes, groups = Counter(), {}
        for selector, line in batch:
            day = int(json.loads(line)["ts"] // 86400)
            groups.setdefault((selector, day), []).append(line)
        for (selector, day), lines in groups.items():
            chunk, size = [], 0
            for line in lines:
                if size + len(line) > min(self.SEGMENT_BYTES, self.max_bytes):
                    self._segment(selector, day, chunk, notes)
                    chunk, size = [], 0
                chunk.append(line)
                size += len(line)
            if chunk:
                self._segment(selector, day, chunk, notes)
        return notes

    def _segment(self, selector, day, lines, notes):
        try:
            payload = b"".join(lines)
            files = self._files(create=True)
            candidates = [
                f
                for f in files
                if f[4] == self.session
                and f[5] == selector
                and int(f[0] // 86400) == day
            ]
            target = max(candidates, key=lambda f: f[1], default=None)
            if target and target[3] + len(payload) > min(
                self.SEGMENT_BYTES, self.max_bytes
            ):
                target = None
            notes.update(self._prune(len(payload), new_file=target is None))
            append = target is not None and target[2].exists()
            if append:
                path, size, stamp = target[2], target[3], target[0]
            else:
                self._sequence += 1
                path = (
                    self.root / f"{self.session}_{selector}_{self._sequence:016x}.jsonl"
                )
                size, stamp = 0, json.loads(lines[0])["ts"]
            with path.open("r+b" if append else "xb") as stream:
                stream.seek(size)
                try:
                    if stream.write(payload) != len(payload):
                        raise OSError("诊断写入不完整")
                    stream.flush()
                    # 追加不得刷新保留期限或改变按片段起始时间淘汰的顺序。
                    os.utime(path, (stamp, stamp))
                except Exception:
                    stream.seek(size)
                    stream.truncate()
                    stream.flush()
                    os.utime(path, (stamp, stamp))
                    raise
            notes[(selector, "written")] += len(lines)
        except Exception:
            notes[(selector, "dropped")] += len(lines)
            notes[(selector, "write_errors")] += 1

    async def close(self):
        self._closed = True
        self._maintenance_stop.set()
        if self._maintenance_task is not None:
            await asyncio.gather(self._maintenance_task, return_exceptions=True)
        if self.enabled:
            self._wake.set()
            await self.flush()
            if self._task is not None:
                await asyncio.gather(self._task, return_exceptions=True)
                await asyncio.sleep(0)

    def stats(self, bot_id=None, mode=None):
        selector = (
            self._selector(bot_id, mode)
            if self.enabled and bot_id is not None and mode is not None
            else None
        )
        selected, counter = set(), Counter()
        for ns, (scope, counts) in self._counts.items():
            if (mode is None or scope == mode) and (
                bot_id is None or ns == self._selector(bot_id, scope)
            ):
                selected.add(ns)
                counter.update(counts)
        if bot_id is None and mode is None:
            counter = self._total
        missing = {key: min(2**63 - 1, counter[key]) for key in self.GAPS}
        missing["unattributed"] = min(
            2**63 - 1, sum(self._unattributed[k] for k in self.GAPS)
        )
        gap = any(missing.values())
        missing["historical_counters_unknown"] = True
        return dict(
            enabled=self.enabled,
            closed=self._closed,
            version=self.VERSION,
            detail_level=self.detail_level,
            max_bytes=self.max_bytes,
            session=self.session,
            namespace=(
                self._tag("namespace", selector) if selector and self.session else None
            ),
            counters_scope="current_session",
            counters={key: min(2**63 - 1, counter[key]) for key in self.CODES},
            missing=missing,
            gap=gap,
            queued=sum(1 for ns, _ in self._queue if ns in selected),
            tracked_namespaces=len(self._counts),
            namespace_limit=self.MAX_NAMESPACES,
        )

    def _sizes(self, selector):
        files = self._files()
        return (sum(f[3] for f in files if f[5] == selector), sum(f[3] for f in files))

    async def status(self, bot_id, mode):
        result = self.stats(bot_id, mode)
        selector = self._selector(bot_id, mode)
        result.update(
            disk_bytes=None,
            total_disk_bytes=None,
            max_bytes=self.max_bytes,
            detail_level=self.detail_level,
            size_available=False,
            queued_bytes=sum(len(line) for ns, line in self._queue if ns == selector),
            total_queued_bytes=sum(len(line) for _, line in self._queue),
        )
        async with self._lock:
            try:
                scoped, total = await self._disk(self._sizes, selector)
                result.update(
                    disk_bytes=scoped, total_disk_bytes=total, size_available=True
                )
            except Exception:
                pass
        return result

    def _valid_row(self, row, session, mode):
        if type(row) is not dict:
            return False
        expected = self.ROW_KEYS | (
            {"content"} if row.get("kind") == "content" else set()
        )
        if set(row) != expected:
            return False
        if row.get("kind") == "content":
            content = row.get("content")
            if (
                row.get("version") != 2
                or type(content) is not dict
                or set(content) != {"stage", "data", "truncated", "omitted"}
            ):
                return False
            if (
                content["stage"] not in self.STAGES
                or type(content["truncated"]) is not bool
                or type(content["omitted"]) is not bool
            ):
                return False
            # 已有快照重新过白名单，拒绝被外部篡改的凭据或对象结构。
            if self._content_snapshot(content["data"])["data"] != content["data"]:
                return False
        return (
            row["version"] in (1, self.VERSION)
            and row["session"] == session
            and row["mode"] == mode
            and type(row["kind"]) is str
            and row["kind"] in self.KINDS
            and self._number(row["ts"], 0)
            and all(
                type(row[k]) is str and re.fullmatch(r"[0-9a-f]{32}", row[k])
                for k in ("namespace", "partition", "member")
            )
            and type(row["fields"]) is dict
            and self._fields(row["fields"]) == row["fields"]
        )

    def _read(self, selector, mode):
        notes, rows = self._prune(), deque(maxlen=self.MAX_EXPORT_ROWS)
        sizes, total_bytes = deque(maxlen=self.MAX_EXPORT_ROWS), 0
        for file in self._files():
            if file[5] != selector:
                continue
            for line in file[2].read_bytes().splitlines():
                try:
                    if len(line) > self.MAX_LINE:
                        raise ValueError("诊断行过大")
                    row = json.loads(line)
                    if not self._valid_row(row, file[4], mode):
                        raise ValueError("无效诊断行")
                    while rows and (
                        len(rows) == rows.maxlen
                        or total_bytes + len(line) > self.MAX_EXPORT_BYTES
                    ):
                        rows.popleft()
                        total_bytes -= sizes.popleft()
                        notes[(selector, "export_truncated")] += 1
                    rows.append(row)
                    sizes.append(len(line))
                    total_bytes += len(line)
                except (ValueError, TypeError, OverflowError, RecursionError):
                    notes[(selector, "corrupt_lines")] += 1
        return list(rows), notes

    async def export(self, bot_id, mode):
        """显式管理读取即使关闭也可用；不启用记录或启动任务。"""
        selector = self._selector(bot_id, mode)
        if len(self._counts) < self.MAX_NAMESPACES:
            self._counts.setdefault(selector, (mode, Counter()))
        await self.flush()
        rows = []
        async with self._lock:
            try:
                rows, notes = await self._disk(self._read, selector, mode)
                self._merge(notes)
            except Exception:
                self._note(selector, "read_errors")
        stats = await self.status(bot_id, mode)
        return dict(rows=rows, stats=stats, missing=stats["missing"])

    def _erase(self, selector):
        notes = Counter()
        for file in self._files():
            if file[5] == selector:
                self._remove(file, "cleared_rows", notes)
        return notes

    async def clear(self, bot_id, mode):
        """删除选中分区的保留历史及清除线性化点之前的排队行。"""
        selector = self._selector(bot_id, mode)
        if len(self._counts) < self.MAX_NAMESPACES:
            self._counts.setdefault(selector, (mode, Counter()))
        succeeded = True
        async with self._lock:
            self._generations[selector] = self._generations.get(selector, 0) + 1
            pending = sum(1 for ns, _ in self._queue if ns == selector)
            self._queue = deque(
                (ns, line) for ns, line in self._queue if ns != selector
            )
            self._note(selector, "cleared_rows", pending)
            try:
                self._merge(await self._disk(self._erase, selector))
            except Exception:
                succeeded = False
                self._note(selector, "write_errors")
        return dict(await self.status(bot_id, mode), clear_succeeded=succeeded)
