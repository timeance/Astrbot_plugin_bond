"""汇总管理员导出的脱敏诊断；没有金标准时不计算语义准确率。"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path


def summarize(data):
    rows = data["rows"]
    assessments = [r for r in rows if r["kind"] == "assessment"]
    calls = [r["fields"] for r in rows if r["kind"] == "call"]
    attempts = [c for c in calls if c.get("status") != "budget"]
    events = {
        (r["session"], r["namespace"], r["fields"]["event_id"])
        for r in assessments
        if r["fields"].get("event_id")
    }
    changes = [
        r["fields"]
        for r in rows
        if r["kind"] == "relation"
        and r["fields"].get("reason") == "automatic"
        and r["fields"].get("status") == "committed"
    ]
    flips = sum(
        bool(
            c.get("term_before")
            and c.get("term_after")
            and c["term_before"] != c["term_after"]
        )
        for c in changes
    )
    elapsed = sorted(
        r["fields"]["elapsed_ms"] for r in assessments if "elapsed_ms" in r["fields"]
    )
    tokens = [c.get("tokens") for c in attempts]

    def percentile(q):
        return elapsed[max(0, math.ceil(q * len(elapsed)) - 1)] if elapsed else None

    return {
        "scope": "仅本次导出保留行；不是全量群聊或完整历史",
        "rows": len(rows),
        "sessions": len({r["session"] for r in rows}),
        "unique_recorded_assessment_events": len(events),
        "cross_session_deduplication": "unknown",
        "assessment_statuses": dict(
            Counter(r["fields"].get("status", "unknown") for r in assessments)
        ),
        "provider_calls": len(attempts),
        "provider_statuses": dict(
            Counter(c.get("status", "unknown") for c in attempts)
        ),
        "budget_skips": len(calls) - len(attempts),
        "provider_tokens": (
            sum(tokens) if tokens and all(t is not None for t in tokens) else None
        ),
        "unknown_token_calls": sum(t is None for t in tokens),
        "automatic_commits": len(changes),
        "automatic_delta_sum": {
            k: sum(c.get("delta", {}).get(k, 0) for c in changes)
            for k in ("familiarity", "affinity", "trust")
        },
        "automatic_term_flips": flips,
        "term_flips_per_100_recorded_assessment_events": (
            100 * flips / len(events) if events else None
        ),
        "assessment_pipeline_latency_ms": {
            "p50": percentile(0.5),
            "p95": percentile(0.95),
            "samples": len(elapsed),
        },
        "rendered_terms": dict(
            Counter(
                r["fields"].get("term_after", "unknown")
                for r in rows
                if r["kind"] == "render"
            )
        ),
        "missing": data.get("missing", {}),
        "unknown_without_gold_or_live_measurement": {
            "persistent_false_update_rate": None,
            "target_precision": None,
            "abstention_coverage": None,
            "neutral_drift": None,
            "main_reply_latency_p50_p95": None,
            "human_semantic_accuracy": None,
        },
        "limits": [
            "可能有丢弃、轮转、清除、导出截断；历史计数未知，不能推断未保留事件。",
            "翻转率分母只含保留且有事件ID的评估；残缺行可破坏事件配对，不代表整体翻转率。",
            "总增量不是中性漂移；拒绝/弃权数量不是语义正确率；confidence未经概率校准。",
            "流水线耗时包含数据库与Provider等待，不是主回复延迟；不含聊天正文，需另获许可的金标准盲评。",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.stat().st_size > 64 * 1024 * 1024:
        parser.error("导出文件超过64MB")
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    if "diagnostics" in data:
        data = data["diagnostics"]
    result = summarize(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
