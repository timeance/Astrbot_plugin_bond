"""久别旧友的现状回放；固定时钟、合成已分类事件，不新增回暖机制。"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_bond.core.level_economy import EconomyConfig
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_profiles import build_relation_profile
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

START = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()


async def replay(output):
    output.mkdir(parents=True, exist_ok=False)
    results = {}
    for mode in ("realistic", "galgame"):
        store = SQLiteBackend(output / mode / "group.db")
        await store.init()
        policy, caps = build_relation_profile(mode)
        svc = FavorService(
            store,
            LevelTable.from_config(None),
            economy=EconomyConfig(),
            relation_policy=policy,
            relation_caps=caps,
            decay_enabled=True,
            half_life_base=30,
            half_life_growth=1,
            half_life_max=120,
            decay_floor_enabled=True,
            tz_name="Asia/Shanghai",
        )
        rows = []

        async def capture(stage):
            rows.append(
                dict(
                    stage=stage,
                    state=(await svc.get_relation("g", "u")).as_dict(),
                    title=await svc.relation_title("g", "u"),
                )
            )

        try:
            with patch("time.time", return_value=START):
                await svc.set_relation(
                    "g", "u", familiarity=60, affinity=40, trust=20, event_id="seed"
                )
                await svc.apply_relation_event(
                    "g", "u", "friendly", event_id="before-departure"
                )
                await capture("before_silence")
            with patch("time.time", return_value=START + 90 * 86400):
                await capture("after_90_days")
            for index in range(3):
                with patch("time.time", return_value=START + 90 * 86400 + index * 180):
                    change = await svc.apply_relation_event(
                        "g", "u", "friendly", event_id=f"return-{index}"
                    )
                    await capture(f"return_{index + 1}")
                    rows[-1]["delta"] = change["delta"]
        finally:
            await store.close()
        results[mode] = rows
    (output / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for mode, rows in results.items():
        print(mode, [(row["stage"], row["state"]["affinity"]) for row in rows])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(replay(parser.parse_args().output.resolve()))
