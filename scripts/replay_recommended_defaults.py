"""对照发布默认值：固定时钟、合成事件、真实服务与 SQLite，不调用 Provider。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_bond.core.level_economy import EconomyConfig
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.settings import snapshot
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

START = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
SCENARIOS = {
    "positive_burst": [(0, i * 180, "friendly", 1) for i in range(10)],
    "neutral_3_days": [
        (day, i * 180, kind, 1)
        for day in range(3)
        for i, kind in enumerate(["normal_question"] * 6 + ["criticism"])
    ],
    "negative_mistake": [(0, 0, "insult", 1), (0, 180, "friendly", 1)],
    "violation_apology_repeat": [
        (0, 0, "insult", 2),
        (0, 180, "apology", 1),
        (1, 0, "friendly", 1),
        (2, 0, "insult", 2),
    ],
}


def parameters():
    values = snapshot({})["values"]
    recommended = {
        "decay_enabled": values["decay.enabled"],
        "half_life_base": values["decay.half_life_base"],
        "half_life_growth": values["decay.half_life_growth"],
        "half_life_max": values["decay.half_life_max"],
        "decay_floor_enabled": values["decay.floor_enabled"],
        "economy": {
            "negative_weight": values["economy.negative_weight"],
            "repair_threshold": values["economy.repair_threshold"],
        },
    }
    previous = dict(
        recommended,
        half_life_growth=1.3,
        economy={"negative_weight": 1.5, "repair_threshold": 2.0},
    )
    return {"previous_0_3_3": previous, "recommended": recommended}


async def replay(runtime: Path):
    if runtime.exists():
        raise ValueError("请使用新的空运行目录")
    arms = parameters()
    result = {
        "method": "preclassified synthetic events, fixed clock, real FavorService/SQLite; not model or human accuracy",
        "provider_calls": 0,
        "arms": {},
    }
    for arm, config in arms.items():
        cases = {}
        for name, events in SCENARIOS.items():
            store = SQLiteBackend(runtime / arm / name / "group.db")
            await store.init()
            service = FavorService(
                store,
                LevelTable.from_config(None),
                **dict(config, economy=EconomyConfig.from_config(config["economy"])),
            )
            rows = []
            try:
                for i, (day, second, kind, intensity) in enumerate(events):
                    with patch("time.time", return_value=START + day * 86400 + second):
                        change = await service.apply_relation_event(
                            "7001",
                            "8001",
                            kind,
                            intensity=intensity,
                            event_id=f"synthetic-{name}-{i}",
                        )
                        record = await store.get("7001", "8001")
                        rows.append(
                            dict(
                                day=day,
                                second=second,
                                event=kind,
                                delta=change["delta"],
                                state=change["after"],
                                half_life=record.half_life,
                                title=await service.relation_title("7001", "8001"),
                            )
                        )
                silence = {}
                for day in (3, 30):
                    with patch("time.time", return_value=START + day * 86400):
                        silence[str(day)] = (
                            await service.get_relation("7001", "8001")
                        ).as_dict()
                cases[name] = dict(rows=rows, silence=silence)
            finally:
                await store.close()
        result["arms"][arm] = dict(parameters=config, scenarios=cases)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(replay(output / "runtime"))
    result["source_sha256"] = {
        name: hashlib.sha256(
            (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for name in (
            "_conf_schema.json",
            "core/decay.py",
            "core/level_economy.py",
            "services/favor_service.py",
            "storage/sqlite_backend.py",
        )
    }
    (output / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for arm, data in result["arms"].items():
        for case, trajectory in data["scenarios"].items():
            last = trajectory["rows"][-1]
            print(arm, case, last["state"], "half_life", last["half_life"])


if __name__ == "__main__":
    main()
