"""回放仪器和现状复现；已知缺陷并不等于产品验收通过。"""

import asyncio
import shutil
from copy import deepcopy

import pytest

from astrbot_plugin_bond.tests.research_support import load_fixture, replay, ratio


@pytest.fixture(scope="module")
def comparison(tmp_path_factory):
    runtime = tmp_path_factory.mktemp("replay")
    try:
        yield asyncio.run(replay(runtime))[0]
    finally:
        # 结果已在内存中；只清理本 fixture 创建的合成库，避免整套测试占满磁盘。
        shutil.rmtree(runtime)


def cases(report, arm):
    return {case["id"]: case for case in report["arms"][arm]["scenarios"]}


def test_fixture_covers_contract_and_separates_gold_from_provider():
    fixture = load_fixture()
    assert len(fixture["scenarios"]) >= 32
    tags = set().union(*(set(c["tags"]) for c in fixture["scenarios"]))
    assert {"A1", "A2", "attribution", "temporary", "privacy", "repair", "busy"} <= tags
    for case in fixture["scenarios"]:
        for step in case["steps"]:
            if step.get("action", "message") == "message":
                assert "gold" in step and "response" in step
                assert "score" not in step["gold"]


def test_metrics_have_explicit_denominators(comparison):
    assert ratio(0, 0) == dict(numerator=0, denominator=0, value=None)
    assert ratio(1, 4)["value"] == 0.25
    for arm in comparison["arms"].values():
        rows = [r for c in arm["scenarios"] for r in c["rows"]]
        expected = [r for r in rows if not r["gold"]["should_update"]]
        metric = arm["metrics"]["persistent_false_update_rate"]
        assert metric == ratio(sum(r["changed"] for r in expected), len(expected))
        assert arm["metrics"]["main_reply_p95_ms"] is None
        assert arm["metrics"]["real_provider_tokens"] is None
        assert arm["metrics"]["semantic_accuracy"] is None


def test_model_a_changes_only_three_trust_defaults(comparison):
    baseline = deepcopy(comparison["arms"]["baseline"]["rules"])
    for event in ("thanks", "friendly", "apology"):
        baseline[event]["trust"] = 0
    assert baseline == comparison["arms"]["model-a"]["rules"]
    for result in comparison["arms"]["model-a"]["scenarios"]:
        for row in result["rows"]:
            if row["gold"]["social_act"] in {
                "thanks",
                "friendly",
                "apology",
                "greeting",
            }:
                assert row["delta"]["trust"] == 0


def test_structural_conflicts_abstain_but_text_only_quote_risk_remains(comparison):
    baseline = cases(comparison, "baseline")
    for name in (
        "S04-reply-at-conflict",
        "S06-multiple-targets",
        "S07-missing-reply-source",
    ):
        for arm in comparison["arms"]:
            row = cases(comparison, arm)[name]["rows"][0]
            assert not row["gold"]["should_update"]
            assert not row["changed"] and not row["provider_called"], name
    assert baseline["S03-quoted-attack"]["rows"][0]["changed"]
    for name in (
        "S01-other-conversation",
        "S02-reply-other",
        "S05-other-bot-target",
        "S08-known-bot-sender",
        "S09-self-sender",
    ):
        assert not baseline[name]["rows"][0]["changed"]


def test_50_100_200_day_actual_deltas_follow_model_and_stage_curve(comparison):
    baseline = cases(comparison, "baseline")
    for days in (50, 100, 200):
        # 旧样本的 Provider 固定把寒暄分类为 friendly；框架现在必须执行模型建议。
        # 这不是说真实模型应该奖励打卡，也不把 gold 偷渡到 Provider 响应。
        for prefix in ("S18-greeting", "S19-variants"):
            rows = baseline[f"{prefix}-{days}d"]["rows"]
            assert len(rows) == days
            assert all(r["provider_called"] for r in rows)
            assert all(r["delta"]["familiarity"] > 0 for r in rows)
            assert all(r["changed"] for r in rows)
    questions = baseline["S20-questions-200d"]["rows"]
    assert sum(r["delta"]["familiarity"] for r in questions) == pytest.approx(26.01)
    assert all(r["delta"]["affinity"] == r["delta"]["trust"] == 0 for r in questions)


def test_repair_and_reoffense_have_distinct_but_limited_meaning(comparison):
    expected = {
        "baseline": ([38, 39, 39], [38, 39, 37]),
        "model-a": ([38, 38, 38], [38, 38, 36]),
    }
    for arm, (repair, repeat) in expected.items():
        data = cases(comparison, arm)
        assert [
            r["after"]["trust"] for r in data["S26-violation-apology-repair"]["rows"]
        ] == repair
        assert [
            r["after"]["trust"] for r in data["S27-violation-apology-repeat"]["rows"]
        ] == repeat
        assert data["S26-violation-apology-repair"]["rows"][-1]["known_limit"]
        assert data["S48-repair-claim-unknown"]["rows"][0]["status"] == "abstained"


def test_joke_classification_is_scripted_but_numeric_stage_is_state_dependent(
    comparison,
):
    data = cases(comparison, "baseline")
    deltas = [
        data[name]["rows"][0]["delta"]
        for name in (
            "S11-joke-stranger",
            "S12-joke-close",
            "S13-joke-distant",
            "S14-joke-boundary",
        )
    ]
    assert all(
        d["familiarity"] > 0 and d["affinity"] > 0 and d["trust"] == 0 for d in deltas
    )
    # 类别仍由预置 Provider 统一给出；分段倍率只改变数字，不证明识别了玩笑的关系情境。
    assert deltas[0]["familiarity"] > deltas[1]["familiarity"]
    assert deltas[1] == deltas[3]


def test_failures_dedup_and_temporary_results_do_not_write(comparison):
    for arm in comparison["arms"]:
        data = cases(comparison, arm)
        for number in range(29, 36):
            case = next(c for key, c in data.items() if key.startswith(f"S{number}-"))
            assert not any(r["changed"] for r in case["rows"])
        assert not data["S37-inflight-revision"]["rows"][0]["changed"]
        assert not data["S40-restart-dedup"]["rows"][-1]["changed"]
        # 三条不同稳定 ID 均在 300 秒 / 5 次窗口内；旧长冷却不再拦截。
        assert all(
            r["provider_called"] and r["changed"] for r in data["S41-cooldown"]["rows"]
        )
        assert [r["changed"] for r in data["S36-temporary-expiry"]["rows"]] == [
            False,
            False,
            True,
        ]
        busy = data["S46-concurrent-busy"]["rows"]
        assert sum(r["status"] == "busy" for r in busy) == 10
        assert sum(r["provider_called"] for r in busy) == 2


def test_isolation_and_persona_composition(comparison):
    data = cases(comparison, "model-a")
    assert len(data["S39-bot-group-isolation"]["final"]) == 3
    assert len(data["S38-scope-roundtrip"]["final"]) == 2
    assert len(data["S10-same-nickname"]["final"]) == 2
    blocks = data["S43-persona-global-privacy"]["blocks"]
    assert len(blocks) == 2
    assert all("私密合成印象" not in b["block"] for b in blocks)
    assert all(b["block"].startswith(b["persona"]) for b in blocks)


def test_repeat_run_is_deterministic(tmp_path):
    selected = {
        "S26-violation-apology-repair",
        "S38-scope-roundtrip",
        "S36-temporary-expiry",
        "S46-concurrent-busy",
        "S19-variants-50d",
    }
    first = asyncio.run(replay(tmp_path / "one", selected=selected))[0]
    second = asyncio.run(replay(tmp_path / "two", selected=selected))[0]
    assert first == second


def test_quotes_and_decay_remain_separate_from_target_and_event_writes(comparison):
    for arm in comparison["arms"]:
        data = cases(comparison, arm)
        for name in (
            "S03-quoted-attack",
            "S47-quote-confident-thanks",
        ):
            assert data[name]["rows"][0]["changed"]
        silence = data["S44-silence-200d"]["rows"]
        assert all(not r["changed"] for r in silence)
        assert silence[-1]["after"]["affinity"] < silence[0]["after"]["affinity"]
        assert (
            comparison["arms"][arm]["metrics"]["quote_false_update_rate"]["numerator"]
            > 0
        )
