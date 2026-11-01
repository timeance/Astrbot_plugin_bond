"""按 Bot 与关系模式路由 SQLite 存储。"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from ..core.scope import account_id, scope_mode as validate_scope_mode
from .migrations import SCHEMA_VERSION
from .sqlite_backend import SQLiteBackend

_DB_NAMES = {"per_group": "group.db", "global": "global.db"}
_BACKUP_NAME = re.compile(r"(?:per_group|global)-[0-9]+-[0-9a-f]{32}\.db\Z")
_REQUIRED_COLUMNS = {
    "favor": {
        "group_id",
        "user_id",
        "favor",
        "updated_at",
        "relationship",
        "nickname",
        "impression",
        "tags",
        "impression_at",
        "half_life",
        "points",
        "familiarity",
        "trust",
    },
    "daily_gain": {
        "group_id",
        "user_id",
        "day",
        "gain",
        "familiarity_gain",
        "trust_gain",
    },
    "cooldown": {"group_id", "user_id", "key", "last_ts"},
    "favor_log": {
        "id",
        "group_id",
        "user_id",
        "delta",
        "favor_before",
        "favor_after",
        "reason",
        "source",
        "ts",
        "message",
        "reversed",
        "relation_delta",
        "event_id",
    },
    "impression_progress": {"group_id", "user_id", "log_id"},
    "observed_members": {"group_id", "user_id", "last_seen_at"},
}
_TABLE_INFO_SQL = {
    "favor": "PRAGMA table_info(favor)",
    "daily_gain": "PRAGMA table_info(daily_gain)",
    "cooldown": "PRAGMA table_info(cooldown)",
    "favor_log": "PRAGMA table_info(favor_log)",
    "impression_progress": "PRAGMA table_info(impression_progress)",
    "observed_members": "PRAGMA table_info(observed_members)",
}


class BotScopeStorageRouter:
    """为每个 Bot 建立并缓存独立的 group/global 两个后端。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve(strict=False)
        if self._root.exists() and not self._root.is_dir():
            raise ValueError("存储根目录必须是目录")
        self._lock = threading.RLock()
        self._backends: dict[tuple[str, str], SQLiteBackend] = {}
        self._closed = False

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("storage router 已关闭")

    @staticmethod
    def _is_path_alias(path: Path) -> bool:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(callable(is_junction) and is_junction())

    def _reject_path_alias(self, path: Path) -> None:
        if self._is_path_alias(path):
            raise ValueError("存储路径不允许符号链接或目录联接别名")

    def _inside_root(self, path: Path) -> Path:
        resolved = Path(path).resolve(strict=False)
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise ValueError("存储路径超出 root") from exc
        return resolved

    def _ensure_dir(self, path: Path) -> Path:
        if path != self._root:
            self._reject_path_alias(path)
        resolved = self._inside_root(path)
        if not resolved.exists():
            resolved.mkdir(parents=True, exist_ok=True)
        if not resolved.is_dir():
            raise ValueError(f"存储路径不是目录: {resolved.name}")
        if path != self._root:
            self._reject_path_alias(path)
        return self._inside_root(resolved)

    def _existing_dir(self, path: Path) -> Path:
        self._reject_path_alias(path)
        resolved = self._inside_root(path)
        if not resolved.is_dir():
            raise FileNotFoundError(f"目录不存在: {resolved.name}")
        self._reject_path_alias(path)
        return resolved

    def _bot_db_paths(self, bot_id: str) -> tuple[Path, Path]:
        root = self._ensure_dir(self._root)
        bots = self._ensure_dir(root / "bots")
        bot_dir = self._ensure_dir(bots / bot_id)
        group_path = bot_dir / _DB_NAMES["per_group"]
        global_path = bot_dir / _DB_NAMES["global"]
        self._reject_path_alias(group_path)
        self._reject_path_alias(global_path)
        return (
            self._inside_root(group_path),
            self._inside_root(global_path),
        )

    def _new_backup_path(self, bot_id: str, mode: str) -> Path:
        root = self._ensure_dir(self._root)
        backups = self._ensure_dir(root / "backups")
        bot_dir = self._ensure_dir(backups / bot_id)
        while True:
            name = f"{mode}-{time.time_ns()}-{uuid.uuid4().hex}.db"
            path = self._inside_root(bot_dir / name)
            if not path.exists() and not path.is_symlink():
                return path

    def _backup_path(self, bot_id: str, mode: str, backup_name: str) -> Path:
        if not isinstance(backup_name, str) or not backup_name.isascii():
            raise ValueError("backup_name 无效")
        if not _BACKUP_NAME.fullmatch(backup_name):
            raise ValueError("backup_name 不是 router 生成的备份名")
        if not backup_name.startswith(f"{mode}-"):
            raise ValueError("备份不属于当前 scope")
        backups = self._existing_dir(self._root / "backups")
        bot_dir = self._existing_dir(backups / bot_id)
        raw = bot_dir / backup_name
        self._reject_path_alias(raw)
        path = self._inside_root(raw)
        if not path.is_file():
            raise FileNotFoundError(f"备份不存在: {backup_name}")
        return path

    @staticmethod
    def _validate_backup_schema(path: Path) -> None:
        try:
            conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise ValueError("备份不是可读的 SQLite 数据库") from exc
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise ValueError("备份 integrity_check 失败")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise ValueError(f"不支持的未来 schema 版本: {version}")
            if version != SCHEMA_VERSION:
                raise ValueError(f"备份 schema 版本不匹配: {version}")
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table, required in _REQUIRED_COLUMNS.items():
                if table not in tables:
                    raise ValueError(f"备份缺少表: {table}")
                columns = {row[1] for row in conn.execute(_TABLE_INFO_SQL[table])}
                if not required.issubset(columns):
                    raise ValueError(f"备份表结构不完整: {table}")
        except sqlite3.Error as exc:
            raise ValueError("备份 schema 校验失败") from exc
        finally:
            conn.close()

    async def get(self, bot_id: str, scope_mode: str) -> SQLiteBackend:
        bot_id = account_id(bot_id)
        mode = validate_scope_mode(scope_mode)
        with self._lock:
            self._assert_open()
            key = (bot_id, mode)
            cached = self._backends.get(key)
            if cached is not None:
                return cached

            group_path, global_path = self._bot_db_paths(bot_id)
            group_backend = SQLiteBackend(group_path)
            global_backend = SQLiteBackend(global_path)
            try:
                await group_backend.init()
                await global_backend.init()
            except Exception:
                await group_backend.close()
                await global_backend.close()
                raise
            self._backends[(bot_id, "per_group")] = group_backend
            self._backends[(bot_id, "global")] = global_backend
            return self._backends[key]

    def known_bot_ids(self) -> list[str]:
        with self._lock:
            if self._closed or not self._root.exists():
                return []
            bots_path = self._root / "bots"
            self._reject_path_alias(bots_path)
            bots = self._inside_root(bots_path)
            if not bots.exists():
                return []
            if not bots.is_dir():
                raise ValueError("bots 路径不是目录")
            ids: set[str] = set()
            for bot_id, _mode in self._backends:
                bot_dir = bots / bot_id
                try:
                    self._reject_path_alias(bot_dir)
                    resolved = self._inside_root(bot_dir)
                except ValueError:
                    continue
                if resolved.is_dir():
                    ids.add(bot_id)
            for entry in bots.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    self._reject_path_alias(entry)
                    bot_id = account_id(entry.name)
                    resolved = self._inside_root(entry)
                except ValueError:
                    continue
                if resolved.is_dir():
                    ids.add(bot_id)
            return sorted(ids)

    async def backup(self, bot_id: str, scope_mode: str) -> Path:
        bot_id = account_id(bot_id)
        mode = validate_scope_mode(scope_mode)
        with self._lock:
            self._assert_open()
            backend = await self.get(bot_id, mode)
            destination = self._new_backup_path(bot_id, mode)
            try:
                await backend._backup_to(destination)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            return destination

    async def restore(self, bot_id: str, scope_mode: str, backup_name: str) -> None:
        bot_id = account_id(bot_id)
        mode = validate_scope_mode(scope_mode)
        with self._lock:
            self._assert_open()
            source = self._backup_path(bot_id, mode, backup_name)
            self._validate_backup_schema(source)
            backend = await self.get(bot_id, mode)
            # 这个快照保留在固定备份目录中；替换过程另在数据库目录保留临时旧文件用于回滚。
            await self.backup(bot_id, mode)
            await backend._restore_from(source)

    async def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            backends = list(
                {id(backend): backend for backend in self._backends.values()}.values()
            )
            self._backends.clear()
            first_error: Exception | None = None
            for backend in backends:
                try:
                    await backend.close()
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
            if first_error is not None:
                raise first_error
