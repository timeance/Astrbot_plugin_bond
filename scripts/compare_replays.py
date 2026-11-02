"""比较两次完整回放；重复消息按序列位置对齐，不按消息 ID 覆盖。"""

import argparse
import json
from pathlib import Path


def compare(left, right, left_arm, right_arm):
    if left["fixture_sha256"] != right["fixture_sha256"]:
        raise ValueError("夹具或金标不同，不能直接作同输入对照")
    a, b = left["arms"][left_arm], right["arms"][right_arm]
    if [c["id"] for c in a["scenarios"]] != [c["id"] for c in b["scenarios"]]:
        raise ValueError("场景集合或顺序不同")
    changes, errors = [], []
    for old, new in zip(a["scenarios"], b["scenarios"]):
        if len(old["rows"]) != len(new["rows"]):
            raise ValueError("事件数量不同")
        changed = []
        for index, (x, y) in enumerate(zip(old["rows"], new["rows"])):
            if x["event_id"] != y["event_id"] or x["gold"] != y["gold"]:
                raise ValueError("事件身份或金标不同")
            if x["delta"] != y["delta"]:
                changed.append(
                    dict(
                        index=index,
                        message_id=y["message_id"],
                        before=x["delta"],
                        after=y["delta"],
                    )
                )
            kinds = []
            if not y["gold"]["should_update"] and y["changed"] and not x["changed"]:
                kinds.append("persistent_false_update")
            if (
                y["gold"]["direction"]["trust"] == "zero"
                and y["delta"]["trust"]
                and not x["delta"]["trust"]
            ):
                kinds.append("trust_false_update")
            if kinds:
                errors.append(dict(scenario=new["id"], index=index, kinds=kinds))
        if changed:
            changes.append(dict(id=new["id"], events=changed, final=new["final"]))
    return dict(
        fixture_sha256=left["fixture_sha256"],
        left=dict(
            commit=left["tested_commit"],
            arm=left_arm,
            rules=a["rules"],
            source_files_sha256=left["source_files_sha256"],
            metrics=a["metrics"],
        ),
        right=dict(
            commit=right["tested_commit"],
            arm=right_arm,
            rules=b["rules"],
            source_files_sha256=right["source_files_sha256"],
            metrics=b["metrics"],
        ),
        new_errors=errors,
        changed_scenarios=changes,
        limits="合成预置响应；不证明真实模型语义效果。无新错误不等于已消除既有错误。",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--left-arm", default="model-a")
    parser.add_argument("--right-arm", default="current")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(
        json.loads(args.left.read_text(encoding="utf-8")),
        json.loads(args.right.read_text(encoding="utf-8")),
        args.left_arm,
        args.right_arm,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                new_errors=result["new_errors"],
                changed={
                    c["id"]: len(c["events"]) for c in result["changed_scenarios"]
                },
            ),
            ensure_ascii=False,
        )
    )
    if result["new_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
