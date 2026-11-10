"""真实回放的候选收益、弃权代价与来源合同；不评估模型语义准确率。"""

import asyncio
import shutil
import json
import inspect
import linecache
import textwrap
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.scripts import replay_attribution_candidates as candidates
from astrbot_plugin_bond.services.assessment_service import AssessmentService
from astrbot_plugin_bond.tests.research_support import ratio


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    original = AssessmentService.assess
    inner = getattr(AssessmentService, "_assess", None)
    runtime = tmp_path_factory.mktemp("attribution") / "runtime"
    original_run = candidates.run_arm

    async def compact_arm(path, *args, **kwargs):
        result = await original_run(path, *args, **kwargs)
        target = path.resolve()
        if not target.is_relative_to(runtime.resolve()) or target == runtime.resolve():
            raise ValueError("合成回放清理路径越界")
        # 每个实验臂关闭 SQLite 后即可回收临时库，完整报告与轨迹仍在内存中。
        shutil.rmtree(target)
        return result

    with patch.object(candidates, "run_arm", compact_arm):
        data = asyncio.run(candidates.experiment(runtime))
    assert AssessmentService.assess is original
    assert getattr(AssessmentService, "_assess", None) is inner
    try:
        yield data
    finally:
        shutil.rmtree(runtime)


def fixture_cases():
    data = json.loads(candidates.FIXTURE.read_text(encoding="utf-8"))
    return data["scenarios"]


@pytest.mark.parametrize("case", fixture_cases(), ids=lambda case: case["id"])
def test_quote_geometry_contract(case):
    step = case["steps"][0]
    decision = candidates.quote_geometry(step["text"], step["response"]["evidence"])
    assert decision["abstain"] == (case["quote_expectation"] == "block")


def test_original_denominators_and_real_service_control(report):
    summary = report["summary"]
    assert summary["original_denominators"] == dict(
        events=1201, forbidden=381, zero_trust=1195
    )
    assert "不证明信任证据识别收益" in summary["trust_rule_note"]
    for result in report["original"].values():
        assert all(
            row["delta"]["trust"] == 0
            for case in result["scenarios"]
            for row in case["rows"]
        )
    expected = {
        # 原脚本让 350 条无信息寒暄返回 friendly，另有一条旧冷却样本现在入场。
        # 保留历史 gold/Provider；当前普通 insult/manipulation 均不再改变 T。
        "control": (357, 0, 0, 0),
        "quoted_evidence": (355, 0, 0, 0),
        "joke_abstain": (354, 0, 1, 0),
        "manipulation_no_source": (356, 0, 4, 0),
    }
    for arm, (false_updates, false_trust, suppressed, negative) in expected.items():
        row = summary["original"][arm]
        assert row["metrics"]["events"] == 1201
        assert row["metrics"]["persistent_false_update_rate"] == ratio(
            false_updates, 381
        )
        assert row["metrics"]["trust_false_update_rate"] == ratio(false_trust, 1195)
        assert row["suppressed_expected_updates"] == ratio(suppressed, 820)
        assert row["suppressed_expected_negative_trust"] == ratio(negative, 4)
        assert not row["new_false_updates"]


def test_exact_original_changed_scenarios_and_frozen_gold(report):
    expected = {
        "quoted_evidence": {"S03-quoted-attack", "S47-quote-confident-thanks"},
        "joke_abstain": {
            "S11-joke-stranger",
            "S12-joke-close",
            "S13-joke-distant",
            "S14-joke-boundary",
        },
        "manipulation_no_source": {
            "S23-test-injection-misclassified",
            "S24-diagnostic-violation",
            "S26-violation-apology-repair",
            "S27-violation-apology-repeat",
        },
    }
    for arm, ids in expected.items():
        changed = report["summary"]["original"][arm]["changed_rows"]
        assert {row["scenario"] for row in changed} == ids
        assert all(row["status"] == "abstained" for row in changed)
        assert all(not any(row["after"].values()) for row in changed)


def test_provider_inputs_responses_and_call_counts_are_unchanged(report):
    for section in ("original", "new_samples"):
        control = report[section]["control"]
        for arm in candidates.ARMS:
            row = report[section][arm]
            assert (
                row["provider_transcript_sha256"]
                == control["provider_transcript_sha256"]
            )
            assert (
                row["metrics"]["provider_calls"] == control["metrics"]["provider_calls"]
            )
            assert all(
                set(c["payload"])
                == {
                    "target_bot",
                    "sender",
                    "text",
                    "trusted_metadata",
                    "recent_turns",
                    "scored_events",
                    "boundary_evidence",
                }
                for c in row["provider_transcript"]
            )
    assert report["original"]["control"]["metrics"]["provider_calls"] == 1193
    assert report["summary"]["source_files_unchanged"]
    assert report["summary"]["protected_source_sha256"] == candidates.protected_hashes()


def test_quote_abstention_cost_is_not_hidden_in_old_denominators(report):
    control = report["new_samples"]["control"]
    quote = report["summary"]["new_samples"]["quoted_evidence"]
    assert control["metrics"]["events"] == 20
    assert control["metrics"]["persistent_false_update_rate"] == ratio(8, 8)
    assert quote["metrics"]["persistent_false_update_rate"] == ratio(0, 8)
    assert quote["suppressed_expected_updates"] == ratio(5, 12)
    assert quote["abstained_gate_count"] == 13
    blocked_expected = {
        row["scenario"] for row in quote["changed_rows"] if row["should_update"]
    }
    assert blocked_expected == {
        "Q03-duplicate-inside-outside",
        "Q11-author-emphasis",
        "Q13-wide-evidence",
        "Q17-explicit-self-quote",
        "Q19-code-question-inside",
    }
    surviving = {
        case["id"]
        for case in report["new_samples"]["quoted_evidence"]["scenarios"]
        if case["rows"][0]["changed"]
    }
    assert surviving == {
        "Q05-direct-thanks",
        "Q06-direct-criticism",
        "Q07-outside-quote",
        "Q08-review-outside",
        "Q16-repeated-outside",
        "Q18-code-question-outside",
        "Q20-python-code-question",
    }


def test_self_quote_and_normal_code_question_expose_semantic_limit(report):
    fixture = {case["id"]: case["steps"][0] for case in fixture_cases()}
    outside = fixture["Q18-code-question-outside"]
    inside = fixture["Q19-code-question-inside"]
    assert outside["text"] == inside["text"]
    assert outside["gold"] == inside["gold"]
    assert outside["response"]["event_type"] == inside["response"]["event_type"]
    assert outside["response"]["evidence"] != inside["response"]["evidence"]
    expected = {
        "Q17-explicit-self-quote": False,
        "Q18-code-question-outside": True,
        "Q19-code-question-inside": False,
        "Q20-python-code-question": True,
    }
    control = {
        case["id"]: case["rows"][0]
        for case in report["new_samples"]["control"]["scenarios"]
    }
    quote = {
        case["id"]: case["rows"][0]
        for case in report["new_samples"]["quoted_evidence"]["scenarios"]
    }
    for cid, retained in expected.items():
        assert control[cid]["changed"]
        assert bool(quote[cid]["changed"]) is retained
    assert report["summary"]["production_quote_recommendation"] == (
        "defer_quote_gate_semantics_not_identifiable_from_quote_geometry"
    )


def test_gate_checks_large_final_verdict_and_all_occurrences(report):
    gate = next(
        row
        for row in report["new_samples"]["quoted_evidence"]["gates"]
        if row["text"] == "别人说“谢谢你”，我说收到"
    )
    assert gate["event_type"] == "thanks"
    assert gate["evidence"] == "谢谢你" and gate["abstained"]
    calls = [
        call
        for call in report["new_samples"]["quoted_evidence"]["provider_transcript"]
        if call["message_id"] == "Q04-final-review-quoted-0000"
    ]
    assert [c["tier"] for c in calls] == ["small", "large"]
    assert [json.loads(c["completion_text"])["event_type"] for c in calls] == [
        "criticism",
        "thanks",
    ]
    duplicate = candidates.quote_geometry("谢谢你。别人也说“谢谢你”", "谢谢你")
    assert len(duplicate["occurrences"]) == 2 and duplicate["abstain"]


def test_same_observation_does_not_prove_semantic_discrimination(report):
    joke, injection, boundary = report["summary"]["observability"]
    assert joke["observations"] == 4 and joke["unique_provider_payloads"] == 1
    assert joke["expected_updates"] == [False, True, False, False]
    assert injection["unique_provider_payloads"] == 1
    assert boundary["identical_initial_state"]
    assert not boundary["boundary_source_in_provider_input"]


def test_repair_sources_cannot_create_verified_production_repair(report):
    repair = report["summary"]["repair"]
    assert repair["contract_pass"] == ratio(6, 6)
    assert repair["zero_analysis_writes"] == ratio(6, 6)
    assert repair["production_positive_repair"] == ratio(0, 0)
    assert repair["existing_shadow_case_count"] == 13
    assert all(
        check == dict(numerator=13, denominator=13)
        for check in repair["existing_shadow_checks"].values()
    )
    for case in repair["cases"]:
        for source in case["sources"]:
            assert "ts" not in source["row"]
            assert source["row"]["ts_utc"].endswith("+00:00")
    cases = {case["id"]: case for case in repair["cases"]}
    assert sum(case["eligible"] for case in cases.values()) == 1
    assert cases["valid_same_partition"]["eligible"]
    expected = {
        "verbal_only": "verbal_claim_only",
        "reset_missing": "missing_retained_source",
        "manual_source": "manual_or_audit_source",
        "reversed_source": "reversed_source",
        "cross_partition": "cross_partition",
    }
    for name, reason in expected.items():
        assert cases[name]["reason"] == reason
        assert not cases[name]["eligible"]
    assert cases["reset_missing"]["original_ref"] is None
    assert all(case["formal_trust"] == 0 for case in cases.values())
    assert all(case["production_observable_repair"] is None for case in cases.values())


def test_unknown_metrics_remain_unknown(report):
    assert report["summary"]["semantic_accuracy"] is None
    assert report["summary"]["production_repair_positive"] == ratio(0, 0)
    for section in ("original", "new_samples"):
        for result in report[section].values():
            for name in (
                "semantic_accuracy",
                "real_provider_tokens",
                "main_reply_p50_ms",
                "main_reply_p95_ms",
            ):
                assert result["metrics"][name] is None


def test_final_gate_supports_wrapped_assess_body_without_touching_wrapper(
    report, tmp_path
):
    original = getattr(AssessmentService, "_assess", AssessmentService.assess)
    source = textwrap.dedent(inspect.getsource(original))
    if original.__name__ == "assess":
        source = source.replace("async def assess(", "async def _assess(", 1)
    filename = "<offline wrapped assessment compatibility>"
    namespace = dict(vars(candidates.assessment_service))
    exec(compile(source, filename, "exec"), namespace)
    inner = namespace["_assess"]

    async def wrapper(self, interaction, *, directed, admission_state=None):
        return await self._assess(
            interaction, directed=directed, admission_state=admission_state
        )

    linecache.cache[filename] = (len(source), None, source.splitlines(True), filename)
    try:
        with (
            patch.object(AssessmentService, "_assess", inner, create=True),
            patch.object(AssessmentService, "assess", wrapper),
        ):
            result = asyncio.run(
                candidates.run_arm(
                    tmp_path / "wrapped", "quoted_evidence", candidates.FIXTURE
                )
            )
            assert AssessmentService.assess is wrapper
            assert AssessmentService._assess is inner
        control = report["new_samples"]["quoted_evidence"]
        assert (
            result["provider_transcript_sha256"]
            == control["provider_transcript_sha256"]
        )
        observed = [
            (row["message_id"], row["status"], row["delta"])
            for row in candidates.indexed_rows(result).values()
        ]
        expected = [
            (row["message_id"], row["status"], row["delta"])
            for row in candidates.indexed_rows(control).values()
        ]
        assert observed == expected
        assert sum(row["abstained"] for row in result["gates"]) == 13
    finally:
        linecache.cache.pop(filename, None)
