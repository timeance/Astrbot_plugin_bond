"""复用 assessment_call 的持久化预算；由后端传入现有连接和锁，不新建数据库。"""

from __future__ import annotations

import time
from datetime import datetime, timezone


def call_counts(conn, lock, user_id, tier, *, window_sec=300, now=None):
    """UTC 日次数与个人滚动初评次数；窗口横跨 UTC 日期边界。"""
    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    with lock:
        total, member = conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(user_id=?),0) FROM assessment_call WHERE day=? AND tier=?",
            (user_id, day, tier),
        ).fetchone()
        window = (
            conn.execute(
                "SELECT COUNT(*) FROM assessment_call WHERE tier='small' AND user_id=? AND started_at>?",
                (user_id, now - window_sec),
            ).fetchone()[0]
            if tier == "small"
            else 0
        )
        return total, member, window


def call_exists(conn, lock, event_id, tier):
    with lock:
        return (
            conn.execute(
                "SELECT 1 FROM assessment_call WHERE event_id=? AND tier=? LIMIT 1",
                (event_id, tier),
            ).fetchone()
            is not None
        )


def reserve_call(
    conn,
    lock,
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
    now=None,
):
    """锁和事务内复查本库；跨 group/global 合计检查由共享 budget_lock 保护。"""
    if tier not in {"small", "large"}:
        raise ValueError("未知评估层级")
    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    with lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            total, member, window = call_counts(
                conn, lock, user_id, tier, window_sec=window_sec, now=now
            )
            if (
                total >= bot_cap
                or member >= user_cap
                or (tier == "small" and window >= window_max_calls)
                or call_exists(conn, lock, event_id, tier)
            ):
                conn.rollback()
                return None
            cursor = conn.execute(
                "INSERT INTO assessment_call(event_id,group_id,user_id,day,tier,provider_id,status,started_at) VALUES(?,?,?,?,?,?,?,?)",
                (event_id, group_id, user_id, day, tier, provider_id, "pending", now),
            )
            conn.commit()
            return cursor.lastrowid
        except BaseException:
            conn.rollback()
            raise


def finish_call(conn, lock, call_id, status, tokens):
    if tokens is not None and (type(tokens) is not int or tokens < 0):
        tokens = None
    with lock, conn:
        conn.execute(
            "UPDATE assessment_call SET status=?,tokens=? WHERE id=?",
            (status, tokens, call_id),
        )
