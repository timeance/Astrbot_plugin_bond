"""同一合成回放对比诊断开/关；Provider 响应与关系输入完全相同。"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_bond.tests import research_support as support
from astrbot_plugin_bond.services.diagnostics_service import DiagnosticRecorder
from astrbot_plugin_bond.core.taskregistry import TaskRegistry


async def compare(output):
    results, timings, stats = {}, {}, {}
    original = support.FavorService
    for name, enabled in (("off", False), ("on", True)):
        recorder = DiagnosticRecorder(
            output / name / "diagnostics", TaskRegistry(), enabled
        )

        def factory(*args, **kwargs):
            return original(*args, **kwargs, diagnostics=recorder)

        try:
            with patch.object(support, "FavorService", factory):
                results[name], timings[name] = await support.replay(
                    output / name / "runtime", ["current"]
                )
            await recorder.flush()
            stats[name] = recorder.stats()
        finally:
            await recorder.close()
    assert results["off"] == results["on"], "诊断改变了合成业务结果"
    assert not (output / "off" / "diagnostics").exists()
    latency = {}
    for arm in ("off", "on"):
        values = sorted(timings[arm]["current"]["offline_hook_ms"])
        latency[arm] = {
            "samples": len(values),
            "p50": values[math.ceil(len(values) * 0.5) - 1],
            "p95": values[math.ceil(len(values) * 0.95) - 1],
        }
    report = {
        "logical_results_identical": True,
        "fixture_sha256": results["on"]["fixture_sha256"],
        "metrics": results["on"]["arms"]["current"]["metrics"],
        "diagnostics": stats,
        "offline_hook_latency_ms": latency,
        "limits": "单机单次合成测量，无真实Provider或主回复；延迟不是性能保证。轮转/丢弃见diagnostics。",
    }
    (output / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "timings.json").write_text(
        json.dumps(timings, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("必须使用新的空输出目录")
    output.mkdir(parents=True, exist_ok=True)
    asyncio.run(compare(output))


if __name__ == "__main__":
    main()
