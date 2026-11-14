"""心弦好感度 - SQLite 存储后端（默认实现）。

并发策略：check_same_thread=False + 全局 threading.Lock + WAL + busy_timeout。
读-改-写在锁内原子完成。sqlite3 为同步驱动，async 方法内直接执行
（操作均为毫秒级，不阻塞事件循环）。
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path

from ..core.decay import consolidate_half_life, effective_favor, resolve_half_life
from ..core.decimal import round1
from ..core.models import FavorRecord
from ..core.relation_policy import affinity_bounds_for_undo
from ..core.scope import GLOBAL_GROUP
from .base import StorageBackend, clamp_daily
from .migrations import SCHEMA_VERSION, migrate
from .daily_usage import read_usage, add_usage


class SQLiteBackend(StorageBackend):
    """基于 sqlite3 标准库的存储后端。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._initial_half_life = 10.0
        self._initial_favor = 0.0
        self._maintenance_strategy = "legacy"
        self._effective_record = None
        self._title_epoch = uuid.uuid4().hex

    def configure_initial_values(self, value, default_favor=0):
        self._initial_half_life = float(value)
        self._initial_favor = float(default_favor)

    def _uses_record_effective(self, effective):
        # 保留调用方显式传入的自定义投影；服务自身使用完整维护资料。
        return self._effective_record is not None and (
            effective is None
            or getattr(effective, "__self__", None)
            is getattr(self._effective_record, "__self__", None)
        )

    def _maintenance_record(self, conn, group_id, user_id):
        row = conn.execute(
            "SELECT favor,updated_at,half_life,familiarity,trust,trust_updated_at,familiarity_updated_at,"
            "affinity_maintained_at,familiarity_maintained_at,trust_maintained_at,affinity_floor_reference "
            "FROM favor WHERE group_id=? AND user_id=?",
            (group_id, user_id),
        ).fetchone()
        if row is None:
            return FavorRecord(
                group_id,
                user_id,
                favor=self._initial_favor,
                half_life=self._initial_half_life,
            )
        return FavorRecord(
            group_id,
            user_id,
            favor=row[0],
            updated_at=row[1],
            half_life=row[2],
            familiarity=row[3],
            trust=row[4],
            trust_updated_at=row[5],
            familiarity_updated_at=row[6],
            affinity_maintained_at=row[7],
            familiarity_maintained_at=row[8],
            trust_maintained_at=row[9],
            affinity_floor_reference=row[10],
        )

    def configure_maintenance(self, strategy, effective_record):
        """重载边界原子切换全部成员；结算不是互动，不刷新维护时间。"""
        if strategy not in ("independent", "legacy"):
            raise ValueError("关系维护策略无效")
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN IMMEDIATE")
                previous, _ = conn.execute(
                    "SELECT strategy,effective_at FROM maintenance_policy WHERE id=1"
                ).fetchone()
                if previous != strategy:
                    now = time.time()
                    keys = conn.execute("SELECT group_id,user_id FROM favor").fetchall()
                    for group_id, user_id in keys:
                        override = self._override_state(group_id, user_id, now)
                        # 暂停期间先结算到暂停开始，结束时仍只平移一次完整暂停时长。
                        point = (
                            min(now, override["started_at"])
                            if override["active"]
                            else now
                        )
                        rec = self._maintenance_record(conn, group_id, user_id)
                        values = effective_record(rec, point)
                        conn.execute(
                            "UPDATE favor SET favor=?,familiarity=?,trust=?,updated_at=?,"
                            "familiarity_updated_at=?,trust_updated_at=?,"
                            "affinity_maintained_at=COALESCE(affinity_maintained_at,?),"
                            "familiarity_maintained_at=COALESCE(familiarity_maintained_at,?),"
                            "trust_maintained_at=COALESCE(trust_maintained_at,?),"
                            "affinity_floor_reference=COALESCE(affinity_floor_reference,?) "
                            "WHERE group_id=? AND user_id=?",
                            (
                                values["affinity"],
                                values["familiarity"],
                                values["trust"],
                                max(rec.updated_at, point) if rec.updated_at > 0 else 0,
                                (
                                    max(rec.familiarity_updated_at, point)
                                    if rec.familiarity_updated_at > 0
                                    else 0
                                ),
                                (
                                    max(rec.trust_updated_at, point)
                                    if rec.trust_updated_at > 0
                                    else 0
                                ),
                                rec.updated_at,
                                rec.familiarity_updated_at,
                                rec.trust_updated_at,
                                rec.favor,
                                group_id,
                                user_id,
                            ),
                        )
                    conn.execute(
                        "UPDATE maintenance_policy SET strategy=?,effective_at=? WHERE id=1",
                        (strategy, now),
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            self._maintenance_strategy = strategy
            self._effective_record = effective_record

    @staticmethod
    def _mark_maintenance(conn, group_id, user_id, keys, now):
        """只在合格维护/显式管理写入时重设相应维度的参考。"""
        columns = {
            "affinity": "affinity_maintained_at",
            "familiarity": "familiarity_maintained_at",
            "trust": "trust_maintained_at",
        }
        assignments, params = [], []
        for key in keys:
            assignments.append(columns[key] + "=?")
            params.append(now)
            if key == "affinity":
                assignments.append("affinity_floor_reference=favor")
        if assignments:
            conn.execute(
                "UPDATE favor SET "
                + ",".join(assignments)
                + " WHERE group_id=? AND user_id=?",
                (*params, group_id, user_id),
            )

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
                    if self._effective_record is not None:
                        self.configure_maintenance(
                            self._maintenance_strategy, self._effective_record
                        )
                    # 缓存可重建；清理也属于恢复事务，失败时保留原库回退能力。
                    with self._conn:
                        self._conn.execute("DELETE FROM relation_title_cache")
                except Exception as open_error:
                    if self._conn is not None:
                        self._conn.close()
                        self._conn = None
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
                self._title_epoch = uuid.uuid4().hex
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
        return self._get_sync(group_id, user_id)

    def _get_sync(self, group_id: str, user_id: str) -> FavorRecord | None:
        with self._lock:
            row = (
                self._c()
                .execute(
                    "SELECT favor, updated_at, relationship, nickname, impression, tags, impression_at, half_life, points, familiarity, trust, trust_updated_at, familiarity_updated_at, affinity_maintained_at, familiarity_maintained_at, trust_maintained_at, affinity_floor_reference, impression_revision, impression_valid, impression_origin "
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
            familiarity=float(row[9]),
            trust=float(row[10]),
            trust_updated_at=float(row[11]),
            familiarity_updated_at=float(row[12]),
            affinity_maintained_at=row[13],
            familiarity_maintained_at=row[14],
            trust_maintained_at=row[15],
            affinity_floor_reference=row[16],
            impression_revision=row[17],
            impression_valid=bool(row[18]),
            impression_origin=row[19],
        )

    async def read_relation_snapshot(self, group_id, user_id, *, log_limit=0):
        return await asyncio.to_thread(
            self._read_relation_snapshot_sync, group_id, user_id, log_limit
        )

    def _read_relation_snapshot_sync(self, group_id, user_id, log_limit):
        # 同连接上的线程由锁协调；BEGIN 保证其他 WAL 连接提交也不会混入后续查询。
        with self._lock:
            conn = self._c()
            conn.execute("BEGIN")
            try:
                now = time.time()
                row = conn.execute(
                    "SELECT active,started_at,expires_at,values_json "
                    "FROM relation_override WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ).fetchone()
                rec = self._get_sync(group_id, user_id)
                temporary = dict(active=False)
                if row is not None and row[0]:
                    if now < row[2]:
                        temporary = dict(active=True, values=json.loads(row[3]))
                    elif rec is not None:
                        # get() 到期会结束覆盖并平移时钟。只读快照投影相同结果，
                        # 不在消息注入路径写到期审计或移动数据库中的维护时间。
                        duration = max(0, row[2] - row[1])
                        for key in (
                            "updated_at",
                            "familiarity_updated_at",
                            "trust_updated_at",
                            "affinity_maintained_at",
                            "familiarity_maintained_at",
                            "trust_maintained_at",
                        ):
                            value = getattr(rec, key)
                            if value is not None and value > 0:
                                setattr(rec, key, value + duration)
                logs = (
                    self._query_logs_sync(
                        group_id, user_id, limit=log_limit, exclude_passive=True
                    )
                    if log_limit > 0
                    else []
                )
                return rec, temporary, logs, now
            finally:
                conn.rollback()

    def _title_state_sync(self, group_id, user_id, origin_group_id):
        from ..core.relation_titles import fingerprint

        conn, now = self._c(), time.time()
        temporary = self._override_state(group_id, user_id, now)
        record = self._get_sync(group_id, user_id)
        history = conn.execute(
            "SELECT COUNT(*),MAX(id),COALESCE(SUM(reversed),0) FROM favor_log WHERE group_id=? AND user_id=?",
            (group_id, user_id),
        ).fetchone()
        sources = conn.execute(
            "SELECT id FROM favor_log WHERE group_id=? AND user_id=? AND origin_group_id=? AND reversed=0 ORDER BY id DESC LIMIT 3",
            (group_id, user_id, origin_group_id),
        ).fetchall()
        # 全局聚合印象不能跨群披露；撤销后须重新刷新/确认印象才能引用。
        impression = (
            record.impression
            if record
            and group_id != GLOBAL_GROUP
            and record.impression_valid
            and record.impression_at > 0
            else ""
        )
        return dict(
            record=record,
            temporary=temporary,
            now=now,
            impression=impression,
            sources=[r[0] for r in sources],
            revoked=history[2],
            stamp=fingerprint(
                [
                    vars(record) if record else None,
                    temporary,
                    history,
                    self._title_epoch,
                ]
            ),
        )

    async def title_state(self, group_id, user_id, origin_group_id):
        with self._lock, self._c() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._title_state_sync(group_id, user_id, origin_group_id)

    async def read_title_cache(self, group_id, user_id, context_id):
        with self._lock:
            row = (
                self._c()
                .execute(
                    "SELECT title,metadata,attempted_at FROM relation_title_cache WHERE group_id=? AND user_id=? AND context_id=?",
                    (group_id, user_id, context_id),
                )
                .fetchone()
            )
        if row is None:
            return None
        try:
            return dict(title=row[0], metadata=json.loads(row[1]), attempted_at=row[2])
        except (TypeError, ValueError):
            return dict(title="", metadata={}, attempted_at=row[2])

    async def title_sources_valid(self, group_id, user_id, origin_group_id, sources):
        with self._lock:
            return all(
                self._c()
                .execute(
                    "SELECT 1 FROM favor_log WHERE id=? AND group_id=? AND user_id=? AND origin_group_id=? AND reversed=0",
                    (source, group_id, user_id, origin_group_id),
                )
                .fetchone()
                for source in sources
            )

    async def title_day_calls(self, day):
        with self._lock:
            row = (
                self._c()
                .execute("SELECT calls FROM relation_title_calls WHERE day=?", (day,))
                .fetchone()
            )
            return row[0] if row else 0

    def sync_title_mode(self, enabled, snapshot_record):
        """装配时同步全部缓存的模式与当前基线，无需成员被访问或解析人格。"""
        from ..core.relation_titles import TITLE_VERSION, score_buckets

        with self._lock, self._c() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT group_id,user_id,context_id,title,metadata FROM relation_title_cache"
            ).fetchall()
            for group_id, user_id, context_id, title, raw in rows:
                try:
                    metadata = json.loads(raw)
                except (TypeError, ValueError):
                    metadata = {}
                if not isinstance(metadata, dict):
                    metadata = {}
                old = metadata.get("tracking")
                if isinstance(old, dict) and old.get("enabled") == enabled:
                    continue
                state = self._title_state_sync(group_id, user_id, group_id)
                rec = snapshot_record(
                    group_id, user_id, state["record"], state["temporary"], state["now"]
                )
                buckets = score_buckets([rec.familiarity, rec.favor, rec.trust])
                try:
                    verified = (
                        score_buckets(metadata["values"])
                        if metadata.get("version") == TITLE_VERSION
                        and len(metadata.get("values", [])) == 3
                        else None
                    )
                except (TypeError, ValueError, OverflowError):
                    verified = None
                tracking = dict(old) if isinstance(old, dict) else {}
                tracking.update(
                    buckets=buckets,
                    enabled=enabled,
                    pending=False,
                    require_fresh=bool(title and verified != buckets),
                    revision=(old.get("revision", 0) if isinstance(old, dict) else 0)
                    + 1,
                )
                metadata["tracking"] = tracking
                conn.execute(
                    "UPDATE relation_title_cache SET metadata=? WHERE group_id=? AND user_id=? AND context_id=?",
                    (
                        json.dumps(metadata, ensure_ascii=False),
                        group_id,
                        user_id,
                        context_id,
                    ),
                )

    async def update_title_tracking(
        self,
        group_id,
        user_id,
        context_id,
        tracking,
        *,
        expected_metadata,
        origin_group_id,
        expected_stamp,
        expected_buckets,
        expected_version,
        snapshot_record,
    ):
        """事务内核对关系快照、有效分桶、协议版本和缓存，只写观察状态。"""
        from ..core.relation_titles import TITLE_VERSION, score_buckets

        with self._lock, self._c() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._title_state_sync(group_id, user_id, origin_group_id)
            if state["stamp"] != expected_stamp or expected_version != TITLE_VERSION:
                return False
            rec = snapshot_record(
                group_id, user_id, state["record"], state["temporary"], state["now"]
            )
            if (
                score_buckets([rec.familiarity, rec.favor, rec.trust])
                != expected_buckets
            ):
                return False
            cached = await self.read_title_cache(group_id, user_id, context_id)
            current = cached["metadata"] if cached else None
            if current != expected_metadata:
                return False
            metadata = dict(current) if isinstance(current, dict) else {}
            metadata["tracking"] = tracking
            conn.execute(
                "INSERT INTO relation_title_cache(group_id,user_id,context_id,metadata) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id,user_id,context_id) DO UPDATE SET metadata=excluded.metadata",
                (
                    group_id,
                    user_id,
                    context_id,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            return True

    async def reserve_title_call(
        self, group_id, user_id, context_id, day, cooldown=None
    ):
        with self._lock, self._c() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            row = conn.execute(
                "SELECT attempted_at FROM relation_title_cache WHERE group_id=? AND user_id=? AND context_id=?",
                (group_id, user_id, context_id),
            ).fetchone()
            # 只有首个真实调用检查并推进刷新时钟，后续生成/复核独立计费。
            if cooldown is not None and row and row[0] > 0 and now - row[0] < cooldown:
                return False
            if cooldown is not None:
                conn.execute(
                    "INSERT INTO relation_title_cache(group_id,user_id,context_id,attempted_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(group_id,user_id,context_id) DO UPDATE SET attempted_at=excluded.attempted_at",
                    (group_id, user_id, context_id, now),
                )
            conn.execute(
                "INSERT INTO relation_title_calls(day,calls) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET calls=calls+1",
                (day,),
            )
            conn.execute(
                "DELETE FROM relation_title_calls WHERE day < date(?,'-90 day')", (day,)
            )
            return True

    async def commit_title(
        self,
        group_id,
        user_id,
        origin_group_id,
        context_id,
        stamp,
        title,
        metadata,
        *,
        expected_metadata=None,
    ):
        with self._lock, self._c() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # 锁内再次核对关系、证据、临时覆盖和恢复世代；过期生成永不回写。
            current = self._title_state_sync(group_id, user_id, origin_group_id)
            if current["stamp"] != stamp or current["temporary"]["active"]:
                return False
            if expected_metadata is not None:
                cached = await self.read_title_cache(group_id, user_id, context_id)
                if cached is None or cached["metadata"] != expected_metadata:
                    return False
            cursor = conn.execute(
                "UPDATE relation_title_cache SET title=?,metadata=? WHERE group_id=? AND user_id=? AND context_id=?",
                (
                    title,
                    json.dumps(metadata, ensure_ascii=False),
                    group_id,
                    user_id,
                    context_id,
                ),
            )
            return cursor.rowcount == 1

    async def clear_title_cache(self):
        with self._lock, self._c() as conn:
            self._title_epoch = uuid.uuid4().hex
            conn.execute("DELETE FROM relation_title_cache")

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
        # 时间衰减（指数遗忘曲线）：落库前先结算，不借此发放保温期。
        if self._uses_record_effective(effective) and row:
            current = self._effective_record(
                self._maintenance_record(conn, group_id, user_id), now
            )["affinity"]
        elif decay:
            base, growth, h_max, baseline = decay
            current = (
                effective(current, last_ts, half_life)
                if effective is not None
                else effective_favor(
                    current,
                    last_ts,
                    now,
                    half_life=resolve_half_life(
                        half_life, base=base, growth=growth, h_max=h_max
                    ),
                    baseline=baseline,
                )
            )
        # 先封顶及舍入；只有实际正增长才能巩固，避免零分事件悄悄延长半衰期。
        new_value = round1(max(min_favor, min(max_favor, current + delta)))
        real_delta = round1(new_value - current)
        if decay:
            base, growth, h_max, _ = decay
        new_h = (
            consolidate_half_life(
                half_life,
                base=base,
                growth=growth,
                h_max=h_max,
                positive=real_delta > 0,
            )
            if decay
            else half_life
        )
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
        with self._lock, self._c():
            self._guard_relation_write(group_id, user_id)
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
            self._mark_maintenance(conn, group_id, user_id, ("affinity",), now)
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
        day_start=None,
        day_end=None,
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
                self._guard_relation_write(group_id, user_id)
                allowed, capped = delta, False
                if daily_cap_up is not None and daily_cap_down is not None and day:
                    usage, _ = read_usage(
                        conn, group_id, user_id, day, day_start, day_end
                    )
                    allowed, capped = clamp_daily(
                        delta, usage["affinity"], daily_cap_up, daily_cap_down
                    )
                    allowed = math.copysign(
                        math.floor(abs(allowed) * 10 + 1e-8) / 10, allowed
                    )
                    if allowed == 0:
                        # 当日额度耗尽：不动任何表（与历史行为一致——
                        # 旧实现在 service 层提前 return，不写不摸）
                        cur, _, current_h = self._compute_favor_write(
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
                        # guard 可能刚平移临时关系暂停的时间锚；先取正确快照再回滚。
                        conn.rollback()
                        return (
                            FavorRecord(
                                group_id, user_id, cur, now, half_life=current_h
                            ),
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
                self._mark_maintenance(conn, group_id, user_id, ("affinity",), now)
                if real_delta:
                    if day:
                        add_usage(
                            conn,
                            group_id,
                            user_id,
                            day,
                            {"affinity": real_delta},
                            judge=source == "judge",
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

    def _stage_trust_evidence(self, conn, group_id, user_id, evidence, now):
        """在关系事务内核对来源、去重并登记证据；返回可消费的三件证据。"""
        for source in evidence.source_events:
            if not conn.execute(
                "SELECT 1 FROM trust_evidence e JOIN favor_log l ON l.id=e.source_log_id "
                "WHERE e.group_id=? AND e.user_id=? AND e.origin_group_id=? AND e.event_id=? "
                "AND e.invalidated=0 AND l.reversed=0",
                (group_id, user_id, evidence.group_id, source),
            ).fetchone():
                raise ValueError("信任来源已撤销或失效")
        positive = evidence.event_type in {
            "boundary_respected",
            "verified_correction",
            "promise_kept",
            "repair_action",
        }
        if (
            positive
            and conn.execute(
                "SELECT 1 FROM trust_evidence WHERE group_id=? AND user_id=? "
                "AND event_type IN ('boundary_respected','verified_correction','promise_kept','repair_action') "
                "AND (behavior_key IN (?,?) OR current_key IN (?,?) OR fingerprint=?)",
                (
                    group_id,
                    user_id,
                    evidence.behavior_key,
                    evidence.current_key,
                    evidence.behavior_key,
                    evidence.current_key,
                    evidence.fingerprint,
                ),
            ).fetchone()
        ):
            return None
        conn.execute(
            "UPDATE trust_evidence SET invalidated=1 WHERE group_id=? AND user_id=? "
            "AND event_type='boundary_respected' AND consumed_by='' AND observed_at<?",
            (group_id, user_id, now - 14 * 86400),
        )
        # 窗口外只保留去重、撤销和消费墓碑，不长期保留证据原文。
        conn.execute(
            "UPDATE trust_evidence SET reference_excerpt='',behavior_excerpt='' "
            "WHERE group_id=? AND user_id=? AND observed_at<?",
            (group_id, user_id, now - 14 * 86400),
        )
        if evidence.event_type in {
            "boundary_violation",
            "repeated_pressure",
            "deliberate_deception",
            "severe_manipulation",
        }:
            conn.execute(
                "UPDATE trust_evidence SET invalidated=1 WHERE group_id=? AND user_id=? "
                "AND event_type='boundary_respected' AND consumed_by=''",
                (group_id, user_id),
            )
            conn.execute(
                "INSERT INTO cooldown(group_id,user_id,key,last_ts) VALUES(?,?,'boundary_reset',?) "
                "ON CONFLICT(group_id,user_id,key) DO UPDATE SET last_ts=excluded.last_ts",
                (group_id, user_id, now),
            )
        reset = conn.execute(
            "SELECT last_ts FROM cooldown WHERE group_id=? AND user_id=? AND key='boundary_reset'",
            (group_id, user_id),
        ).fetchone()
        invalidated = evidence.event_type == "boundary_respected" and (
            evidence.observed_at < now - 14 * 86400
            or evidence.observed_at > now
            or (reset is not None and evidence.observed_at < reset[0])
        )
        conn.execute(
            "INSERT INTO trust_evidence(group_id,user_id,origin_group_id,event_id,event_type,current_id,"
            "current_key,reference_id,reference_excerpt,reference_at,behavior_id,behavior_key,behavior_excerpt,"
            "fingerprint,observed_at,source_log_id,source_events,invalidated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
            (
                group_id,
                user_id,
                evidence.group_id,
                evidence.event_id,
                evidence.event_type,
                evidence.current_id,
                evidence.current_key,
                evidence.reference_id,
                evidence.reference_excerpt,
                evidence.reference_at,
                evidence.behavior_id,
                evidence.behavior_key,
                evidence.behavior_excerpt,
                evidence.fingerprint,
                evidence.observed_at,
                json.dumps(evidence.source_events),
                int(invalidated),
            ),
        )
        last = conn.execute(
            "SELECT last_ts FROM cooldown WHERE group_id=? AND user_id=? AND key='boundary_respected'",
            (group_id, user_id),
        ).fetchone()
        if (
            evidence.event_type != "boundary_respected"
            or invalidated
            or (last and now - last[0] < 5 * 86400)
        ):
            return []
        rows = conn.execute(
            "SELECT e.event_id FROM trust_evidence e LEFT JOIN favor_log l ON l.id=e.source_log_id "
            "WHERE e.group_id=? AND e.user_id=? AND e.event_type='boundary_respected' "
            "AND e.invalidated=0 AND e.consumed_by='' AND e.observed_at>=? "
            "AND (l.reversed=0 OR (e.source_log_id=0 AND e.event_id=?)) "
            # 当前证据必须参与本批，避免奖励流水本身再成为后续奖励的来源。
            "ORDER BY (e.event_id=?) DESC,e.observed_at,e.event_id LIMIT 3",
            (group_id, user_id, now - 14 * 86400, evidence.event_id, evidence.event_id),
        ).fetchall()
        return [row[0] for row in rows] if len(rows) == 3 else []

    async def boundary_evidence_context(self, group_id, user_id, *, origin_group_id):
        """返回本群的最小已复核窗口，供重启后识别边界及语义重复。"""
        with self._lock:
            rows = (
                self._c()
                .execute(
                    "SELECT e.event_id,e.reference_id,e.reference_excerpt,e.reference_at,e.behavior_id,"
                    "e.behavior_excerpt,e.observed_at,e.consumed_by,e.invalidated,l.reversed,e.event_type,l.message "
                    "FROM trust_evidence e JOIN favor_log l ON l.id=e.source_log_id "
                    "WHERE e.group_id=? AND e.user_id=? AND e.origin_group_id=? "
                    "AND e.event_type IN ('boundary_respected','verified_correction','promise_kept','repair_action') AND e.observed_at>=? "
                    "ORDER BY e.observed_at DESC,e.event_id LIMIT 20",
                    (group_id, user_id, origin_group_id, time.time() - 14 * 86400),
                )
                .fetchall()
            )
        return [
            dict(
                zip(
                    (
                        "event_id",
                        "reference_id",
                        "reference_excerpt",
                        "reference_at",
                        "behavior_id",
                        "behavior_excerpt",
                        "observed_at",
                        "consumed_by",
                        "invalidated",
                        "reversed",
                        "event_type",
                        "message",
                    ),
                    row,
                )
            )
            for row in rows
        ]

    async def apply_relation_change(
        self,
        group_id,
        user_id,
        deltas,
        *,
        event_id,
        day,
        caps,
        cooldown_sec=120,
        reason="relation",
        source="judge",
        message="",
        effective=None,
        decay=None,
        default_favor=0.0,
        values=None,
        expected_revision=None,
        effective_trust=None,
        effective_familiarity=None,
        transform=None,
        original_intent=None,
        day_start=None,
        day_end=None,
        origin_group_id="",
        trust_evidence=None,
    ):
        """在同一事务计算阶段倍率、独立额度、精度余量、分值和流水。"""
        now = time.time()
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._guard_relation_write(group_id, user_id, expected_revision)
                row = conn.execute(
                    "SELECT favor,updated_at,half_life,familiarity,trust,trust_updated_at,familiarity_updated_at FROM favor WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ).fetchone()
                stored, ts, h, f, t, t_ts, f_ts = row or (
                    default_favor,
                    0,
                    self._initial_half_life,
                    0,
                    0,
                    0,
                    0,
                )
                a = effective(stored, ts, h) if effective else float(stored)
                current_f = (
                    effective_familiarity(f, f_ts)
                    if effective_familiarity
                    else float(f)
                )
                current_t = effective_trust(t, t_ts) if effective_trust else float(t)
                before = dict(familiarity=current_f, affinity=a, trust=current_t)
                if self._uses_record_effective(effective) and row:
                    before = self._effective_record(
                        self._maintenance_record(conn, group_id, user_id), now
                    )
                    a = before["affinity"]
                if values is None and not -100 <= a <= 100:
                    raise ValueError(
                        "历史亲近度超出三维范围，须由管理员确认调整后再启用事件评估"
                    )
                record = self._maintenance_record(conn, group_id, user_id)
                clocks = {
                    "affinity": record.affinity_maintained_at,
                    "familiarity": record.familiarity_maintained_at,
                    "trust": record.trust_maintained_at,
                }
                settled = dict(
                    affinity=record.updated_at,
                    familiarity=record.familiarity_updated_at,
                    trust=record.trust_updated_at,
                )
                clocks = {
                    key: settled[key] if value is None else value
                    for key, value in clocks.items()
                }
                policy_at = conn.execute(
                    "SELECT effective_at FROM maintenance_policy WHERE id=1"
                ).fetchone()[0]
                maintenance = dict(
                    maintenance_strategy=self._maintenance_strategy,
                    maintenance_at=now,
                    maintenance_policy_at=policy_at,
                    maintenance_before=clocks,
                )
                duplicate = conn.execute(
                    "SELECT 1 FROM favor_log WHERE group_id=? AND user_id=? AND event_id=?",
                    (group_id, user_id, event_id),
                ).fetchone()
                last = conn.execute(
                    "SELECT last_ts FROM cooldown WHERE group_id=? AND user_id=? AND key='relation'",
                    (group_id, user_id),
                ).fetchone()
                batch = []
                if not duplicate and trust_evidence is not None:
                    batch = self._stage_trust_evidence(
                        conn, group_id, user_id, trust_evidence, now
                    )
                    duplicate = batch is None
                if duplicate or (
                    values is None and last and now - last[0] < cooldown_sec
                ):
                    conn.rollback()
                    return dict(
                        before=before,
                        after=before,
                        delta=dict.fromkeys(before, 0.0),
                        skipped="duplicate" if duplicate else "cooldown",
                        maintenance=dict(
                            maintenance,
                            maintenance_after=clocks,
                            maintenance_qualified=dict.fromkeys(before, False),
                            maintenance_applied=dict.fromkeys(before, False),
                            maintenance_reason=dict.fromkeys(
                                before, "duplicate" if duplicate else "cooldown"
                            ),
                        ),
                    )
                used, positive_count = read_usage(
                    conn, group_id, user_id, day, day_start, day_end
                )
                # 原始意图区分配置零值与规则归零；计分仍使用已限幅的 deltas。
                original_deltas = dict(
                    deltas if original_intent is None else original_intent
                )
                if transform is not None and values is None:
                    deltas = transform(dict(before), positive_count)
                if (
                    trust_evidence is not None
                    and trust_evidence.event_type == "boundary_respected"
                    and not batch
                ):
                    deltas = dict.fromkeys(before, 0.0)
                    original_deltas = dict(deltas)
                adjusted = dict(deltas)
                after, real, qualified, allowed, reasons = {}, {}, set(), {}, {}
                for key in before:
                    lower = 0 if key == "familiarity" else -100
                    if values is not None:
                        after[key] = float(values[key])
                        qualified.add(key)
                        allowed[key], reasons[key] = 0.0, "manual"
                    else:
                        intent, _ = clamp_daily(adjusted[key], used[key], *caps[key])
                        allowed[key] = intent
                        if original_deltas[key] == 0:
                            reasons[key] = "original_zero"
                        elif adjusted[key] == 0:
                            reasons[key] = "rule_zero"
                        elif intent == 0:
                            reasons[key] = "quota"
                        else:
                            qualified.add(key)
                            reasons[key] = "eligible"
                        # 计分沿用原顺序：规则后一位小数，再扣额度和向零截断。
                        scoring = (
                            round1(adjusted[key])
                            if transform is not None and key != "familiarity"
                            else adjusted[key]
                        )
                        amount, _ = clamp_daily(scoring, used[key], *caps[key])
                        if key != "familiarity":
                            # 最后才舍入；额度尾差只能向零收敛，不可四舍五入超领。
                            amount = math.copysign(
                                math.floor(abs(amount) * 10 + 1e-8) / 10, amount
                            )
                        if key in qualified and amount == 0:
                            reasons[key] = "precision"
                        value = max(lower, min(100, before[key] + amount))
                        if key in qualified and amount != 0 and value == before[key]:
                            reasons[key] = "bound"
                        after[key] = (
                            round(value, 8)
                            if key == "familiarity" and amount
                            else value
                        )
                    real[key] = round(after[key] - before[key], 8)
                new_h = h
                if (
                    decay is not None
                    and values is None
                    and (
                        self._maintenance_strategy == "legacy"
                        or "affinity" in qualified
                    )
                ):
                    base, growth, h_max, _ = decay
                    new_h = consolidate_half_life(
                        h,
                        base=base,
                        growth=growth,
                        h_max=h_max,
                        positive=real["affinity"] > 0,
                    )
                maintained = (
                    {"familiarity", "affinity"} | (qualified & {"trust"})
                    if self._maintenance_strategy == "legacy" and values is None
                    else qualified
                )
                # 无关维度保留原始数值、结算锚、托底参考和巩固状态。
                conn.execute(
                    "INSERT INTO favor(group_id,user_id,favor,updated_at,half_life,familiarity,trust,trust_updated_at,familiarity_updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(group_id,user_id) DO UPDATE SET favor=excluded.favor,updated_at=excluded.updated_at,"
                    "half_life=excluded.half_life,familiarity=excluded.familiarity,trust=excluded.trust,trust_updated_at=excluded.trust_updated_at,familiarity_updated_at=excluded.familiarity_updated_at",
                    (
                        group_id,
                        user_id,
                        after["affinity"] if "affinity" in maintained else stored,
                        now if "affinity" in maintained else ts,
                        new_h,
                        after["familiarity"] if "familiarity" in maintained else f,
                        after["trust"] if "trust" in maintained else t,
                        now if "trust" in maintained else t_ts,
                        now if "familiarity" in maintained else f_ts,
                    ),
                )
                self._mark_maintenance(conn, group_id, user_id, maintained, now)
                if values is None:
                    add_usage(
                        conn, group_id, user_id, day, real, judge=source == "judge"
                    )
                    conn.execute(
                        "INSERT INTO cooldown(group_id,user_id,key,last_ts) VALUES(?,?,'relation',?) "
                        "ON CONFLICT(group_id,user_id,key) DO UPDATE SET last_ts=excluded.last_ts",
                        (group_id, user_id, now),
                    )
                log = conn.execute(
                    "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,reason,source,ts,message,relation_delta,event_id,origin_group_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        group_id,
                        user_id,
                        real["affinity"],
                        a,
                        after["affinity"],
                        reason[:200],
                        source,
                        now,
                        message[:200],
                        json.dumps(real),
                        event_id,
                        origin_group_id,
                    ),
                )
                if trust_evidence is not None:
                    conn.execute(
                        "UPDATE trust_evidence SET source_log_id=? WHERE group_id=? AND user_id=? AND event_id=?",
                        (log.lastrowid, group_id, user_id, event_id),
                    )
                    if batch and real["trust"] > 0:
                        conn.executemany(
                            "UPDATE trust_evidence SET consumed_by=? WHERE group_id=? AND user_id=? AND event_id=?",
                            [(event_id, group_id, user_id, item) for item in batch],
                        )
                        conn.execute(
                            "INSERT INTO cooldown(group_id,user_id,key,last_ts) VALUES(?,?,'boundary_respected',?) "
                            "ON CONFLICT(group_id,user_id,key) DO UPDATE SET last_ts=excluded.last_ts",
                            (group_id, user_id, now),
                        )
                conn.commit()
                maintenance.update(
                    intent=original_deltas,
                    adjusted=adjusted,
                    allowed=allowed,
                    maintenance_qualified={key: key in qualified for key in before},
                    maintenance_applied={key: key in maintained for key in before},
                    maintenance_reason={
                        key: (
                            "legacy"
                            if key not in qualified and key in maintained
                            else reasons[key]
                        )
                        for key in before
                    },
                    maintenance_after={
                        key: now if key in maintained else clocks[key] for key in before
                    },
                )
                return dict(
                    before=before,
                    after=after,
                    delta=real,
                    skipped=None,
                    maintenance=maintenance,
                )
            except BaseException:
                conn.rollback()
                raise

    async def apply_passive_familiarity(
        self,
        group_id,
        user_id,
        *,
        event_id,
        day,
        daily_cap_up,
        amount=0.3,
        ceiling=60.0,
        cooldown_sec=1800.0,
        expected_revision=None,
        day_start=None,
        day_end=None,
        origin_group_id="",
    ):
        """只结算 F；被动出现不是维护，也不影响主动互动冷却。"""
        for value in (amount, ceiling, cooldown_sec, daily_cap_up):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("被动熟悉度参数须为有限非负数")
        if ceiling > 100 or not isinstance(event_id, str) or not event_id:
            raise ValueError("被动熟悉度上限或事件编号无效")
        now = time.time()
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._guard_relation_write(group_id, user_id, expected_revision)
                record = self._maintenance_record(conn, group_id, user_id)
                if self._effective_record is None:
                    raise RuntimeError("被动熟悉度须配置完整关系结算函数")
                before = self._effective_record(record, now)
                clocks = {
                    "affinity": (
                        record.updated_at
                        if record.affinity_maintained_at is None
                        else record.affinity_maintained_at
                    ),
                    "familiarity": (
                        record.familiarity_updated_at
                        if record.familiarity_maintained_at is None
                        else record.familiarity_maintained_at
                    ),
                    "trust": (
                        record.trust_updated_at
                        if record.trust_maintained_at is None
                        else record.trust_maintained_at
                    ),
                }
                duplicate = conn.execute(
                    "SELECT 1 FROM favor_log WHERE group_id=? AND user_id=? AND event_id=?",
                    (group_id, user_id, event_id),
                ).fetchone()
                last = conn.execute(
                    "SELECT last_ts FROM cooldown WHERE group_id=? AND user_id=? AND key='passive_familiarity'",
                    (group_id, user_id),
                ).fetchone()
                skipped = None
                if duplicate:
                    skipped = "duplicate"
                elif last and now - last[0] < cooldown_sec:
                    skipped = "cooldown"
                elif before["familiarity"] >= ceiling:
                    skipped = "bound"
                elif amount == 0:
                    skipped = "rule_zero"
                allowed = 0.0
                if skipped is None:
                    used, _ = read_usage(
                        conn, group_id, user_id, day, day_start, day_end
                    )
                    allowed, _ = clamp_daily(
                        min(amount, ceiling - before["familiarity"]),
                        used["familiarity"],
                        daily_cap_up,
                        0,
                    )
                    allowed = max(
                        0.0,
                        min(
                            allowed,
                            amount,
                            ceiling - before["familiarity"],
                            daily_cap_up - used["familiarity"][0],
                        ),
                    )
                    if allowed <= 0:
                        skipped = "quota"
                after = dict(before)
                delta = dict.fromkeys(before, 0.0)
                if skipped is not None:
                    conn.rollback()
                else:
                    # 不先舍入衰减基线；本次增加不能因舍入超出额度或上限。
                    after["familiarity"] = min(ceiling, before["familiarity"] + allowed)
                    delta["familiarity"] = allowed
                    conn.execute(
                        "INSERT INTO favor(group_id,user_id,favor,updated_at,half_life,"
                        "familiarity,familiarity_updated_at,affinity_maintained_at,"
                        "familiarity_maintained_at,trust_maintained_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(group_id,user_id) DO UPDATE SET "
                        "familiarity=excluded.familiarity,"
                        "familiarity_updated_at=excluded.familiarity_updated_at,"
                        "familiarity_maintained_at=COALESCE(favor.familiarity_maintained_at,favor.familiarity_updated_at)",
                        (
                            group_id,
                            user_id,
                            self._initial_favor,
                            0,
                            self._initial_half_life,
                            after["familiarity"],
                            now,
                            0,
                            0,
                            0,
                        ),
                    )
                    add_usage(conn, group_id, user_id, day, delta)
                    conn.execute(
                        "INSERT INTO cooldown(group_id,user_id,key,last_ts) VALUES(?,?,'passive_familiarity',?) "
                        "ON CONFLICT(group_id,user_id,key) DO UPDATE SET last_ts=excluded.last_ts",
                        (group_id, user_id, now),
                    )
                    conn.execute(
                        "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,reason,source,ts,message,relation_delta,event_id,origin_group_id) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            group_id,
                            user_id,
                            0,
                            before["affinity"],
                            before["affinity"],
                            "群聊活跃",
                            "passive_familiarity",
                            now,
                            "",
                            json.dumps(delta),
                            event_id,
                            origin_group_id,
                        ),
                    )
                    conn.commit()
                return dict(
                    before=before,
                    after=after,
                    delta=delta,
                    skipped=skipped,
                    maintenance=dict(
                        maintenance_strategy=self._maintenance_strategy,
                        maintenance_at=now,
                        maintenance_before=clocks,
                        maintenance_after=dict(clocks),
                        maintenance_qualified=dict.fromkeys(before, False),
                        maintenance_applied=dict.fromkeys(before, False),
                        maintenance_reason=dict.fromkeys(before, "passive"),
                    ),
                )
            except BaseException:
                conn.rollback()
                raise

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
        if "impression" in cols:
            cols = dict(cols, impression_origin="legacy", impression_revision=1)
        insert_cols = {"favor": self._initial_favor, **cols}
        col_sql = ", ".join(insert_cols)
        ph = ", ".join("?" for _ in insert_cols)
        update_sql = ", ".join(f"{c}=excluded.{c}" for c in cols)
        if touch:
            update_sql += ", updated_at=excluded.updated_at"
        self._c().execute(
            f"INSERT INTO favor(group_id, user_id, updated_at, half_life, {col_sql}) "
            f"VALUES(?,?,?,?,{ph}) "
            f"ON CONFLICT(group_id, user_id) DO UPDATE SET {update_sql}",
            (group_id, user_id, now, self._initial_half_life, *insert_cols.values()),
        )
        if touch:
            self._mark_maintenance(self._c(), group_id, user_id, ("affinity",), now)
        self._c().commit()

    async def set_value(self, group_id: str, user_id: str, value: float) -> FavorRecord:
        value = round1(value)
        with self._lock, self._c():
            self._guard_relation_write(group_id, user_id)
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
                self._guard_relation_write(group_id, user_id)
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
                if self._uses_record_effective(effective) and row:
                    before = self._effective_record(
                        self._maintenance_record(conn, group_id, user_id), now
                    )["affinity"]
                conn.execute(
                    "INSERT INTO favor(group_id, user_id, favor, updated_at, half_life) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                    "favor=excluded.favor, updated_at=excluded.updated_at",
                    (group_id, user_id, value, now, h),
                )
                self._mark_maintenance(conn, group_id, user_id, ("affinity",), now)
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
                    "impression_at": time.time(),
                },
            )

    async def set_tags(self, group_id, user_id, tags):
        with self._lock:
            self._upsert(
                group_id, user_id, {"tags": json.dumps(tags, ensure_ascii=False)}
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

    async def set_profile_audited(self, group_id, user_id, impression, tags, actor):
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN IMMEDIATE")
                now = time.time()
                row = conn.execute(
                    "SELECT favor FROM favor WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ).fetchone()
                value = row[0] if row else self._initial_favor
                conn.execute(
                    "INSERT INTO favor(group_id,user_id,favor,updated_at,half_life,impression,tags,impression_at) VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(group_id,user_id) DO UPDATE SET impression=excluded.impression,tags=excluded.tags,impression_at=excluded.impression_at",
                    (
                        group_id,
                        user_id,
                        value,
                        now,
                        self._initial_half_life,
                        impression,
                        json.dumps(tags, ensure_ascii=False),
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE favor SET impression_origin='manual',impression_revision=MAX(1,impression_revision) WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                )
                conn.execute(
                    "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,reason,source,ts) VALUES(?,?,0,?,?,?,'admin_profile',?)",
                    (
                        group_id,
                        user_id,
                        value,
                        value,
                        f"Web 管理员 {actor} 修改印象与标签"[:200],
                        now,
                    ),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

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
                    "SELECT id, delta, message, reason, source, ts, reversed, event_id, group_id, user_id, origin_group_id FROM favor_log "
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
                            "event_id",
                            "group_id",
                            "user_id",
                            "origin_group_id",
                        ),
                        r,
                    )
                )
                for r in reversed(rows)
            ]

    async def commit_impression(
        self,
        group_id,
        user_id,
        impression,
        tags,
        log_ids,
        points=None,
        expected_revision=None,
        expected_impression_revision=None,
    ):
        if not log_ids:
            return False
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN")
                self._guard_relation_write(group_id, user_id, expected_revision)
                current = self._get_sync(group_id, user_id)
                if (
                    expected_impression_revision is not None
                    and expected_impression_revision
                    != (current.impression_revision if current else 0)
                ):
                    conn.rollback()
                    return False
                inherited = (
                    conn.execute(
                        "SELECT log_id FROM impression_sources WHERE group_id=? AND user_id=?",
                        (group_id, user_id),
                    ).fetchall()
                    if current and current.impression_valid
                    else []
                )
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
                    "INSERT INTO favor(group_id,user_id,updated_at,impression,tags,impression_at,points,half_life,favor) "
                    "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET "
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
                        self._initial_favor,
                        (
                            json.dumps(points, ensure_ascii=False)
                            if points is not None
                            else None
                        ),
                    ),
                )
                conn.execute(
                    "UPDATE favor SET impression_valid=1,impression_origin=?,impression_revision=MAX(1,impression_revision) WHERE group_id=? AND user_id=?",
                    (
                        (
                            "legacy_summary"
                            if current
                            and current.impression_valid
                            and current.impression
                            and current.impression_origin
                            in ("legacy", "legacy_summary")
                            else "summary"
                        ),
                        group_id,
                        user_id,
                    ),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO impression_sources VALUES(?,?,?)",
                    [
                        (group_id, user_id, value)
                        for value in set(log_ids) | {r[0] for r in inherited}
                    ],
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
                    "SELECT user_id, favor, updated_at, half_life, familiarity, trust, trust_updated_at, familiarity_updated_at, affinity_maintained_at, familiarity_maintained_at, trust_maintained_at, affinity_floor_reference FROM favor "
                    "WHERE group_id=? ORDER BY favor DESC, updated_at ASC LIMIT ?",
                    (group_id, limit),
                )
                .fetchall()
            )
        return [
            FavorRecord(
                group_id,
                r[0],
                float(r[1]),
                r[2],
                half_life=float(r[3] or 10.0),
                familiarity=float(r[4]),
                trust=float(r[5]),
                trust_updated_at=float(r[6]),
                familiarity_updated_at=float(r[7]),
                affinity_maintained_at=r[8],
                familiarity_maintained_at=r[9],
                trust_maintained_at=r[10],
                affinity_floor_reference=r[11],
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
                        "SELECT user_id, favor, updated_at, relationship, nickname, impression, tags, impression_at, half_life, points, familiarity, trust, trust_updated_at, familiarity_updated_at, affinity_maintained_at, familiarity_maintained_at, trust_maintained_at, affinity_floor_reference FROM favor "
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
                        float(r[10]),
                        float(r[11]),
                        float(r[12]),
                        float(r[13]),
                        r[14],
                        r[15],
                        r[16],
                        r[17],
                    )
                    for r in rows
                ]
            else:
                rows = (
                    self._c()
                    .execute(
                        "SELECT group_id, user_id, favor, updated_at, relationship, nickname, impression, tags, impression_at, half_life, points, familiarity, trust, trust_updated_at, familiarity_updated_at, affinity_maintained_at, familiarity_maintained_at, trust_maintained_at, affinity_floor_reference FROM favor "
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
                        float(r[11]),
                        float(r[12]),
                        float(r[13]),
                        float(r[14]),
                        r[15],
                        r[16],
                        r[17],
                        r[18],
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

    async def daily_usage(self, group_id, user_id, day, day_start=None, day_end=None):
        with self._lock, self._c():
            usage, _ = read_usage(self._c(), group_id, user_id, day, day_start, day_end)
            return usage

    async def add_daily_gain(self, group_id, user_id, day, delta):
        with self._lock, self._c():
            add_usage(self._c(), group_id, user_id, day, {"affinity": delta})

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
                    "f.half_life, f.points, f.familiarity, f.trust, f.trust_updated_at, f.familiarity_updated_at, f.affinity_maintained_at, f.familiarity_maintained_at, f.trust_maintained_at, f.affinity_floor_reference FROM favor AS f "
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
                float(row[10]),
                float(row[11]),
                float(row[12]),
                float(row[13]),
                row[14],
                row[15],
                row[16],
                row[17],
            )
            for row in rows
        ]

    async def observed_member_keys(
        self, group_id: str | None = None
    ) -> set[tuple[str, str]]:
        return await asyncio.to_thread(self._observed_member_keys_sync, group_id)

    def _observed_member_keys_sync(self, group_id):
        with self._lock:
            if group_id is None:
                rows = (
                    self._c()
                    .execute("SELECT group_id, user_id FROM observed_members")
                    .fetchall()
                )
            else:
                rows = (
                    self._c()
                    .execute(
                        "SELECT group_id, user_id FROM observed_members WHERE group_id=?",
                        (group_id,),
                    )
                    .fetchall()
                )
        return {(row[0], row[1]) for row in rows}

    async def list_observed(self, group_id: str) -> list[FavorRecord]:
        return await self.ranking_observed(group_id, -1)

    async def reset(self, group_id: str, user_id: str | None = None) -> None:
        # 生产标准：execute 的 SQL 保持全字面量（表名也不插值），值一律 ? 参数化
        if user_id is None:
            stmts = [
                ("DELETE FROM trust_evidence WHERE group_id=?", (group_id,)),
                ("DELETE FROM relation_title_cache WHERE group_id=?", (group_id,)),
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
                    "DELETE FROM trust_evidence WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ),
                (
                    "DELETE FROM relation_title_cache WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ),
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
        with self._lock, self._c():
            conn = self._c()
            active = (
                self._c()
                .execute(
                    "SELECT user_id FROM relation_override WHERE group_id=? AND active=1",
                    (group_id,),
                )
                .fetchall()
            )
            for (uid,) in active:
                if user_id is None or user_id == uid:
                    self._guard_relation_write(group_id, uid)
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
        self,
        group_id=None,
        user_id=None,
        limit=200,
        offset=0,
        fuzzy=False,
        dimension=None,
        source=None,
        group_passive=False,
    ) -> list[dict]:
        return await asyncio.to_thread(
            self._query_logs_sync,
            group_id,
            user_id,
            limit,
            offset,
            fuzzy,
            dimension,
            source,
            group_passive=group_passive,
        )

    def _query_logs_sync(
        self,
        group_id=None,
        user_id=None,
        limit=200,
        offset=0,
        fuzzy=False,
        dimension=None,
        source=None,
        exclude_passive=False,
        group_passive=False,
    ) -> list[dict]:
        columns = (
            "id, group_id, user_id, delta, favor_before, favor_after, reason, "
            "source, ts, message, reversed, relation_delta, event_id, origin_group_id"
        )
        where: list[str] = []
        args: list = []
        if group_id:
            where.append("group_id = ?")
            args.append(group_id)
        if user_id:
            if fuzzy:
                where.append("user_id LIKE ?")
                args.append(f"%{user_id}%")
            else:
                where.append("user_id = ?")
                args.append(user_id)
        # 筛选先于合并和分页；旧流水只知道亲近度，不补造 F/T 的零值。
        dimensions = {
            "familiarity": "json_extract(relation_delta, '$.familiarity') != 0",
            "affinity": "COALESCE(json_extract(relation_delta, '$.affinity'), delta) != 0",
            "trust": "json_extract(relation_delta, '$.trust') != 0",
        }
        if dimension is not None:
            if dimension not in dimensions:
                raise ValueError("未知流水维度")
            where.append(dimensions[dimension])
        if source is not None:
            where.append("source = ?")
            args.append(source)
        if exclude_passive:
            where.append("COALESCE(source, '') != 'passive_familiarity'")
        clause = " WHERE " + " AND ".join(where) if where else ""
        if group_passive:
            # 触发标识与来源群共同决定展示批次；无标识的旧记录和撤销不猜测归组。
            bucket = (
                "CASE WHEN source='passive_familiarity' AND event_id != '' "
                "THEN 'event:' || event_id ELSE 'row:' || id END"
            )
            sql = (
                f"WITH filtered AS (SELECT {columns}, {bucket} AS bucket FROM favor_log{clause}), "
                "chosen AS (SELECT group_id, origin_group_id, bucket, MAX(ts) AS latest_ts, MAX(id) AS latest_id "
                "FROM filtered GROUP BY group_id, origin_group_id, bucket "
                "ORDER BY latest_ts DESC, latest_id DESC LIMIT ? OFFSET ?) "
                "SELECT "
                + ", ".join("f." + col.strip() for col in columns.split(","))
                + " FROM filtered f JOIN chosen c USING(group_id, origin_group_id, bucket) "
                "ORDER BY c.latest_ts DESC, c.latest_id DESC, f.ts DESC, f.id DESC"
            )
        else:
            sql = f"SELECT {columns} FROM favor_log{clause} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"
        args.extend([int(limit), int(offset)])
        with self._lock:
            rows = self._c().execute(sql, args).fetchall()
        records = [self._log_record(row) for row in rows]
        if not group_passive:
            return records
        result: list[dict] = []
        batches: dict[tuple, dict] = {}
        for record in records:
            if record["source"] != "passive_familiarity" or not record["event_id"]:
                result.append(record)
                continue
            key = (record["group_id"], record["origin_group_id"], record["event_id"])
            if key not in batches:
                batch = {**record, "members": []}
                batches[key] = batch
                result.append(batch)
            batches[key]["members"].append(record)
        return result

    @staticmethod
    def _log_record(row) -> dict:
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
            "relation_delta": json.loads(row[11]),
            "event_id": row[12],
            "origin_group_id": row[13],
        }

    async def get_log(self, log_id: int) -> dict | None:
        with self._lock:
            row = (
                self._c()
                .execute(
                    "SELECT id, group_id, user_id, delta, favor_before, favor_after, reason, source, ts, message, reversed, relation_delta, event_id, origin_group_id "
                    "FROM favor_log WHERE id=?",
                    (int(log_id),),
                )
                .fetchone()
            )
        return self._log_record(row) if row is not None else None

    def _invalidate_trust_source(self, conn, log_id, *, reverse_rewards=True):
        """消费标记永不回退；撤销来源时同时失效并撤回依赖的奖励。"""
        affected = conn.execute(
            "WITH RECURSIVE affected(group_id,user_id,event_id,consumed_by) AS ("
            "SELECT group_id,user_id,event_id,consumed_by FROM trust_evidence WHERE source_log_id=? "
            "UNION SELECT e.group_id,e.user_id,e.event_id,e.consumed_by FROM trust_evidence e "
            "JOIN affected a ON e.group_id=a.group_id AND e.user_id=a.user_id "
            "WHERE (a.consumed_by!='' AND e.consumed_by=a.consumed_by) "
            "OR EXISTS(SELECT 1 FROM json_each(e.source_events) WHERE value=a.event_id)) "
            "SELECT group_id,user_id,event_id,consumed_by FROM affected",
            (int(log_id),),
        ).fetchall()
        conn.executemany(
            "UPDATE trust_evidence SET invalidated=1 WHERE group_id=? AND user_id=? AND event_id=?",
            [(group, user, event) for group, user, event, _ in affected],
        )
        dependent_trust = 0.0
        if reverse_rewards:
            for group, user, reward in {
                (group, user, reward) for group, user, _, reward in affected if reward
            }:
                row = conn.execute(
                    "SELECT id,relation_delta FROM favor_log WHERE group_id=? AND user_id=? "
                    "AND event_id=? AND reversed=0 AND id!=?",
                    (group, user, reward, int(log_id)),
                ).fetchone()
                if row:
                    dependent_trust += float(json.loads(row[1]).get("trust", 0))
                    conn.execute(
                        "UPDATE favor_log SET reversed=1 WHERE id=?", (row[0],)
                    )
        return dependent_trust

    async def mark_reversed(self, log_id: int) -> None:
        with self._lock, self._c() as conn:
            # 此低层接口保持只标记契约；数值撤销统一经 apply_undo。
            self._invalidate_trust_source(conn, log_id, reverse_rewards=False)
            conn.execute("UPDATE favor_log SET reversed=1 WHERE id=?", (int(log_id),))

    async def apply_undo(
        self,
        log_id: int,
        *,
        max_favor: float,
        min_favor: float,
        effective=None,
        default_favor: float = 0.0,
        actor: str = "",
        effective_trust=None,
        effective_familiarity=None,
    ) -> dict:
        """反向实际增量并标记原流水；不退还已消耗互动额度或模型预算。"""
        now = time.time()
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT group_id,user_id,delta,reversed,relation_delta,source,origin_group_id FROM favor_log WHERE id=?",
                    (int(log_id),),
                ).fetchone()
                if row is None:
                    raise ValueError("记录不存在")
                if row[5] == "admin_profile":
                    raise ValueError("资料审计不是可撤销的数值变动")
                if row[3]:
                    raise ValueError("该变动已撤销")
                group_id, user_id, delta = row[0], row[1], float(row[2])
                self._guard_relation_write(group_id, user_id)
                stored, ts, h, f, t, t_ts, f_ts = conn.execute(
                    "SELECT favor,updated_at,half_life,familiarity,trust,trust_updated_at,familiarity_updated_at FROM favor WHERE group_id=? AND user_id=?",
                    (group_id, user_id),
                ).fetchone() or (default_favor, 0, self._initial_half_life, 0, 0, 0, 0)
                cur = effective(stored, ts, h) if effective else float(stored)
                current_f = (
                    effective_familiarity(f, f_ts)
                    if effective_familiarity
                    else float(f)
                )
                current_t = effective_trust(t, t_ts) if effective_trust else float(t)
                if self._uses_record_effective(effective):
                    current = self._effective_record(
                        self._maintenance_record(conn, group_id, user_id), now
                    )
                    cur, current_f, current_t = (
                        current["affinity"],
                        current["familiarity"],
                        current["trust"],
                    )
                vector = json.loads(row[4])
                dependent_trust = self._invalidate_trust_source(conn, log_id)
                if dependent_trust:
                    vector["trust"] = float(vector.get("trust", 0)) + dependent_trust
                low, high = affinity_bounds_for_undo(
                    vector, legacy_min=min_favor, legacy_max=max_favor
                )
                target = round(max(low, min(high, cur - delta)), 8)
                real = round(target - cur, 8)
                next_f = round(
                    max(0, min(100, current_f - float(vector.get("familiarity", 0)))), 8
                )
                next_t = round(
                    max(-100, min(100, current_t - float(vector.get("trust", 0)))), 8
                )
                # 无 F 向量时不能把衰减值的舍入尾差变成撤销事件。
                if not vector.get("familiarity"):
                    next_f = current_f
                extra = (
                    dict(
                        familiarity=round(next_f - current_f, 8),
                        affinity=real,
                        trust=round(next_t - current_t, 8),
                    )
                    if vector
                    else {}
                )
                change_f = bool(extra.get("familiarity"))
                change_t = bool(extra.get("trust"))
                conn.execute(
                    "INSERT INTO favor(group_id,user_id,favor,updated_at,half_life,familiarity,trust,trust_updated_at,familiarity_updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(group_id,user_id) DO UPDATE SET favor=excluded.favor,updated_at=excluded.updated_at,"
                    "familiarity=excluded.familiarity,trust=excluded.trust,trust_updated_at=excluded.trust_updated_at,familiarity_updated_at=excluded.familiarity_updated_at",
                    (
                        group_id,
                        user_id,
                        target if real else stored,
                        now if real else ts,
                        h,
                        next_f if change_f else f,
                        next_t if change_t else t,
                        now if change_t else t_ts,
                        now if change_f else f_ts,
                    ),
                )
                maintained = [
                    key
                    for key, changed in (
                        ("affinity", real),
                        ("familiarity", change_f),
                        ("trust", change_t),
                    )
                    if changed
                ]
                # 被动来源沿撤销链保留，撤销或重做都不授予维护资格。
                if row[5] != "passive_familiarity":
                    self._mark_maintenance(conn, group_id, user_id, maintained, now)
                if real or any(extra.values()) or actor:
                    conn.execute(
                        "INSERT INTO favor_log(group_id,user_id,delta,favor_before,favor_after,reason,source,ts,relation_delta,origin_group_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            group_id,
                            user_id,
                            real,
                            cur,
                            target,
                            (
                                f"Web 管理员 {actor} 撤销#{int(log_id)}"
                                if actor
                                else f"撤销#{int(log_id)}"
                            )[:200],
                            (
                                "passive_familiarity"
                                if row[5] == "passive_familiarity"
                                else "undo"
                            ),
                            now,
                            json.dumps(extra),
                            row[6],
                        ),
                    )
                conn.execute(
                    "UPDATE favor_log SET reversed=1 WHERE id=?", (int(log_id),)
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return dict(
            group_id=group_id,
            user_id=user_id,
            before=round1(cur),
            after=round1(target),
            delta=real,
        )

    async def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def bot_id(self):
        """物理分库路径由路由器构造，评估只能服务此账号。"""
        from ..core.scope import account_id

        return account_id(self._db_path.parent.name)

    async def claim_assessment(self, event_id, group_id, user_id, cooldown):
        now = time.time()
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN IMMEDIATE")
                last = conn.execute(
                    "SELECT last_ts FROM cooldown WHERE group_id=? AND user_id=? AND key='assessment'",
                    (group_id, user_id),
                ).fetchone()
                if last and now - last[0] < cooldown:
                    conn.rollback()
                    return False
                cur = conn.execute(
                    "INSERT OR IGNORE INTO assessment_attempt VALUES(?,?,?,?,?)",
                    (event_id, group_id, user_id, now, "pending"),
                )
                if not cur.rowcount:
                    conn.rollback()
                    return False
                conn.execute(
                    "INSERT INTO cooldown VALUES(?,?,?,?) ON CONFLICT(group_id,user_id,key) DO UPDATE SET last_ts=excluded.last_ts",
                    (group_id, user_id, "assessment", now),
                )
                conn.commit()
                return True
            except BaseException:
                conn.rollback()
                raise

    async def finish_assessment(self, event_id, status):
        with self._lock:
            self._c().execute(
                "UPDATE assessment_attempt SET status=? WHERE event_id=?",
                (status, event_id),
            )
            self._c().commit()

    async def assessment_call_exists(self, event_id, tier):
        from .assessment_ledger import call_exists

        return call_exists(self._c(), self._lock, event_id, tier)

    async def assessment_call_counts(self, user_id, tier, *, window_sec=300):
        from .assessment_ledger import call_counts

        return call_counts(self._c(), self._lock, user_id, tier, window_sec=window_sec)

    async def reserve_assessment_call(
        self,
        event_id,
        group_id,
        user_id,
        tier,
        provider_id,
        user_cap,
        bot_cap,
        *,
        window_sec=300,
        window_max_calls=5,
    ):
        from .assessment_ledger import reserve_call

        return reserve_call(
            self._c(),
            self._lock,
            event_id,
            group_id,
            user_id,
            tier,
            provider_id,
            user_cap,
            bot_cap,
            window_sec=window_sec,
            window_max_calls=window_max_calls,
        )

    async def begin_assessment_call(
        self, event_id, group_id, user_id, tier, provider_id, user_cap, bot_cap
    ):
        return await self.reserve_assessment_call(
            event_id, group_id, user_id, tier, provider_id, user_cap, bot_cap
        )

    async def finish_assessment_call(self, call_id, status, tokens):
        from .assessment_ledger import finish_call

        finish_call(self._c(), self._lock, call_id, status, tokens)

    async def assessment_context_events(
        self, group_id, user_id, *, origin_group_id, limit=8, days=7
    ):
        """分页之前按原始群筛选；旧 global 流水没有来源，不能进入上下文。"""
        with self._lock:
            rows = (
                self._c()
                .execute(
                    "SELECT event_id,ts,reason,message FROM favor_log WHERE group_id=? AND user_id=? AND "
                    "(origin_group_id=? OR (origin_group_id='' AND group_id=?)) AND source='judge' AND reversed=0 AND ts>=? "
                    "AND (delta!=0 OR COALESCE(json_extract(relation_delta,'$.familiarity'),0)!=0 OR COALESCE(json_extract(relation_delta,'$.trust'),0)!=0) "
                    "ORDER BY ts DESC,id DESC LIMIT ?",
                    (
                        group_id,
                        user_id,
                        origin_group_id,
                        origin_group_id,
                        time.time() - days * 86400,
                        min(20, max(0, int(limit))),
                    ),
                )
                .fetchall()
            )
        return [dict(zip(("event_id", "ts", "reason", "message"), row)) for row in rows]

    async def assessment_day_counts(self, day):
        return await asyncio.to_thread(self._assessment_day_counts_sync, day)

    def _assessment_day_counts_sync(self, day):
        with self._lock:
            return dict(
                self._c()
                .execute(
                    "SELECT tier,COUNT(*) FROM assessment_call WHERE day=? GROUP BY tier",
                    (day,),
                )
                .fetchall()
            )

    async def changed_members_count(self, start, end):
        return await asyncio.to_thread(self._changed_members_count_sync, start, end)

    def _changed_members_count_sync(self, start, end):
        with self._lock:
            # 先枚举现存群键，再逐群按已有时间索引查询，避免遍历历史流水。
            # reset 同事务删除对应记录和流水，不会留下仅有流水的群。
            return (
                self._c()
                .execute(
                    "SELECT COUNT(DISTINCT logs.user_id) FROM "
                    "(SELECT DISTINCT group_id FROM favor) AS groups "
                    "CROSS JOIN favor_log AS logs INDEXED BY idx_favor_log_group_ts "
                    "WHERE logs.group_id=groups.group_id AND logs.ts>=? AND logs.ts<? "
                    "AND (logs.delta!=0 "
                    "OR COALESCE(json_extract(logs.relation_delta,'$.familiarity'),0)!=0 "
                    "OR COALESCE(json_extract(logs.relation_delta,'$.affinity'),0)!=0 "
                    "OR COALESCE(json_extract(logs.relation_delta,'$.trust'),0)!=0)",
                    (start, end),
                )
                .fetchone()[0]
            )

    async def assessment_stats(self):
        """无用量的调用保持 NULL，不能汇总成免费或零 Token。"""
        with self._lock:
            calls = (
                self._c()
                .execute(
                    "SELECT day,group_id,tier,status,COUNT(*),CASE WHEN COUNT(tokens)=COUNT(*) THEN SUM(tokens) ELSE NULL END FROM assessment_call GROUP BY day,group_id,tier,status"
                )
                .fetchall()
            )
            events = (
                self._c()
                .execute(
                    "SELECT status,COUNT(*) FROM assessment_attempt GROUP BY status"
                )
                .fetchall()
            )
            return {
                "calls": [
                    dict(
                        zip(
                            ("day", "group_id", "tier", "status", "count", "tokens"),
                            row,
                        )
                    )
                    for row in calls
                ],
                "events": dict(events),
                "currency_cost": None,
            }

    async def record_assessment_skip(self, interaction, status):
        with self._lock:
            self._c().execute(
                "INSERT OR IGNORE INTO assessment_attempt VALUES(?,?,?,?,?)",
                (
                    interaction.event_id,
                    interaction.group_id,
                    interaction.user_id,
                    time.time(),
                    status,
                ),
            )
            self._c().commit()

    async def large_call_counts(self, user_id):
        return (await self.assessment_call_counts(user_id, "large"))[:2]

    async def update_member_names(self, group_id, user_id, nickname=None, card=None):
        with self._lock, self._c():
            for key, value in (("", nickname), (group_id, card)):
                if value is not None:
                    self._c().execute(
                        "INSERT INTO member_names VALUES(?,?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET nickname=excluded.nickname,updated_at=excluded.updated_at",
                        (key, user_id, value[:256], time.time()),
                    )

    async def member_names(self):
        with self._lock:
            return {
                (g, u): n
                for g, u, n in self._c()
                .execute("SELECT group_id,user_id,nickname FROM member_names")
                .fetchall()
            }

    def _override_state(self, group_id, user_id, now):
        row = (
            self._c()
            .execute(
                "SELECT revision,active,started_at,expires_at,values_json FROM relation_override WHERE group_id=? AND user_id=?",
                (group_id, user_id),
            )
            .fetchone()
        )
        if row is None:
            return dict(revision=0, active=False)
        revision, active, started, expires, values = row
        if active and now >= expires:
            self._end_override(group_id, user_id, expires, "system", "expired")
            active = False
        return dict(
            revision=revision,
            active=bool(active),
            started_at=started,
            expires_at=expires,
            values=json.loads(values),
        )

    def _end_override(self, group_id, user_id, ended, actor, action):
        row = (
            self._c()
            .execute(
                "SELECT started_at,expires_at FROM relation_override WHERE group_id=? AND user_id=? AND active=1",
                (group_id, user_id),
            )
            .fetchone()
        )
        if row is None:
            return
        duration = max(0, min(ended, row[1]) - row[0])
        self._c().execute(
            "UPDATE favor SET updated_at=CASE WHEN updated_at>0 THEN updated_at+? ELSE updated_at END, "
            "trust_updated_at=CASE WHEN trust_updated_at>0 THEN trust_updated_at+? ELSE trust_updated_at END, "
            "familiarity_updated_at=CASE WHEN familiarity_updated_at>0 THEN familiarity_updated_at+? ELSE familiarity_updated_at END, "
            "affinity_maintained_at=CASE WHEN affinity_maintained_at>0 THEN affinity_maintained_at+? ELSE affinity_maintained_at END, "
            "familiarity_maintained_at=CASE WHEN familiarity_maintained_at>0 THEN familiarity_maintained_at+? ELSE familiarity_maintained_at END, "
            "trust_maintained_at=CASE WHEN trust_maintained_at>0 THEN trust_maintained_at+? ELSE trust_maintained_at END WHERE group_id=? AND user_id=?",
            (
                duration,
                duration,
                duration,
                duration,
                duration,
                duration,
                group_id,
                user_id,
            ),
        )
        self._c().execute(
            "UPDATE relation_override SET active=0 WHERE group_id=? AND user_id=?",
            (group_id, user_id),
        )
        self._c().execute(
            "INSERT INTO override_audit(group_id,user_id,actor,action,ts,details) VALUES(?,?,?,?,?,?)",
            (group_id, user_id, actor, action, ended, "{}"),
        )

    async def override_state(self, group_id, user_id):
        with self._lock, self._c():
            return self._override_state(group_id, user_id, time.time())

    def _guard_relation_write(self, group_id, user_id, revision=None):
        state = self._override_state(group_id, user_id, time.time())
        if state["active"] or (revision is not None and state["revision"] != revision):
            raise ValueError("临时关系生效或任务版本已过期，请结束临时关系后重试")

    async def set_override(
        self,
        group_id,
        user_id,
        values,
        duration,
        actor,
        default_favor=0,
        reason="管理员调整",
    ):
        with self._lock:
            conn = self._c()
            try:
                conn.execute("BEGIN")
                now = time.time()
                old = self._override_state(group_id, user_id, now)
                conn.execute(
                    "INSERT INTO favor(group_id,user_id,favor,updated_at,half_life) VALUES(?,?,?,?,?) ON CONFLICT(group_id,user_id) DO NOTHING",
                    (group_id, user_id, default_favor, 0, self._initial_half_life),
                )
                if values is None:
                    self._end_override(group_id, user_id, now, actor, "cancelled")
                    conn.execute(
                        "UPDATE relation_override SET revision=revision+1 WHERE group_id=? AND user_id=?",
                        (group_id, user_id),
                    )
                else:
                    started = old["started_at"] if old["active"] else now
                    conn.execute(
                        "INSERT INTO relation_override VALUES(?,?,?,?,?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET revision=excluded.revision,active=1,started_at=excluded.started_at,expires_at=excluded.expires_at,values_json=excluded.values_json",
                        (
                            group_id,
                            user_id,
                            old["revision"] + 1,
                            1,
                            started,
                            now + duration,
                            json.dumps(values),
                        ),
                    )
                    conn.execute(
                        "INSERT INTO override_audit(group_id,user_id,actor,action,ts,details) VALUES(?,?,?,?,?,?)",
                        (
                            group_id,
                            user_id,
                            actor,
                            "updated" if old["active"] else "created",
                            now,
                            json.dumps(
                                dict(
                                    values=values,
                                    expires_at=now + duration,
                                    reason=reason,
                                )
                            ),
                        ),
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    async def override_audit(self, group_id, user_id):
        with self._lock:
            rows = (
                self._c()
                .execute(
                    "SELECT actor,action,ts,details FROM override_audit WHERE group_id=? AND user_id=? ORDER BY id DESC LIMIT 20",
                    (group_id, user_id),
                )
                .fetchall()
            )
            return [
                dict(actor=a, action=b, ts=c, details=json.loads(d))
                for a, b, c, d in rows
            ]

    async def ensure_member(self, group_id, user_id):
        with self._lock, self._c():
            self._c().execute(
                "INSERT INTO favor(group_id,user_id,favor,updated_at,half_life) VALUES(?,?,?,?,?) ON CONFLICT(group_id,user_id) DO NOTHING",
                (group_id, user_id, self._initial_favor, 0, self._initial_half_life),
            )
