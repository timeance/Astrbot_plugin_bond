"""生成词典与125格对照；可复用已有回放轨迹，不修改分值或调用模型。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_bond.core.relation_policy import RelationResolver, RelationState
from astrbot_plugin_bond.core.relation_lexicon import (
    BAND_EDGES,
    BAND_LABELS,
    CELLS,
    LEXICON_VERSION,
    TERMS,
)

CENTERS = ((0, 15, 30, 50, 85), (-70, -25, 0, 25, 70), (-70, -25, 0, 25, 70))


def compare(replay=None):
    old, new = RelationResolver(thresholds={}), RelationResolver()
    cells = []
    for f, a, t in product(range(5), repeat=3):
        state = RelationState(*(CENTERS[i][band] for i, band in enumerate((f, a, t))))
        description = new.describe(state)
        cells.append(
            dict(
                bands=[f, a, t],
                labels=list(description.labels),
                old=old.resolve(state),
                key=description.key,
                title=description.title,
                meaning=description.meaning,
            )
        )
    trajectories = []
    if replay:
        # 保存轨迹已经过真实服务/SQLite链；只重算展示，不补造事件或金标。
        data = json.loads(Path(replay).read_text(encoding="utf-8"))
        arm = data["arms"].get("current", data["arms"].get("model-a"))
        for scenario in arm["scenarios"]:
            old_flips = new_flips = scored = 0
            changed_words = []
            for row in scenario["rows"]:
                if "before" not in row or "after" not in row:
                    continue
                before, after = RelationState(**row["before"]), RelationState(
                    **row["after"]
                )
                old_pair = [old.resolve(before), old.resolve(after)]
                new_pair = [new.resolve(before), new.resolve(after)]
                old_flips += old_pair[0] != old_pair[1]
                new_flips += new_pair[0] != new_pair[1]
                scored += 1
                if old_pair != new_pair:
                    changed_words.append(
                        dict(
                            message_id=row.get("message_id"), old=old_pair, new=new_pair
                        )
                    )
            trajectories.append(
                dict(
                    id=scenario["id"],
                    events=scored,
                    old_flips=old_flips,
                    new_flips=new_flips,
                    old_per_100=100 * old_flips / scored if scored else None,
                    new_per_100=100 * new_flips / scored if scored else None,
                    changes=changed_words,
                )
            )
    return dict(
        lexicon_version=LEXICON_VERSION,
        terms=len(TERMS),
        cells=cells,
        edges=BAND_EDGES,
        trajectories=trajectories,
        replay_sha256=(
            hashlib.sha256(Path(replay).read_bytes()).hexdigest() if replay else None
        ),
        limits="仅比较固定状态的展示；翻转包含合理变化，不是错误震荡率。真人贴切度未测。",
    )


def markdown(report):
    lines = [
        "# Bond 关系词典",
        "",
        "由 scripts/compare_relation_lexicon.py 从唯一词典源生成。词典版本 "
        + LEXICON_VERSION
        + "。",
        "",
        "F/A/T 保持原范围和计算；以下只描述当前 Bot 的态度，不断言对方感受或历史事实。",
        "零值处于亲近/信任中档；信任中档不区分无证据、抵消或陈旧。高信任称谓不是可靠性证明。",
        "",
        "F 边界 10/25/40/70，边界值进高档；A/T 边界 -40/-10/10/40。",
        "A/T 的 -40 留在最低档（保持既有负边界），-10/10/40 进高档。无历史滞回或称谓冷却。",
        "",
        "## 词条",
        "",
        "| 编号 | 称谓 | 释义 |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        f"| {key} | {title} | {meaning} |" for key, (title, meaning) in TERMS.items()
    )
    for f, label in enumerate(BAND_LABELS[0]):
        lines += [
            "",
            "## F：" + label,
            "",
            "| A \\ T | " + " | ".join(BAND_LABELS[2]) + " |",
            "| --- | " + " | ".join(["---"] * 5) + " |",
        ]
        for a, alabel in enumerate(BAND_LABELS[1]):
            lines.append(
                "| "
                + alabel
                + " | "
                + " | ".join(TERMS[key][0] for key in CELLS[f][a])
                + " |"
            )
    lines += [
        "",
        "## 展示与兼容",
        "",
        "公开接口显示称谓；模型接收称谓、释义及三维分档，不注入数值。",
        "人格、权限、主动发言和回复频率保持宿主约束。无关系schema或历史改分。",
        "显式调用 RelationResolver(thresholds=..., labels=...) 继续使用旧三档规则。",
        "默认 RelationResolver() 使用新词典；RelationResolver(thresholds={}) 可用于旧版离线对照。",
        "回滚代码恢复旧称谓，无需反向迁移或重算流水。",
        "",
        "## 回放边界",
        "",
        report["limits"],
    ]
    if report["trajectories"]:
        lines += [
            "",
            "| 场景 | 事件 | 旧称谓变化 | 新称谓变化 |",
            "| --- | ---: | ---: | ---: |",
        ]
        lines.extend(
            f'| {r["id"]} | {r["events"]} | {r["old_flips"]} | {r["new_flips"]} |'
            for r in report["trajectories"]
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = compare(args.replay)
    (args.output / "lexicon.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "RELATION_LEXICON.md").write_text(markdown(report), encoding="utf-8")
    print(
        f'{report["terms"]}词条，{len(report["cells"])}组合，{len(report["trajectories"])}条轨迹；未调用Provider。'
    )


if __name__ == "__main__":
    main()
