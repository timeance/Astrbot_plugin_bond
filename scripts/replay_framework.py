"""框架长期回放：固定时钟、预置模型响应和真实 SQLite；不访问网络。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from astrbot_plugin_bond.core.assessment import Interaction
from astrbot_plugin_bond.core.level_economy import EconomyConfig
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.relation_profiles import build_relation_profile
from astrbot_plugin_bond.services.assessment_service import (
    AssessmentConfig,
    AssessmentCapacity,
    AssessmentService,
)
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

START = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()


async def run_case(mode, frequency, days=180):
    store = SQLiteBackend(Path(":memory:"))
    await store.init()
    # 纯内存 SQLite 保留真实事务；可信 Bot 身份只用于此合成装配。
    store._db_path = Path("synthetic/10001/group.db")
    policy, caps = build_relation_profile(mode)
    favor = FavorService(
        store,
        LevelTable.from_config(None),
        relation_policy=policy,
        relation_caps=caps,
        economy=EconomyConfig(),
        decay_enabled=True,
        half_life_base=60,
        half_life_growth=1,
        half_life_max=120,
        decay_floor_enabled=True,
        trust_decay_enabled=True,
        trust_half_life=180,
    )
    response = {}
    calls = 0

    async def text_chat(prompt):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            completion_text=json.dumps(response, ensure_ascii=False), usage=None
        )

    async def stores():
        return [store]

    assessor = AssessmentService(
        store,
        favor,
        SimpleNamespace(
            get_provider_by_id=lambda _: SimpleNamespace(text_chat=text_chat)
        ),
        AssessmentConfig(enabled=True, small_provider_id="synthetic"),
        AssessmentCapacity(),
        stores,
    )
    status = Counter()
    checkpoints = {}
    try:
        for day in range(days):
            for turn in range(frequency):
                now = START + day * 86400 + turn * 600
                # 模拟模型判断：三次有内容问答后一次友好互动，第五次重复表达弃权。
                kind = "friendly" if turn % 4 == 3 else "normal_question"
                text = f"合成第{day}天第{turn}次互动"
                response = dict(
                    event_type=kind,
                    confidence=0.95,
                    evidence=text,
                    should_update=turn % 5 != 4,
                )
                with patch("time.time", return_value=now):
                    item = Interaction(
                        "10001", "7001", "8001", "synthetic", f"{day}:{turn}", text
                    )
                    status[await assessor.assess(item, directed=True)] += 1
            if day + 1 in (7, 30, 180):
                with patch("time.time", return_value=START + (day + 1) * 86400):
                    checkpoints[str(day + 1)] = (
                        await favor.get_relation("7001", "8001")
                    ).as_dict()
        return dict(
            mode=mode,
            attempts_per_day=frequency,
            days=days,
            status=dict(status),
            simulated_calls=calls,
            checkpoints=checkpoints,
            real_provider_tokens=None,
            real_semantic_accuracy=None,
        )
    finally:
        await store.close()


async def replay():
    return dict(
        method="fixed-clock synthetic verdicts through AssessmentService/FavorService/SQLite; no semantic accuracy claim",
        cases=[
            await run_case(mode, frequency)
            for mode in ("realistic", "galgame")
            for frequency in (1, 5, 30, 60)
        ],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    target = output / "results.json"
    if target.exists():
        raise ValueError("请使用新的输出目录")
    result = asyncio.run(replay())
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for item in result["cases"]:
        print(
            item["mode"],
            item["attempts_per_day"],
            item["checkpoints"],
            item["simulated_calls"],
        )


if __name__ == "__main__":
    main()
