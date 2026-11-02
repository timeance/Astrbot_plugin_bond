"""合成诊断只报告可观察事实，不把未知指标补零。"""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "summarize_diagnostics",
    Path(__file__).resolve().parents[1] / "scripts" / "summarize_diagnostics.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_summary_keeps_unknowns_and_excludes_manual_drift():
    def row(kind, **fields):
        return {
            "kind": kind,
            "session": "synthetic",
            "namespace": "scope",
            "fields": fields,
        }

    result = module.summarize(
        {
            "rows": [
                row("assessment", event_id="event", status="accepted", elapsed_ms=3),
                row(
                    "assessment",
                    event_id="event",
                    status="duplicate_or_cooldown",
                    elapsed_ms=1,
                ),
                row("call", status="ok"),
                row("call", status="budget"),
                row(
                    "relation",
                    reason="automatic",
                    status="committed",
                    delta={"trust": 0, "affinity": 1},
                    term_before="r01",
                    term_after="r02",
                ),
                row(
                    "relation", reason="manual", status="committed", delta={"trust": 50}
                ),
            ],
            "missing": {"historical_counters_unknown": True},
        }
    )
    assert result["unique_recorded_assessment_events"] == 1
    assert result["provider_calls"] == result["budget_skips"] == 1
    assert result["provider_tokens"] is None
    assert result["automatic_delta_sum"] == {
        "trust": 0,
        "affinity": 1,
        "familiarity": 0,
    }
    assert result["automatic_term_flips"] == 1
    assert result["assessment_pipeline_latency_ms"] == {
        "p50": 1,
        "p95": 3,
        "samples": 2,
    }
    assert all(
        v is None for v in result["unknown_without_gold_or_live_measurement"].values()
    )


def test_empty_summary_is_not_zero_accuracy_or_latency():
    result = module.summarize({"rows": [], "missing": {}})
    assert result["provider_tokens"] is None
    assert result["term_flips_per_100_recorded_assessment_events"] is None
    assert result["assessment_pipeline_latency_ms"]["p50"] is None
