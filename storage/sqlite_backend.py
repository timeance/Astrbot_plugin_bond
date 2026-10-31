"""心弦好感度 - SQLite 存储后端（默认实现）。

并发策略：check_same_thread=False + 全局 threading.Lock + WAL + busy_timeout。
读-改-写在锁内原子完成。sqlite3 为同步驱动，async 方法内直接执行
（操作均为毫秒级，不阻塞事件循环）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path

from ..core.decay import consolidate_half_life, effective_favor
from ..core.decimal import round1
from ..core.models import FavorRecord
from ..core.scope import GLOBAL_GROUP
from .base import StorageBackend, clamp_daily
from .migrations import SCHEMA_VERSION, migrate


class SQLiteBackend(StorageBackend):
    """基于 sqlite3 标准库的存储后端。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._initial_half_life = 10.0

    def configure_initial_half_life(self, value):
        self._initial_half_life = float(value)

    async def init(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = self._open_connection()

    def _open_connection(self) -> sqlite3.Connection:
        """打开并迁移一个连接；失败时不留下半初始化连接。"""
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise ValueError(f"不支持的未来 schema 版本: {version}")
            # 并发三件套：WAL + busy_timeout + synchronous=NORMAL（WAL 模式下
            # NORMAL 已保证崩溃一致性，FULL 只多付每次提交的 fsync——2026-09-11
            # 审查发现第三件缺失，补齐）
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=3000")
            conn.execute("PRAGMA synchronous=NORMAL")
            migrate(conn)
            return conn
        except Exception:
            conn.close()
            raise

    def _c(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("storage 尚未 init()")
        return self._conn

    @staticmethod
    def _remove_sidecars(db_path: Path) -> None:
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)

    async def _backup_to(self, destination: Path) -> None:
        """用 SQLite backup API 生成一致性快照（仅供 router 调用）。"""
        destination = Path(destination)
        with self._lock:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f"备份目标已存在: {destination.name}")
            target = None
            error: Exception | None = None
            try:
                target = sqlite3.connect(str(destination), check_same_thread=False)
                self._c().backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
                target.commit()
            except Exception as exc:
                error = exc
            finally:
                if target is not None:
                    try:
                        target.close()
                    except Exception as close_error:
                        if error is None:
                            error = close_error
            if error is not None:
                try:
                    self._remove_sidecars(destination)
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
                raise error

    async def _restore_from(self, source: Path) -> None:
        """原子替换当前数据库，并保持这个 backend 对象继续可用。"""
        source = Path(source)
        with self._lock:
            db_path = self._db_path
            staged_fd, staged_name = tempfile.mkstemp(
                prefix=f".{db_path.name}.restore-",
                suffix=".db",
                dir=str(db_path.parent),
            )
            os.close(staged_fd)
            staged = Path(staged_name)
            old = db_path.parent / (
                f".{db_path.name}.restore-old-{uuid.uuid4().hex}.db"
            )
            old_moved = False
            try:
                source_conn = sqlite3.connect(str(source), check_same_thread=False)
                staged_conn = sqlite3.connect(str(staged), check_same_thread=False)
                try:
                    source_conn.backup(staged_conn)
                    staged_conn.execute("PRAGMA journal_mode=DELETE")
                    staged_conn.commit()
                finally:
                    staged_conn.close()
                    source_conn.close()
                self._remove_sidecars(staged)

                current = self._c()
                checkpoint = current.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()
                if (
                    not checkpoint
                    or int(checkpoint[0]) != 0
                    or int(checkpoint[1]) != int(checkpoint[2])
                ):
                    raise RuntimeError("当前数据库 WAL 仍被占用，拒绝恢复")
                current.close()
                self._conn = None
                self._remove_sidecars(db_path)

                if db_path.exists() or db_path.is_symlink():
                    os.replace(str(db_path), str(old))
                    old_moved = True
                os.replace(str(staged), str(db_path))
                staged = None
                try:
                    self._conn = self._open_connection()
                except Exception as open_error:
                    self._remove_sidecars(db_path)
                    db_path.unlink(missing_ok=True)
                    if old_moved and old.exists():
                        os.replace(str(old), str(db_path))
                        old_moved = False
                    try:
                        self._conn = self._open_connection()
                    except Exception as rollback_error:
                        raise RuntimeError("恢复失败且回滚失败") from rollback_error
                    raise open_error
                if old.exists():
                    old.unlink(missing_ok=True)
            except Exception:
                if self._conn is None:
                    try:
                        if old_moved and old.exists() and not db_path.exists():
                            os.replace(str(old), str(db_path))
                            old_moved = False
                        self._conn = self._open_connection()
                    except Exception as rollback_error:
                        raise RuntimeError(
                            "恢复失败且无法恢复原连接"
                        ) from rollback_error
                raise
            finally:
                if staged is not None:
                    self._remove_sidecars(staged)
                    staged.unlink(missing_ok=True)

    async def get(self, group_id: str, user_id: str) -> FavorRecord | None:
        with self._lock:
            row = (
                self._c()
                .execute(
                    "SELECT favor, updated_at, relationship, nickname, impression, tags, impression_at, half_life, points "
                    "FROM favor WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                )
                .fetchone()
            )
        if row is None:
            return None
        return FavorRecord(
            group_id=group_id,
            user_id=user_id,
            favor=float(row[0]),
            updated_at=row[1],
            relationship=row[2] or "",
            nickname=row[3] or "",
            impression=row[4] or "",
            tags=row[5] or "",
            impression_at=row[6] or 0.0,
            half_life=float(row[7] or 10.0),
            points=row[8] or "[]",
        )

    def _compute_favor_write(
        self,
        conn,
        group_id: str,
        user_id: str,
        delta: float,
        max_favor: float,
        min_favor: float,
        decay,
        default_favor: float,
        now: float,
        effective=None,
    ) -> tuple[float, float, float]:
        """锁内计算一次好感写入的新值（须在 self._lock 内、事务中调用）。

        读存量 → 衰减到当下 → 叠加 delta → 封顶收敛。返回
        (new_value, real_delta, new_half_life)，与 apply_delta /
        apply_favor_change 共用同一套数值语义（单一真相）。"""
        row = conn.execute(
            "SELECT favor, updated_at, half_life FROM favor WHERE group_id=? AND user_id=?",
            (group_id, user_id),
        ).fetchone()
        if row:
            current, last_ts, half_life = (
                float(row[0]),
                row[1],
                float(row[2] or 10.0),
            )
        else:
            # 无记录以 default_favor 为基数（updated_at=0 表示从未互动，不吃衰减）
            current, last_ts, half_life = (
                float(default_favor or 0.0),
                0.0,
                self._initial_half_life,
            )
        # 时间衰减（指数遗忘曲线）：落库前先把存量衰减到当下（锁定），再叠加本次增减
        if decay:
            base, growth, h_max, baseline = decay
            current = (
                effective(current, last_ts, half_life)
                if effective is not None
                else effective_favor(
                    current, last_ts, now, half_life=half_life, baseline=baseline
                )
            )
            # 正向互动巩固半衰期（SM-2 式），新 h 随本次写库落列
            new_h = consolidate_half_life(
                half_life,
                base=base,
                growth=growth,
                h_max=h_max,
                positive=delta > 0,
            )
        else:
            new_h = half_life
        # 收敛到 1 位小数：吸收每日限幅边界处的浮点幽灵微增量
        new_value = round1(max(min_favor, min(max_favor, current + delta)))
        real_delta = round1(new_value - current)
        return new_value, real_delta, new_h

    async def apply_delta(
        self,
        group_id: str,
        user_id: str,
        delta: float,
        max_favor: float,
        min_favor: float = -100.0,
        decay: tuple[float, float, float, float] | None = None,
        default_favor: float = 0.0,
        effective=None,
    ) -> tuple[FavorRecord, float]:
        """只动数值的增减（数值语义见 base.py）。主写路径走
        apply_favor_change（同事务带额度/流水/冷却）；本方法保留给
        无需记账的调用方。"""
        now = time.time()
        with self._lock:
            conn = self._c()
            new_value, real_delta, new_h = self._compute_favor_write(
                conn,
                group_id,
                user_id,
                delta,
                max_favor,
                min_favor,
                decay,
                default_favor,
                now,
                effective,
            )
            conn.execute(
                "INSERT INTO favor(group_id, user_id, favor, updated_at, half_life) VALUES(?,?,?,?,?) "
                "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                "favor=excluded.favor, updated_at=excluded.updated_at, half_life=excluded.half_life",
                (group_id, user_id, new_value, now, new_h),
            )
            conn.commit()
        return (
            FavorRecord(group_id, user_id, new_value, now, half_life=new_h),
            real_delta,
        )

    async def apply_favor_change(
        self,
        group_id: str,
        user_id: str,
        delta: float,
        *,
        max_favor: float,
        min_favor: float = -100.0,
        decay: tuple[float, float, float, float] | None = None,
        default_favor: float = 0.0,
        day: str = "",
        reason: str = "api",
        source: str = "api",
        message: str = "",
        cooldown_key: str | None = None,
        daily_cap_up: float | None = None,
        daily_cap_down: float | None = None,
        effective=None,
    ) -> tuple[FavorRecord, float, float, bool]:
        """主写路径单事务：每日限幅 + 数值变动 + 当日额度 + 流水 (+ 冷却)。

        历史缺陷（2026-09-11 审查 X-P1c）：favor_service 曾按
        apply_delta → add_daily_gain → add_log → touch_event 四次独立
        commit 串接，进程在中间崩溃会留下"好感已变、流水/额度缺失"的
        漂移行——undo、里程碑、限幅记账全部失真。与 apply_undo 同一
        模式：锁内 BEGIN → 全部语句 → commit，任一步失败整体回滚。
        限幅读（daily_gain）也在同一事务内（Sourcery #66：并发写不会
        双双读到同一剩余额度后超额落库）。返回
        (记录, 实际变化量, 限幅后拟写入量, 是否触顶截断)。"""
        now = time.time()
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN")
                allowed, capped = delta, False
                if daily_cap_up is not None and daily_cap_down is not None and day:
                    grow = conn.execute(
                        "SELECT gain FROM daily_gain "
                        "WHERE group_id=? AND user_id=? AND day=?",
                        (group_id, user_id, day),
                    ).fetchone()
                    allowed, capped = clamp_daily(
                        delta,
                        round1(grow[0]) if grow else 0.0,
                        daily_cap_up,
                        daily_cap_down,
                    )
                    if allowed == 0:
                        # 当日额度耗尽：不动任何表（与历史行为一致——
                        # 旧实现在 service 层提前 return，不写不摸）
                        conn.rollback()
                        cur, _, _ = self._compute_favor_write(
                            conn,
                            group_id,
                            user_id,
                            0,
                            max_favor,
                            min_favor,
                            decay,
                            default_favor,
                            now,
                            effective,
                        )
                        return (
                            FavorRecord(group_id, user_id, cur, now),
                            0.0,
                            0.0,
                            True,
                        )
                new_value, real_delta, new_h = self._compute_favor_write(
                    conn,
                    group_id,
                    user_id,
                    allowed,
                    max_favor,
                    min_favor,
                    decay,
                    default_favor,
                    now,
                    effective,
                )
                conn.execute(
                    "INSERT INTO favor(group_id, user_id, favor, updated_at, half_life) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                    "favor=excluded.favor, updated_at=excluded.updated_at, half_life=excluded.half_life",
                    (group_id, user_id, new_value, now, new_h),
                )
                if real_delta:
                    if day:
                        conn.execute(
                            "INSERT INTO daily_gain(group_id, user_id, day, gain) VALUES(?,?,?,?) "
                            "ON CONFLICT(group_id, user_id, day) DO UPDATE SET gain=gain+excluded.gain",
                            (group_id, user_id, day, real_delta),
                        )
                    conn.execute(
                        "INSERT INTO favor_log(group_id, user_id, delta, favor_before, favor_after, reason, source, ts, message) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            group_id,
                            user_id,
                            real_delta,
                            round1(new_value - real_delta),
                            new_value,
                            (reason or "")[:200],
                            source,
                            now,
                            (message or "")[:200],
                        ),
                    )
                if cooldown_key:
                    conn.execute(
                        "INSERT INTO cooldown(group_id, user_id, key, last_ts) VALUES(?,?,?,?) "
                        "ON CONFLICT(group_id, user_id, key) DO UPDATE SET last_ts=excluded.last_ts",
                        (group_id, user_id, cooldown_key, now),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return (
            FavorRecord(group_id, user_id, new_value, now, half_life=new_h),
            real_delta,
            allowed,
            capped,
        )

    def _upsert(
        self,
        group_id: str,
        user_id: str,
        cols: dict[str, object],
        *,
        touch: bool = False,
    ) -> None:
        """锁内 upsert favor 表的指定列（须在 self._lock 内调用）。

        无记录时 INSERT（一律带 updated_at=now，新行以"现在"起算衰减锚点）；
        行存在时只 UPDATE 给定列。touch=True 时冲突分支一并刷新
        updated_at（好感数值变动必须重置衰减锚点；昵称/关系/印象不算
        好感变动，不重置）。列名来自本类固定调用点，无用户输入。
        """
        now = time.time()
        col_sql = ", ".join(cols)
        ph = ", ".join("?" for _ in cols)
        update_sql = ", ".join(f"{c}=excluded.{c}" for c in cols)
        if touch:
            update_sql += ", updated_at=excluded.updated_at"
        self._c().execute(
            f"INSERT INTO favor(group_id, user_id, updated_at, half_life, {col_sql}) "
            f"VALUES(?,?,?,?,{ph}) "
            f"ON CONFLICT(group_id, user_id) DO UPDATE SET {update_sql}",
            (group_id, user_id, now, self._initial_half_life, *cols.values()),
        )
        self._c().commit()

    async def set_value(self, group_id: str, user_id: str, value: float) -> FavorRecord:
        value = round1(value)
        with self._lock:
            self._upsert(group_id, user_id, {"favor": value}, touch=True)
        return FavorRecord(group_id, user_id, value, time.time())

    async def set_value_with_log(
        self,
        group_id: str,
        user_id: str,
        value: float,
        *,
        reason: str,
        source: str,
        default_favor: float = 0.0,
        effective=None,
    ) -> FavorRecord:
        value = round1(value)
        now = time.time()
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN")
                row = conn.execute(
                    "SELECT favor, updated_at, half_life FROM favor "
                    "WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ).fetchone()
                if row:
                    stored, ts, h = float(row[0]), row[1], float(row[2] or 10.0)
                else:
                    stored, ts, h = (
                        float(default_favor or 0.0),
                        0.0,
                        self._initial_half_life,
                    )
                before = effective(stored, ts, h) if effective else round1(stored)
                conn.execute(
                    "INSERT INTO favor(group_id, user_id, favor, updated_at, half_life) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                    "favor=excluded.favor, updated_at=excluded.updated_at",
                    (group_id, user_id, value, now, h),
                )
                delta = round1(value - before)
                if delta != 0:
                    conn.execute(
                        "INSERT INTO favor_log(group_id, user_id, delta, favor_before, "
                        "favor_after, reason, source, ts) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            group_id,
                            user_id,
                            delta,
                            before,
                            value,
                            (reason or "")[:200],
                            source,
                            now,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return FavorRecord(group_id, user_id, value, now, half_life=h)

    async def set_relationship(
        self, group_id: str, user_id: str, relationship: str
    ) -> None:
        with self._lock:
            self._upsert(group_id, user_id, {"relationship": relationship or ""})

    async def set_nickname(self, group_id: str, user_id: str, nickname: str) -> None:
        with self._lock:
            self._upsert(group_id, user_id, {"nickname": nickname or ""})

    async def set_points(self, group_id: str, user_id: str, points: list[dict]) -> None:
        """写入印象点集（P-C；不改好感数值/衰减锚）。"""
        import json as _json

        with self._lock:
            self._upsert(
                group_id,
                user_id,
                {"points": _json.dumps(list(points or []), ensure_ascii=False)},
            )

    async def set_profile(
        self,
        group_id: str,
        user_id: str,
        impression: str,
        tags: list[str],
        points: list[dict],
    ) -> None:
        """一次 upsert 同步写 印象/标签/点集（Sourcery：分两次写会在第二步
        失败时留下"点已并、印象仍旧"的不一致，重试再并一次会膨胀权重）。"""
        import json as _json

        with self._lock:
            self._upsert(
                group_id,
                user_id,
                {
                    "impression": (impression or "").strip(),
                    "tags": _json.dumps(list(tags or []), ensure_ascii=False),
                    "points": _json.dumps(list(points or []), ensure_ascii=False),
                },
            )

    async def set_impression(
        self, group_id: str, user_id: str, impression: str, tags: list[str]
    ) -> None:
        with self._lock:
            self._upsert(
                group_id,
                user_id,
                {
                    "impression": (impression or "").strip(),
                    "tags": json.dumps(list(tags or []), ensure_ascii=False),
                    "impression_at": time.time(),
                },
            )

    async def impression_logs(self, group_id, user_id, limit):
        with self._lock:
            cursor = (
                self._c()
                .execute(
                    "SELECT log_id FROM impression_progress WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                )
                .fetchone()
            )
            rows = (
                self._c()
                .execute(
                    "SELECT id, delta, message, reason, source, ts, reversed FROM favor_log "
                    "WHERE group_id=? AND user_id=? AND source='judge' AND reversed=0 "
                    "AND id>? ORDER BY id ASC LIMIT ?",
                    (group_id, user_id, cursor[0] if cursor else 0, limit),
                )
                .fetchall()
            )
            return [
                dict(
                    zip(
                        (
                            "id",
                            "delta",
                            "message",
                            "reason",
                            "source",
                            "ts",
                            "reversed",
                        ),
                        r,
                    )
                )
                for r in reversed(rows)
            ]

    async def commit_impression(
        self, group_id, user_id, impression, tags, log_ids, points=None
    ):
        if not log_ids:
            return False
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN")
                progress = conn.execute(
                    "SELECT log_id FROM impression_progress WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ).fetchone()
                if progress and min(log_ids) <= progress[0]:
                    conn.rollback()
                    return False
                for log_id in log_ids:
                    valid = conn.execute(
                        "SELECT 1 FROM favor_log WHERE id=? AND group_id=? AND user_id=? "
                        "AND source='judge' AND reversed=0",
                        (log_id, group_id, user_id),
                    ).fetchone()
                    if not valid:
                        conn.rollback()
                        return False
                now = time.time()
                conn.execute(
                    "INSERT INTO favor(group_id,user_id,updated_at,impression,tags,impression_at,points,half_life) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
                    "impression=excluded.impression,tags=excluded.tags,impression_at=excluded.impression_at,"
                    "points=COALESCE(?,favor.points)",
                    (
                        group_id,
                        user_id,
                        now,
                        impression,
                        json.dumps(tags, ensure_ascii=False),
                        now,
                        (
                            json.dumps(points, ensure_ascii=False)
                            if points is not None
                            else "[]"
                        ),
                        self._initial_half_life,
                        (
                            json.dumps(points, ensure_ascii=False)
                            if points is not None
                            else None
                        ),
                    ),
                )
                conn.execute(
                    "INSERT INTO impression_progress VALUES(?,?,?) ON CONFLICT(group_id,user_id) "
                    "DO UPDATE SET log_id=MAX(log_id,excluded.log_id)",
                    (group_id, user_id, max(log_ids)),
                )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    async def ranking(self, group_id: str, limit: int = 10) -> list[FavorRecord]:
        # X9：重读走线程池（不再阻塞主线程的回复）
        return await asyncio.to_thread(self._ranking_sync, group_id, limit)

    def _ranking_sync(self, group_id: str, limit: int) -> list[FavorRecord]:
        with self._lock:
            rows = (
                self._c()
                .execute(
                    "SELECT user_id, favor, updated_at, half_life FROM favor "
                    "WHERE group_id=? ORDER BY favor DESC, updated_at ASC LIMIT ?",
                    (group_id, limit),
                )
                .fetchall()
            )
        return [
            FavorRecord(
                group_id, r[0], float(r[1]), r[2], half_life=float(r[3] or 10.0)
            )
            for r in rows
        ]

    async def list_favor(
        self, group_id: str | None = None, limit: int = 500
    ) -> list[FavorRecord]:
        return await asyncio.to_thread(self._list_favor_sync, group_id, limit)

    def _list_favor_sync(self, group_id: str | None, limit: int) -> list[FavorRecord]:
        with self._lock:
            if group_id:
                rows = (
                    self._c()
                    .execute(
                        "SELECT user_id, favor, updated_at, relationship, nickname, impression, tags, impression_at, half_life, points FROM favor "
                        "WHERE group_id=? ORDER BY updated_at DESC LIMIT ?",
                        (group_id, limit),
                    )
                    .fetchall()
                )
                recs = [
                    FavorRecord(
                        group_id,
                        r[0],
                        float(r[1]),
                        r[2],
                        r[3] or "",
                        r[4] or "",
                        r[5] or "",
                        r[6] or "",
                        r[7] or 0.0,
                        float(r[8] or 10.0),
                        r[9] or "[]",
                    )
                    for r in rows
                ]
            else:
                rows = (
                    self._c()
                    .execute(
                        "SELECT group_id, user_id, favor, updated_at, relationship, nickname, impression, tags, impression_at, half_life, points FROM favor "
                        "ORDER BY updated_at DESC LIMIT ?",
                        (limit,),
                    )
                    .fetchall()
                )
                recs = [
                    FavorRecord(
                        r[0],
                        r[1],
                        float(r[2]),
                        r[3],
                        r[4] or "",
                        r[5] or "",
                        r[6] or "",
                        r[7] or "",
                        r[8] or 0.0,
                        float(r[9] or 10.0),
                        r[10] or "[]",
                    )
                    for r in rows
                ]
        return recs

    async def distinct_groups(self) -> list[dict]:
        return await asyncio.to_thread(self._distinct_groups_sync)

    def _distinct_groups_sync(self) -> list[dict]:
        with self._lock:
            rows = (
                self._c()
                .execute(
                    "SELECT group_id, COUNT(*) FROM favor GROUP BY group_id ORDER BY group_id"
                )
                .fetchall()
            )
        return [{"group_id": r[0], "count": r[1]} for r in rows]

    async def daily_gain(self, group_id: str, user_id: str, day: str) -> float:
        with self._lock:
            row = (
                self._c()
                .execute(
                    "SELECT gain FROM daily_gain WHERE group_id=? AND user_id=? AND day=?",
                    (group_id, user_id, day),
                )
                .fetchone()
            )
        # 读时收敛：消除累积小增量导致的浮点漂移，使每日限幅比较干净
        return round1(row[0]) if row else 0.0

    async def add_daily_gain(
        self, group_id: str, user_id: str, day: str, delta: float
    ) -> None:
        with self._lock:
            self._c().execute(
                "INSERT INTO daily_gain(group_id, user_id, day, gain) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id, user_id, day) DO UPDATE SET gain=gain+excluded.gain",
                (group_id, user_id, day, delta),
            )
            self._c().commit()

    async def last_event_at(
        self, group_id: str, user_id: str, key: str
    ) -> float | None:
        with self._lock:
            row = (
                self._c()
                .execute(
                    "SELECT last_ts FROM cooldown WHERE group_id=? AND user_id=? AND key=?",
                    (group_id, user_id, key),
                )
                .fetchone()
            )
        return row[0] if row else None

    async def touch_event(
        self, group_id: str, user_id: str, key: str, ts: float
    ) -> None:
        with self._lock:
            self._c().execute(
                "INSERT INTO cooldown(group_id, user_id, key, last_ts) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id, user_id, key) DO UPDATE SET last_ts=excluded.last_ts",
                (group_id, user_id, key, ts),
            )
            self._c().commit()

    async def observe_member(self, group_id: str, user_id: str) -> None:
        """记录成员最近一次在原始群出现的时间。"""
        with self._lock:
            self._c().execute(
                "INSERT INTO observed_members(group_id, user_id, last_seen_at) "
                "VALUES(?,?,?) ON CONFLICT(group_id, user_id) DO UPDATE SET "
                "last_seen_at=excluded.last_seen_at",
                (group_id, user_id, time.time()),
            )
            self._c().commit()

    async def observed_groups(self) -> list[dict]:
        return await asyncio.to_thread(self._observed_groups_sync)

    def _observed_groups_sync(self) -> list[dict]:
        with self._lock:
            rows = (
                self._c()
                .execute(
                    "SELECT group_id, COUNT(*) FROM observed_members "
                    "GROUP BY group_id ORDER BY group_id"
                )
                .fetchall()
            )
        return [{"group_id": row[0], "count": row[1]} for row in rows]

    async def ranking_observed(
        self, group_id: str, limit: int = 10
    ) -> list[FavorRecord]:
        return await asyncio.to_thread(self._ranking_observed_sync, group_id, limit)

    def _ranking_observed_sync(self, group_id: str, limit: int) -> list[FavorRecord]:
        with self._lock:
            rows = (
                self._c()
                .execute(
                    "SELECT f.user_id, f.favor, f.updated_at, f.relationship, "
                    "f.nickname, f.impression, f.tags, f.impression_at, "
                    "f.half_life, f.points FROM favor AS f "
                    "INNER JOIN observed_members AS o ON o.user_id=f.user_id "
                    "WHERE f.group_id=? AND o.group_id=? "
                    "ORDER BY f.favor DESC, f.user_id ASC LIMIT ?",
                    (GLOBAL_GROUP, group_id, int(limit)),
                )
                .fetchall()
            )
        return [
            FavorRecord(
                GLOBAL_GROUP,
                row[0],
                float(row[1]),
                row[2],
                row[3] or "",
                row[4] or "",
                row[5] or "",
                row[6] or "",
                row[7] or 0.0,
                float(row[8] or 10.0),
                row[9] or "[]",
            )
            for row in rows
        ]

    async def list_observed(self, group_id: str) -> list[FavorRecord]:
        return await self.ranking_observed(group_id, -1)

    async def reset(self, group_id: str, user_id: str | None = None) -> None:
        # 生产标准：execute 的 SQL 保持全字面量（表名也不插值），值一律 ? 参数化
        if user_id is None:
            stmts = [
                ("DELETE FROM impression_progress WHERE group_id=?", (group_id,)),
                ("DELETE FROM favor WHERE group_id=?", (group_id,)),
                ("DELETE FROM daily_gain WHERE group_id=?", (group_id,)),
                ("DELETE FROM cooldown WHERE group_id=?", (group_id,)),
                ("DELETE FROM favor_log WHERE group_id=?", (group_id,)),
                ("DELETE FROM observed_members WHERE group_id=?", (group_id,)),
            ]
        else:
            stmts = [
                (
                    "DELETE FROM impression_progress WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ),
                (
                    "DELETE FROM favor WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ),
                (
                    "DELETE FROM daily_gain WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ),
                (
                    "DELETE FROM cooldown WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ),
                (
                    "DELETE FROM favor_log WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ),
                (
                    "DELETE FROM observed_members WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ),
            ]
        with self._lock:
            conn = self._c()
            for sql, params in stmts:
                conn.execute(sql, params)
            conn.commit()

    async def add_log(
        self,
        group_id,
        user_id,
        delta,
        favor_before,
        favor_after,
        reason,
        source,
        ts,
        message: str = "",
    ) -> None:
        # 数据最小化边界（生产标准）：message 存触发发言摘录、reason 存评审
        # 理由——两者都可能携带聊天内容，落库前统一截断，兜底所有调用路径
        # （judge 路径在 FavorService 已截，跨插件/管理路径靠这里兜底）。
        with self._lock:
            self._c().execute(
                "INSERT INTO favor_log(group_id, user_id, delta, favor_before, favor_after, reason, source, ts, message) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    group_id,
                    user_id,
                    round1(delta),
                    round1(favor_before),
                    round1(favor_after),
                    (reason or "")[:200],
                    source,
                    ts,
                    (message or "")[:200],
                ),
            )
            self._c().commit()

    async def query_logs(
        self, group_id=None, user_id=None, limit=200, offset=0, fuzzy=False
    ) -> list[dict]:
        # X9：流水查询（面板 limit 可达 1000，LIKE 模糊走不了索引）放线程池
        return await asyncio.to_thread(
            self._query_logs_sync, group_id, user_id, limit, offset, fuzzy
        )

    def _query_logs_sync(
        self, group_id=None, user_id=None, limit=200, offset=0, fuzzy=False
    ) -> list[dict]:
        sql = (
            "SELECT id, group_id, user_id, delta, favor_before, favor_after, reason, source, ts, message, reversed "
            "FROM favor_log"
        )
        where: list[str] = []
        args: list = []
        if group_id:
            where.append("group_id = ?")
            args.append(group_id)
        if user_id:
            # X1：默认精确匹配——内部路径（衰减计数/修复期/记忆注入）语义
            # 要求精确，LIKE 前导通配会把互为子串的 QQ 混进来；fuzzy 仅
            # 供 WebUI 搜索显式开启（该路径 LIKE 无法走索引，属已知代价）
            if fuzzy:
                where.append("user_id LIKE ?")
                args.append(f"%{user_id}%")
            else:
                where.append("user_id = ?")
                args.append(user_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"
        args.extend([int(limit), int(offset)])
        with self._lock:
            rows = self._c().execute(sql, args).fetchall()
        return [
            {
                "id": r[0],
                "group_id": r[1],
                "user_id": r[2],
                "delta": float(r[3]),
                "favor_before": float(r[4]),
                "favor_after": float(r[5]),
                "reason": r[6],
                "source": r[7],
                "ts": r[8],
                "message": r[9] or "",
                "reversed": bool(r[10]),
            }
            for r in rows
        ]

    async def get_log(self, log_id: int) -> dict | None:
        with self._lock:
            row = (
                self._c()
                .execute(
                    "SELECT id, group_id, user_id, delta, favor_before, favor_after, reason, source, ts, message, reversed "
                    "FROM favor_log WHERE id=?",
                    (int(log_id),),
                )
                .fetchone()
            )
        if row is None:
            return None
        return {
            "id": row[0],
            "group_id": row[1],
            "user_id": row[2],
            "delta": float(row[3]),
            "favor_before": float(row[4]),
            "favor_after": float(row[5]),
            "reason": row[6],
            "source": row[7],
            "ts": row[8],
            "message": row[9] or "",
            "reversed": bool(row[10]),
        }

    async def mark_reversed(self, log_id: int) -> None:
        with self._lock:
            self._c().execute(
                "UPDATE favor_log SET reversed=1 WHERE id=?", (int(log_id),)
            )
            self._c().commit()

    async def apply_undo(
        self,
        log_id: int,
        *,
        max_favor: float,
        min_favor: float,
        effective=None,
        default_favor: float = 0.0,
    ) -> dict:
        """单事务撤销（契约见 base.py）：校验→反向→流水→标记，一个 commit。"""
        now = time.time()
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN")
                row = conn.execute(
                    "SELECT group_id, user_id, delta, reversed FROM favor_log WHERE id=?",
                    (int(log_id),),
                ).fetchone()
                if row is None:
                    raise ValueError("记录不存在")
                if row[3]:
                    raise ValueError("该变动已撤销")
                group_id, user_id, delta = row[0], row[1], float(row[2])
                frow = conn.execute(
                    "SELECT favor, updated_at, half_life FROM favor "
                    "WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ).fetchone()
                if frow:
                    stored, ts, h = float(frow[0]), frow[1], float(frow[2] or 10.0)
                else:
                    stored, ts, h = (
                        float(default_favor or 0.0),
                        0.0,
                        self._initial_half_life,
                    )
                cur = effective(stored, ts, h) if effective else round1(stored)
                target = round1(max(min_favor, min(max_favor, round1(cur - delta))))
                real = round1(target - cur)
                conn.execute(
                    "INSERT INTO favor(group_id, user_id, favor, updated_at, half_life) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                    "favor=excluded.favor, updated_at=excluded.updated_at",
                    (group_id, user_id, target, now, h),
                )
                if real != 0:
                    conn.execute(
                        "INSERT INTO favor_log(group_id, user_id, delta, favor_before, "
                        "favor_after, reason, source, ts) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            group_id,
                            user_id,
                            real,
                            round1(cur),
                            target,
                            f"撤销#{int(log_id)}",
                            "undo",
                            now,
                        ),
                    )
                conn.execute(
                    "UPDATE favor_log SET reversed=1 WHERE id=?", (int(log_id),)
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "group_id": group_id,
            "user_id": user_id,
            "before": round1(cur),
            "after": target,
            "delta": real,
        }

    async def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
