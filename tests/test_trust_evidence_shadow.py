"""真实服务流水的同源派生、生命周期排除和合成标签边界。"""

import asyncio
from copy import deepcopy

import pytest

from astrbot_plugin_bond.core import relation_policy

from astrbot_plugin_bond.scripts.replay_trust_evidence import (
    CATEGORIES,
    HISTORICAL_EVENT_TYPES,
    digest,
    experiment,
    markdown_report,
    shadow_counts,
    shadow_summary,
    source_view,
)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    return asyncio.run(experiment(tmp_path_factory.mktemp("trust-shadow") / "runtime"))


def cases(report):
    return {case["id"]: case for case in report["cases"]}


def counts(case):
    return case["synthetic_upper_bound"]["counts"]["counts"]


def test_all_cases_account_for_identical_sources_without_analysis_writes(report):
    assert len(report["cases"]) == 13
    assert all(
        value == dict(numerator=13, denominator=13)
        for value in report["checks"].values()
    )
    assert report["provider_calls"] == 0
    rules = report["historical_rules"]
    assert len(rules) == 9
    assert set(rules) == HISTORICAL_EVENT_TYPES
    assert all(
        set(vector) == {"familiarity", "affinity", "trust"} for vector in rules.values()
    )
    assert report["historical_rules_sha256"] == digest(rules)
    assert rules["thanks"]["trust"] == 0.2
    assert rules["apology"]["trust"] == 1.0
    assert report["semantic_gain"] is None
    assert report["persistent_false_update_gain"] is None
    assert report["production_decision"] == "stop_no_proven_production_benefit"
    for case in report["cases"]:
        upper = case["synthetic_upper_bound"]
        assert upper["production_observable"] is False
        assert case["production_derived"]["reliability_counts"] is None
        assert case["production_derived"]["history_evidence"] == "unknown"
        assert case["analysis_sqlite_changes"] == 0
        refs = upper["counts"]["source_refs"]
        enumerated = [
            ref for group in upper["summary"]["groups"].values() for ref in group
        ]
        assert sorted(refs) == sorted(enumerated)
        assert len(set(refs)) == len(refs)
        assert set(counts(case)) == set(CATEGORIES)


def test_zero_trust_does_not_identify_empty_cancellation_or_negative(report):
    data = cases(report)
    for name in (
        "no_evidence",
        "numeric_cancellation",
        "labeled_cancellation",
        "labeled_negative",
    ):
        assert data[name]["formal_relation"]["trust"] == 0
    assert data["no_evidence"]["sources"] == []
    assert counts(data["numeric_cancellation"])["unknown"] == 2
    assert sorted(
        s["row"]["relation_delta"]["trust"]
        for s in data["numeric_cancellation"]["sources"]
    ) == [-1, 1]
    assert counts(data["labeled_cancellation"])["positive"] == 1
    assert counts(data["labeled_cancellation"])["negative"] == 1
    assert counts(data["labeled_negative"])["negative"] == 1
    # 独立标注的负来源在真实流水 T=0，不能按旧 T 的符号计诊断证据。
    assert data["labeled_negative"]["sources"][0]["row"]["relation_delta"]["trust"] == 0


def test_nonzero_old_trust_is_not_a_diagnostic_label(report):
    data = cases(report)
    for name in ("unannotated_nonzero_t", "nondiagnostic"):
        case = data[name]
        assert case["formal_relation"]["trust"] == 0.2
        assert counts(case)["positive"] == counts(case)["negative"] == 0
    assert counts(data["unannotated_nonzero_t"])["unknown"] == 1
    assert counts(data["nondiagnostic"])["nondiagnostic"] == 1


def test_stale_is_a_view_boundary_and_keeps_its_source(report):
    case = cases(report)["stale"]
    sources = deepcopy(case["sources"])
    original = deepcopy(sources)
    ts = sources[0]["row"]["ts"]
    assert source_view(sources, ts + 30 * 86400)["entries"][0]["category"] == "unknown"
    assert (
        source_view(sources, ts + 30 * 86400 + 0.01)["entries"][0]["category"]
        == "stale"
    )
    assert counts(case)["stale"] == 1
    assert counts(case)["positive"] == counts(case)["negative"] == 0
    assert sources == original


def test_legacy_missing_fields_are_unknown_not_an_explicit_zero(report):
    case = cases(report)["legacy_unknown"]
    source = case["sources"][0]
    assert source["row"]["relation_delta"] == {}
    assert source["row"]["event_id"] is None
    assert counts(case)["legacy_unknown"] == 1
    assert case["production_derived"]["missing_trust_vector_refs"] == [source["ref"]]
    assert counts(case)["positive"] == counts(case)["negative"] == 0


def test_manual_labels_cannot_be_promoted_to_natural_evidence(report):
    case = cases(report)["manual"]
    assert {s["row"]["source"] for s in case["sources"]} == {"admin", "admin_profile"}
    assert case["formal_relation"]["trust"] == 40
    assert counts(case)["manual_edit"] == 2
    assert counts(case)["positive"] == counts(case)["negative"] == 0


def test_undo_keeps_both_references_without_a_second_behavior(report):
    case = cases(report)["undo"]
    assert len(case["sources"]) == 2
    assert counts(case)["reversed"] == 1
    assert counts(case)["reversal_audit"] == 1
    assert counts(case)["positive"] == counts(case)["negative"] == 0
    assert case["formal_relation"]["trust"] == 0
    assert case["production_derived"]["known_logged_trust_delta_sum"] == 0
    original = next(s["row"] for s in case["sources"] if s["row"]["source"] == "judge")
    assert original["reversed"] is True


def test_reset_cannot_recover_the_annotation_source_or_prove_e_zero(report):
    case = cases(report)["reset"]
    assert case["sources"] == case["inventory"] == []
    assert case["synthetic_upper_bound"]["annotations_without_retained_source"] == [
        "10001/per_group/7001/8001/erased"
    ]
    assert case["production_derived"]["history_evidence"] == "unknown"


def test_full_partition_reference_isolates_identical_event_and_log_ids(report):
    case = cases(report)["isolation"]
    assert len(case["inventory"]) == 5
    assert len({s["ref"] for s in case["inventory"]}) == 5
    assert {s["row"]["event_id"] for s in case["inventory"]} == {"shared"}
    assert len(case["sources"]) == 1
    assert case["sources"][0]["ref"].startswith("10001/per_group/7001/8001/log/")
    assert counts(case)["positive"] == 1
    assert counts(case)["negative"] == 0


def test_decay_changes_effective_affinity_without_event_write_delta(report):
    case = cases(report)["decay"]
    assert case["states"][1]["affinity"] == 60
    assert case["states"][-1]["affinity"] == 0
    assert case["states"][-1]["trust"] == 40
    events = [s["row"] for s in case["sources"] if s["row"]["source"] == "judge"]
    assert len(events) == 2
    assert all(
        row["relation_delta"] == dict(familiarity=0, affinity=0, trust=0)
        for row in events
    )
    assert counts(case)["positive"] == counts(case)["negative"] == 0


def test_annotations_are_explicit_and_cannot_create_or_mutate_sources(report):
    case = cases(report)["labeled_negative"]
    view = case["production_derived"]
    before = deepcopy(view)
    empty = shadow_counts(view, {})
    invented = shadow_counts(view, {"missing/source": "positive"})
    assert empty == invented
    assert empty["counts"]["unknown"] == 1
    annotation = {case["sources"][0]["event_key"]: "negative"}
    assert shadow_counts(view, annotation)["counts"]["negative"] == 1
    assert shadow_summary(view, annotation)["groups"]["negative"] == [
        case["sources"][0]["ref"]
    ]
    assert view == before


def test_repeat_experiment_is_deterministic_and_report_stops_production(
    report, tmp_path, monkeypatch
):
    # 模拟后续默认规则变化：完整历史快照仍须产生相同流水与报告。
    monkeypatch.setattr(
        relation_policy,
        "DEFAULT_RULES",
        {
            event: {dimension: 0.0 for dimension in vector}
            for event, vector in relation_policy.DEFAULT_RULES.items()
        },
    )
    again = asyncio.run(experiment(tmp_path / "runtime"))
    assert again == report
    with pytest.raises(FileExistsError):
        asyncio.run(experiment(tmp_path / "runtime"))
    text = markdown_report(report)
    assert "非生产可知" in text
    assert "不能证明完整历史 E=0" in text
    assert "停止生产推进" in text
    assert "不代表当前生产自动增信规则" in text
    assert report["historical_rules_sha256"] in text
