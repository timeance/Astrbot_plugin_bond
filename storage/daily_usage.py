"""每日互动用量，正负分开累计；时间衰减、人工设值和撤销不退还额度。"""

from __future__ import annotations

import json

DIMENSIONS = ("familiarity", "affinity", "trust")


def read_usage(conn, group_id, user_id, day, day_start=None, day_end=None):
    """须在写事务内调用；旧净额只在首次使用当天时结合原流水补齐。"""
    row = conn.execute(
        "SELECT familiarity_up,familiarity_down,affinity_up,affinity_down,trust_up,trust_down,"
        "positive_count,split_ready FROM daily_gain WHERE group_id=? AND user_id=? AND day=?",
        (group_id, user_id, day),
    ).fetchone()
    if row is None:
        return dict.fromkeys(DIMENSIONS, (0.0, 0.0)), 0
    amounts, count = list(row[:6]), row[6]
    if not row[7] and day_start is not None and day_end is not None:
        reconstructed, count = [0.0] * 6, 0
        rows = conn.execute(
            "SELECT delta,relation_delta,source FROM favor_log WHERE group_id=? AND user_id=? "
            "AND ts>=? AND ts<? AND source NOT IN ('admin','undo','admin_profile')",
            (group_id, user_id, day_start, day_end),
        )
        for delta, encoded, source in rows:
            vector = json.loads(encoded or "{}") or {"affinity": delta}
            for i, key in enumerate(DIMENSIONS):
                value = float(vector.get(key, 0))
                reconstructed[2 * i + (value < 0)] += abs(value)
            count += int(source == "judge" and delta > 0)
        # 原日志缺失时保留净额已能证明的最低用量，不把旧用量静默归零。
        amounts = [round(max(a, b), 8) for a, b in zip(amounts, reconstructed)]
        conn.execute(
            "UPDATE daily_gain SET familiarity_up=?,familiarity_down=?,affinity_up=?,affinity_down=?,"
            "trust_up=?,trust_down=?,positive_count=?,split_ready=1 WHERE group_id=? AND user_id=? AND day=?",
            (*amounts, count, group_id, user_id, day),
        )
    return {
        key: tuple(amounts[i * 2 : i * 2 + 2]) for i, key in enumerate(DIMENSIONS)
    }, count


def add_usage(conn, group_id, user_id, day, vector, *, judge=False):
    f, a, t = (float(vector.get(key, 0)) for key in DIMENSIONS)
    positive = int(judge and a > 0)
    conn.execute(
        "INSERT INTO daily_gain(group_id,user_id,day,familiarity_gain,gain,trust_gain,"
        "familiarity_up,familiarity_down,affinity_up,affinity_down,trust_up,trust_down,positive_count,split_ready) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1) ON CONFLICT(group_id,user_id,day) DO UPDATE SET "
        "familiarity_gain=familiarity_gain+excluded.familiarity_gain,gain=gain+excluded.gain,trust_gain=trust_gain+excluded.trust_gain,"
        "familiarity_up=familiarity_up+excluded.familiarity_up,familiarity_down=familiarity_down+excluded.familiarity_down,"
        "affinity_up=affinity_up+excluded.affinity_up,affinity_down=affinity_down+excluded.affinity_down,"
        "trust_up=trust_up+excluded.trust_up,trust_down=trust_down+excluded.trust_down,"
        "positive_count=positive_count+excluded.positive_count,split_ready=1",
        (
            group_id,
            user_id,
            day,
            f,
            a,
            t,
            max(f, 0),
            max(-f, 0),
            max(a, 0),
            max(-a, 0),
            max(t, 0),
            max(-t, 0),
            positive,
        ),
    )
