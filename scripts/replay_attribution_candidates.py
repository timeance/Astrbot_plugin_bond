"""真实离线回放的最终落分候选；只在内存注入，不修改生产或模型响应。"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import inspect
import json
import sys
import textwrap
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_bond.core.assessment import SYSTEM_PROMPT
from astrbot_plugin_bond.services import assessment_service
from astrbot_plugin_bond.tests import research_support as replay_support
from astrbot_plugin_bond.scripts import replay_trust_evidence as shadow

FIXTURE = ROOT / "tests/fixtures/research/attribution_candidates.json"
BASE_COMMIT = "d287221c241fe88740ab2186a8ffa63676d90dcf"
ARMS = ("control", "quoted_evidence", "joke_abstain", "manipulation_no_source")
ORIGINAL_FIXTURE = replay_support.FIXTURE


def quote_geometry(text, evidence):
    """仅识别最小双引号句法；区间不是作者或事实归属。"""
    occurrences = []
    start = text.find(evidence)
    while start >= 0:
        occurrences.append([start, start + len(evidence)])
        start = text.find(evidence, start + 1)
    spans, opening = [], None
    ambiguous = False
    for pos, char in enumerate(text):
        if char in "「」『』":
            ambiguous = True
        elif char == "“":
            if opening is not None:
                ambiguous = True
            else:
                opening = ("“", pos)
        elif char == "”":
            if opening is None or opening[0] != "“":
                ambiguous = True
            else:
                spans.append([opening[1] + 1, pos])
                opening = None
        elif char == '"':
            if pos and text[pos - 1] == "\\":
                ambiguous = True
            elif opening is None:
                opening = ('"', pos)
            elif opening[0] == '"':
                spans.append([opening[1] + 1, pos])
                opening = None
            else:
                ambiguous = True
    ambiguous = ambiguous or opening is not None
    overlap = [
        occurrence
        for occurrence in occurrences
        if any(occurrence[0] < end and start < occurrence[1] for start, end in spans)
    ]
    reason = (
        "ambiguous_quote_syntax"
        if ambiguous
        else "quoted_or_ambiguous_evidence" if overlap else "outside_supported_quotes"
    )
    return dict(
        abstain=ambiguous or bool(overlap),
        reason=reason,
        occurrences=occurrences,
        quoted_spans=spans,
    )


class CandidateGate:
    def __init__(self, arm):
        self.arm, self.records = arm, []

    def __call__(self, verdict, interaction):
        blocked, reason, geometry = False, "pass", None
        if self.arm == "quoted_evidence":
            geometry = quote_geometry(interaction.text, verdict.evidence)
            blocked, reason = geometry["abstain"], geometry["reason"]
        elif self.arm == "joke_abstain" and verdict.event_type == "joke":
            blocked, reason = True, "all_joke_policy"
        elif (
            self.arm == "manipulation_no_source"
            and verdict.event_type == "manipulation"
        ):
            # 真实入口没有独立诊断来源；绝不把 gold 或模型类别当作来源。
            blocked, reason = True, "independent_diagnostic_source_unavailable"
        self.records.append(
            dict(
                event_id=interaction.event_id,
                event_type=verdict.event_type,
                text=interaction.text,
                evidence=verdict.evidence,
                confidence=verdict.confidence,
                abstained=blocked,
                reason=reason,
                geometry=geometry,
            )
        )
        return blocked


@contextmanager
def final_gate(gate):
    """给当前真实 assess 的最终弃权条件追加候选；不改 _call、复核或写入服务。"""
    service = assessment_service.AssessmentService
    method_name = "_assess" if hasattr(service, "_assess") else "assess"
    original = getattr(service, "_assess", service.assess)
    tree = ast.parse(textwrap.dedent(inspect.getsource(original)))
    if (
        len(tree.body) != 1
        or not isinstance(tree.body[0], ast.AsyncFunctionDef)
        or tree.body[0].name != method_name
    ):
        raise RuntimeError("真实评估方法名或结构已变化，停止候选注入")
    method = tree.body[0]
    targets = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.If)
        and any(
            isinstance(child, ast.Compare)
            and isinstance(child.left, ast.Attribute)
            and isinstance(child.left.value, ast.Name)
            and child.left.value.id == "verdict"
            and child.left.attr == "event_type"
            and any(
                isinstance(value, ast.Constant) and value.value == "uncertain"
                for value in child.comparators
            )
            for child in ast.walk(node.test)
        )
    ]
    if len(targets) != 1:
        raise RuntimeError("真实 assess 最终弃权位置已变化，停止候选注入")
    target = targets[0]
    if ast.unparse(target.body[0]) != "status = 'abstained'":
        raise RuntimeError("真实 assess 最终状态契约已变化")
    target.test = ast.BoolOp(
        op=ast.Or(),
        values=[
            target.test,
            ast.Call(
                func=ast.Name(id="_candidate_gate", ctx=ast.Load()),
                args=[
                    ast.Name(id="verdict", ctx=ast.Load()),
                    ast.Name(id="interaction", ctx=ast.Load()),
                ],
                keywords=[],
            ),
        ],
    )
    namespace = dict(vars(assessment_service), _candidate_gate=gate)
    exec(
        compile(ast.fix_missing_locations(tree), "<offline final gate>", "exec"),
        namespace,
    )
    with patch.object(service, method_name, namespace[method_name]):
        yield


def protected_hashes():
    names = [
        p
        for folder in ("core", "api", "services", "storage")
        for p in (ROOT / folder).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    names += [
        ROOT / "main.py",
        ROOT / "_conf_schema.json",
        ORIGINAL_FIXTURE,
        ROOT / "tests/research_support.py",
        shadow.FIXTURE,
        ROOT / "scripts/replay_trust_evidence.py",
    ]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in names
    }


async def run_arm(runtime, arm, fixture):
    gate, transcript = CandidateGate(arm), []
    original_call = replay_support.World.call

    async def audited_call(world, bot, tier, prompt):
        payload = json.loads(prompt.rsplit("\n输入：", 1)[1])
        assert set(payload) == {"target_bot", "sender", "text"}
        assert prompt == SYSTEM_PROMPT + "\n输入：" + json.dumps(
            payload, ensure_ascii=False
        )
        plan = world.plans[(bot, payload["sender"], payload["text"])]
        record = dict(
            message_id=plan["message_id"],
            bot=bot,
            tier=tier,
            payload=payload,
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        )
        transcript.append(record)
        try:
            response = await original_call(world, bot, tier, prompt)
        except BaseException as exc:
            record["outcome"] = type(exc).__name__
            raise
        record["completion_text"] = response.completion_text
        return response

    with (
        patch.object(replay_support, "FIXTURE", fixture),
        patch.object(replay_support.World, "call", audited_call),
        final_gate(gate),
    ):
        report, _ = await replay_support.replay(runtime, arms=("current",))
    result = report["arms"]["current"]
    result.update(
        gates=gate.records,
        provider_transcript=transcript,
        provider_transcript_sha256=replay_support.digest(
            sorted(transcript, key=replay_support.digest)
        ),
        fixture_sha256=report["fixture_sha256"],
    )
    return result


def indexed_rows(result):
    return {
        (case["id"], index): row
        for case in result["scenarios"]
        for index, row in enumerate(case["rows"])
    }


def comparison(control, candidate):
    left, right = indexed_rows(control), indexed_rows(candidate)
    assert left.keys() == right.keys()
    for key in left:
        assert left[key]["gold"] == right[key]["gold"]
        assert left[key]["text"] == right[key]["text"]
        assert left[key]["message_id"] == right[key]["message_id"]
    forbidden = [key for key, row in left.items() if not row["gold"]["should_update"]]
    expected = [key for key, row in left.items() if row["gold"]["should_update"]]
    expected_written = [key for key in expected if left[key]["changed"]]
    negative = [
        key
        for key, row in left.items()
        if row["gold"]["direction"]["trust"] == "negative"
    ]
    suppressed = [key for key in expected_written if not right[key]["changed"]]
    changed = [
        dict(
            scenario=key[0],
            index=key[1],
            message_id=left[key]["message_id"],
            should_update=left[key]["gold"]["should_update"],
            before=left[key]["delta"],
            after=right[key]["delta"],
            status=right[key]["status"],
        )
        for key in left
        if left[key]["delta"] != right[key]["delta"]
    ]
    return dict(
        metrics=candidate["metrics"],
        prevented_false_updates=replay_support.ratio(
            sum(left[k]["changed"] and not right[k]["changed"] for k in forbidden),
            len(forbidden),
        ),
        suppressed_expected_updates=replay_support.ratio(
            len(suppressed), len(expected)
        ),
        suppressed_baseline_expected_writes=replay_support.ratio(
            len(suppressed), len(expected_written)
        ),
        suppressed_expected_negative_trust=replay_support.ratio(
            sum(
                left[k]["delta"]["trust"] < 0 <= right[k]["delta"]["trust"]
                for k in negative
            ),
            len(negative),
        ),
        new_false_updates=[
            list(k) for k in forbidden if right[k]["changed"] and not left[k]["changed"]
        ],
        changed_rows=changed,
        provider_transcript_unchanged=(
            candidate["provider_transcript_sha256"]
            == control["provider_transcript_sha256"]
        ),
        abstained_gate_count=sum(r["abstained"] for r in candidate["gates"]),
    )


def observability_contracts(fixture):
    cases = {case["id"]: case for case in fixture["scenarios"]}
    groups = [
        [
            "S11-joke-stranger",
            "S12-joke-close",
            "S13-joke-distant",
            "S14-joke-boundary",
        ],
        ["S22-test-injection-abstain", "S23-test-injection-misclassified"],
    ]
    result = []
    for ids in groups:
        selected = [cases[name] for name in ids]
        payloads = [
            dict(
                target_bot=case["steps"][0].get("bot", "10001"),
                sender=case["steps"][0].get("user", "8001"),
                text=case["steps"][0]["text"],
            )
            for case in selected
        ]
        result.append(
            dict(
                scenarios=ids,
                observations=len(ids),
                unique_provider_payloads=len(
                    {replay_support.digest(p) for p in payloads}
                ),
                expected_updates=[
                    case["steps"][0]["gold"]["should_update"] for case in selected
                ],
                scripted_event_types=[
                    case["steps"][0]["response"]["event_type"] for case in selected
                ],
                labels_are_external_semantic_assumptions=True,
            )
        )
    result.append(
        dict(
            scenarios=["S12-joke-close", "S14-joke-boundary"],
            identical_initial_state=(
                cases["S12-joke-close"]["initial"]
                == cases["S14-joke-boundary"]["initial"]
            ),
            boundary_source_in_provider_input=False,
        )
    )
    return result


def repair_link(case, retained):
    """来源有效性先于独立合成标签；既不解析正文，也不从 T 推断标签。"""
    by_event = {source["event_key"]: source for source in retained["inventory"]}
    claim = case["link"]
    original = by_event.get(claim["original"])
    repair = by_event.get(claim["repair"])
    reason = "eligible_synthetic_upper_bound"
    selected = retained["subject"]
    if original is None or repair is None:
        reason = "missing_retained_source"
    elif any(
        source["event_key"].rsplit("/", 1)[0] != selected
        for source in (original, repair)
    ):
        reason = "cross_partition"
    elif any(source["row"]["source"] != "judge" for source in (original, repair)):
        reason = "manual_or_audit_source"
    elif any(source["row"]["reversed"] for source in (original, repair)):
        reason = "reversed_source"
    elif repair["row"]["ts"] <= original["row"]["ts"]:
        reason = "noncausal_order"
    elif not claim["independent_confirmation"]:
        reason = "verbal_claim_only"
    elif (
        case["annotations"].get(claim["original"]) != "negative"
        or case["annotations"].get(claim["repair"]) != "positive"
    ):
        reason = "missing_external_diagnostic_annotation"
    return dict(
        id=case["id"],
        eligible=reason == "eligible_synthetic_upper_bound",
        reason=reason,
        original_ref=original["ref"] if original else None,
        repair_ref=repair["ref"] if repair else None,
        claim=claim,
        production_observable_repair=None,
        production_repair_positive_denominator=0,
    )


async def repair_contracts(runtime, fixture):
    rules = json.loads(shadow.FIXTURE.read_text(encoding="utf-8"))["historical_rules"]
    results = []
    for case in fixture["repair_cases"]:
        retained = await shadow.run_case(runtime / case["id"], case, rules)
        before = replay_support.digest(retained)
        link = repair_link(case, retained)
        assert replay_support.digest(retained) == before
        results.append(
            dict(
                **link,
                expected_eligible=case["expected_eligible"],
                source_sha256=retained["production_derived"]["source_sha256"],
                sources=retained["inventory"],
                analysis_sqlite_changes=retained["analysis_sqlite_changes"],
                source_contracts=retained["checks"],
                formal_trust=retained["formal_relation"]["trust"],
            )
        )
    old = await shadow.experiment(runtime / "existing-shadow")
    return dict(
        cases=results,
        contract_pass=replay_support.ratio(
            sum(c["eligible"] == c["expected_eligible"] for c in results), len(results)
        ),
        zero_analysis_writes=replay_support.ratio(
            sum(c["analysis_sqlite_changes"] == 0 for c in results), len(results)
        ),
        existing_shadow_checks=old["checks"],
        existing_shadow_case_count=len(old["cases"]),
        production_positive_repair=replay_support.ratio(0, 0),
        semantic_gain=None,
        persistent_false_update_gain=None,
    )


async def experiment(runtime):
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=False)
    protected = protected_hashes()
    original = replay_support.load_fixture()
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    full = dict(original={}, new_samples={})
    for name, fixture_path in (
        ("original", ORIGINAL_FIXTURE),
        ("new_samples", FIXTURE),
    ):
        for arm in ARMS:
            full[name][arm] = await run_arm(runtime / name / arm, arm, fixture_path)
    baseline = full["original"]["control"]
    assert baseline["metrics"]["events"] == 1201
    assert baseline["metrics"]["persistent_false_update_rate"] == replay_support.ratio(
        6, 381
    )
    assert baseline["metrics"]["trust_false_update_rate"] == replay_support.ratio(
        2, 1195
    )
    summary = dict(
        format_version=1,
        base_commit=BASE_COMMIT,
        evaluation="synthetic_scripted_provider_routing_and_real_sqlite_only",
        original_fixture_sha256=replay_support.digest(original),
        candidate_fixture_sha256=replay_support.digest(fixture),
        candidate_source_sha256={
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "scripts/replay_attribution_candidates.py",
                "tests/test_attribution_candidates.py",
            )
        },
        original_denominators=dict(events=1201, forbidden=381, zero_trust=1195),
        original={
            arm: comparison(baseline, result)
            for arm, result in full["original"].items()
        },
        new_samples={
            arm: comparison(full["new_samples"]["control"], result)
            for arm, result in full["new_samples"].items()
        },
        observability=observability_contracts(original),
        repair=await repair_contracts(runtime / "repair", fixture),
        protected_source_sha256=protected,
        source_files_unchanged=protected == protected_hashes(),
        semantic_accuracy=None,
        production_repair_positive=replay_support.ratio(0, 0),
        production_quote_recommendation="defer_quote_gate_semantics_not_identifiable_from_quote_geometry",
    )
    assert summary["source_files_unchanged"]
    for section in ("original", "new_samples"):
        assert all(
            row["provider_transcript_unchanged"] for row in summary[section].values()
        )
    full["summary"] = summary
    return full


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("输出目录必须不存在或为空；不会删除已有产物")
    output.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(experiment(output / "runtime"))
    for name, data in (("results.json", report), ("summary.json", report["summary"])):
        (output / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "original": {
                    arm: {
                        "PFUR": row["metrics"]["persistent_false_update_rate"],
                        "suppressed": row["suppressed_expected_updates"],
                        "Trust": row["metrics"]["trust_false_update_rate"],
                    }
                    for arm, row in report["summary"]["original"].items()
                },
                "new_quote": report["summary"]["new_samples"]["quoted_evidence"][
                    "suppressed_expected_updates"
                ],
                "repair": report["summary"]["repair"]["contract_pass"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
