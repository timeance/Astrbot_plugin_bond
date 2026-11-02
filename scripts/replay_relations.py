"""离线合成回放，不访问真实 Provider；报告与临时数据库写入指定目录。"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_bond.tests.research_support import replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arms",
        nargs="+",
        choices=("baseline", "model-a", "current"),
        default=["baseline", "model-a"],
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", action="append")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / "runtime"
    if runtime.exists():
        parser.error("runtime 已存在；请使用新的输出目录以保持空库回放")
    report, timings = asyncio.run(replay(runtime, args.arms, args.scenario))
    report["tested_commit"] = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    report["source_files_sha256"] = {}
    import hashlib

    for folder in ("core", "api", "services", "storage"):
        for path in sorted((ROOT / folder).glob("*.py")):
            report["source_files_sha256"][path.relative_to(ROOT).as_posix()] = (
                hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            )
    for filename in ("main.py", "_conf_schema.json"):
        report["source_files_sha256"][filename] = hashlib.sha256(
            (ROOT / filename).read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
    for name, value in (("results.json", report), ("timings.json", timings)):
        (output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    lines = [
        "# 合成回放对照",
        "",
        "固定模型响应只验证路由、策略与持久化；不代表真实语义准确率或生产效果。",
        "baseline 指冻结的旧规则；路由始终来自本次检出的源码。",
        "",
        "| 实验臂 | 事件 | 错误持久化 | 受话人误更新 | Trust 误更新 | 调用 |",
        "| --- | ---: | --- | --- | --- | ---: |",
    ]

    def fraction(value):
        return f'{value["numerator"]}/{value["denominator"]}'

    for arm, result in report["arms"].items():
        m = result["metrics"]
        lines.append(
            f'| {arm} | {m["events"]} | {fraction(m["persistent_false_update_rate"])} | '
            f'{fraction(m["target_false_update_rate"])} | {fraction(m["trust_false_update_rate"])} | {m["provider_calls"]} |'
        )
    lines += [
        "",
        "真实 Provider Token、真人准确率、主回复 p50/p95 均未知。",
        "逐事件状态、增量、称谓和分母见 results.json；非确定性入口耗时另见 timings.json。",
    ]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
