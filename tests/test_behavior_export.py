"""验证离线材料公平、可复现且未伪造模型验收结果。"""

import asyncio
import json
from collections import defaultdict

import pytest

from astrbot_plugin_bond.scripts.export_behavior_cases import export


@pytest.fixture
def material(tmp_path):
    return asyncio.run(export(tmp_path / "run"))


def grouped_cases(rows):
    groups = defaultdict(dict)
    for row in rows:
        groups[(row["case_id"], row["persona_id"])][row["arm"]] = row
    return groups


def test_behavior_export_has_three_arms_and_real_persisted_stories(material, tmp_path):
    assert material["real_llm_tested"] is False
    for group in grouped_cases(material["cases"]).values():
        assert set(group) == {"no_bond", "old_prompt", "new_prompt"}
    for story in material["persisted_stories"]:
        assert story["restart_verified"] is True
        assert story["persisted_after_restart"] != story["initial"]
        assert story["steps"][0]["before"] == story["initial"]
        assert all(step["synthetic_verdict"] for step in story["steps"])
        for previous, current in zip(story["steps"], story["steps"][1:]):
            assert current["before"] == previous["after"]
        last = story["final_requests"][-1]["messages"][0]["content"]
        first = story["steps"][0]["requests"][-1]["messages"][0]["content"]
        assert last != first
        for index, step in enumerate(story["steps"]):
            histories = [row["messages"][1:-1] for row in step["requests"]]
            assert histories[0] == histories[1] == histories[2]
            assert len(histories[0]) == index
        assert len(story["final_requests"][0]["messages"][1:-1]) == len(story["steps"])
    for name in ("friendly.sqlite", "conflict.sqlite"):
        assert (tmp_path / "run" / name).read_bytes().startswith(b"SQLite format 3")
    parsed = json.loads((tmp_path / "run/cases.json").read_text(encoding="utf-8"))
    assert parsed == material
    with pytest.raises(ValueError):
        asyncio.run(export(tmp_path / "run"))


def test_all_arms_receive_identical_history_and_current_speaker(material):
    groups = grouped_cases(material["cases"])
    for (case_id, persona_id), group in groups.items():
        plain = group["no_bond"]["messages"]
        assert plain[0]["content"].startswith(material["personas"][persona_id])
        assert "[Bond" not in plain[0]["content"]
        for arm in ("old_prompt", "new_prompt"):
            treated = group[arm]["messages"]
            assert treated[1:] == plain[1:]
            assert treated[0]["content"].startswith(plain[0]["content"] + "\n\n[Bond")
        if case_id == "shared_topic":
            assert any("两个结局" in message["content"] for message in plain[1:-1])
        if case_id == "multi_speaker":
            platform = json.loads(plain[0]["content"].split("\n")[-1])
            assert platform["current_user_id"] == "8001"
            assert (
                platform["display_names"]["8001"] == platform["display_names"]["8002"]
            )
            assert platform["history_user_ids"] == ["8001", None, "8002", "8001"]
            assert "刚进群" in plain[3]["content"]


def test_required_personas_relationship_and_real_error_are_available(material):
    groups = grouped_cases(material["cases"])
    scenarios = {scenario["id"]: scenario for scenario in material["scenarios"]}
    for case_id in scenarios:
        assert (case_id, "reserved") in groups
        assert (case_id, "warm") in groups
    assert "温柔委婉" in material["personas"]["warm"]
    assert "克制" in material["personas"]["reserved"]
    familiarity, affinity, trust = scenarios["distant_reliable"]["synthetic_state"]
    assert familiarity >= 70 and affinity < 0 and trust > 0
    criticism = groups[("criticism", "reserved")]["no_bond"]["messages"]
    assert criticism[-2] == {"role": "assistant", "content": "7 乘 8 等于 54。"}
    assert "算错" in criticism[-1]["content"]
    assert ("multi_speaker", "reserved") in groups
    # 额外缺口只覆盖要求的两种人格，保留原场景的活泼与正式人格。
    assert ("multi_speaker", "formal") not in groups
    assert ("help", "playful") in groups and ("help", "formal") in groups


def test_unexecuted_metadata_scores_and_pair_reviews_stay_empty(material):
    assert material["baseline_commit"] == "91caea9b460009d65daa83a086dfaf457f3787bd"
    assert material["baseline_tree"] == "9d6473464d351af84d3c7db2afcdbea3d677b594"
    config = material["evaluation_config"]
    assert all(value is None for value in config["model"].values())
    assert all(value is None for value in config["sampling"].values())
    assert config["repeat_count"] is None and config["executed_at"] is None
    groups = grouped_cases(material["cases"])
    assert set(material["pair_verdicts"]) == {"win", "tie", "loss", "uncertain"}
    for comparison in material["pair_comparisons"]:
        group = groups[(comparison["case_id"], comparison["persona_id"])]
        assert comparison["left_arm"] == "new_prompt"
        assert (
            comparison["right_arm"] in group and comparison["right_arm"] != "new_prompt"
        )
        assert comparison["verdict"] is None and comparison["reason"] == ""
        assert comparison["repeat_index"] is None
    assert len(material["pair_comparisons"]) == 2 * len(groups)
    rows = list(material["cases"])
    for story in material["persisted_stories"]:
        rows.extend(story["final_requests"])
        for step in story["steps"]:
            rows.extend(step["requests"])
    for row in rows:
        assert row["response"] is None and row["repeat_index"] is None
        assert all(value is None for value in row["response_lengths"].values())
        assert all(value is None for value in row["scores"].values())
        assert {"persistent_identity_assumption", "internal_mechanism_leak"} <= row[
            "scores"
        ].keys()
        lengths = row["request_lengths"]
        contents = [message["content"] for message in row["messages"]]
        assert lengths["total_content_chars"] == sum(map(len, contents))
        assert lengths["total_content_utf8_bytes"] > lengths["total_content_chars"]
        assert lengths["tokens"] is None and lengths["tokenizer"] is None


def test_behavior_export_is_reproducible_without_network(tmp_path):
    one = asyncio.run(export(tmp_path / "one"))
    two = asyncio.run(export(tmp_path / "two"))
    assert one == two
