"""只读源码下的默认值对照：真实服务/SQLite、合成数据、固定时钟，无网络。"""

import asyncio
import argparse
import hashlib
import subprocess
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
OUT = parser.parse_args().output.resolve()
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(REPO.parent))
from astrbot_plugin_bond.core.assessment import Interaction
from astrbot_plugin_bond.core.level_economy import EconomyConfig
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core import relation_lexicon as lexicon
from astrbot_plugin_bond.core.relation_policy import RelationState
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.services.assessment_service import (
    AssessmentConfig,
    AssessmentService,
    AssessmentCapacity,
)
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend

BASE_TIME = 1767225600.0
EDGES = {
    "current": (10.0, 25.0, 40.0, 70.0),
    "early_moderate": (2.0, 8.0, 40.0, 70.0),
    "early_quick": (1.0, 5.0, 40.0, 70.0),
}


async def services(name, **opts):
    store = SQLiteBackend(OUT / "runtime" / name / "10001" / "group.db")
    await store.init()
    favor = FavorService(
        store,
        LevelTable.from_config(None),
        economy=EconomyConfig.from_config(None),
        tz_name="Asia/Shanghai",
        **opts,
    )
    return store, favor


def titles(state):
    out = {}
    for arm, edge in EDGES.items():
        with patch.object(lexicon, "BAND_EDGES", (edge, *lexicon.BAND_EDGES[1:])):
            result = lexicon.describe(state)
            out[arm] = dict(title=result.title, familiarity_band=result.bands[0])
    return out


async def growth(kind, per_day):
    store, favor = await services(f"{kind}-{per_day}")
    rows = []
    try:
        for n in range(200):
            day, turn = divmod(n, per_day)
            now = BASE_TIME + day * 86400 + turn * 600
            with patch("time.time", return_value=now):
                result = await favor.apply_relation_event(
                    "7001", "8001", kind, event_id=f"synthetic-{kind}-{per_day}-{n}"
                )
                state = await favor.get_relation("7001", "8001")
            rows.append(
                dict(
                    turn=n + 1,
                    day=day + 1,
                    delta=result["delta"],
                    state=state.as_dict(),
                    titles=titles(state),
                )
            )
    finally:
        await store.close()
    first = {}
    for arm in EDGES:
        first[arm] = {}
        for band in (1, 2, 3, 4):
            first[arm][str(band)] = next(
                (
                    {k: r[k] for k in ("turn", "day", "state")}
                    for r in rows
                    if r["titles"][arm]["familiarity_band"] >= band
                ),
                None,
            )
    return dict(rows=rows, first_reached=first)


class Provider:
    def __init__(self):
        self.calls = 0

    async def text_chat(self, prompt):
        self.calls += 1
        text = json.loads(prompt.split("\n输入：")[-1])["text"]
        return SimpleNamespace(
            completion_text=json.dumps(
                dict(event_type="friendly", confidence=0.99, evidence=text),
                ensure_ascii=False,
            ),
            usage=SimpleNamespace(total=None),
        )


async def greetings():
    out = []
    for n, text in enumerate(
        [
            "早安",
            "早上好",
            "晚安",
            "晚安！🌙",
            "晚安喵",
            "晚安啦",
            "我要休息了，可以跟我说声晚安吗？",
        ]
    ):
        store, favor = await services(f"greeting-{n}")
        provider = Provider()

        async def stores():
            return [store]

        service = AssessmentService(
            store,
            favor,
            SimpleNamespace(get_provider_by_id=lambda _: provider),
            AssessmentConfig(
                enabled=True, small_provider_id="synthetic", cooldown_sec=120
            ),
            AssessmentCapacity(2),
            stores,
        )
        results = []
        try:
            for turn, seconds in enumerate([0, 30, 121, 242, 363, 484]):
                with patch("time.time", return_value=BASE_TIME + seconds):
                    status = await service.assess(
                        Interaction(
                            "10001", "7001", "8001", "synthetic", f"m-{turn}", text
                        ),
                        directed=True,
                    )
                    state = await favor.get_relation("7001", "8001")
                results.append(
                    dict(seconds=seconds, status=status, state=state.as_dict())
                )
            out.append(dict(text=text, provider_calls=provider.calls, rows=results))
        finally:
            await store.close()
    return out


async def silence():
    results = {}
    for name, enabled, base, maximum in [
        ("off", False, 10, 60),
        ("current_on", True, 10, 60),
        ("gentle_candidate", True, 30, 120),
    ]:
        store, favor = await services(
            f"silence-{name}",
            decay_enabled=enabled,
            half_life_base=base,
            half_life_max=maximum,
            decay_floor_enabled=True,
        )
        rows = []
        try:
            for i, affinity in enumerate([5, 20, 60, -20]):
                user = str(8100 + i)
                with patch("time.time", return_value=BASE_TIME):
                    await favor.set_relation(
                        "7001",
                        user,
                        familiarity=40,
                        affinity=affinity,
                        trust=-20,
                        event_id=f"manual-{i}",
                    )
                trajectory = []
                for day in [0, 1, 7, 30, 60, 180]:
                    with patch("time.time", return_value=BASE_TIME + day * 86400):
                        state = await favor.get_relation("7001", user)
                    trajectory.append(
                        dict(
                            day=day,
                            state=state.as_dict(),
                            title=lexicon.describe(state).title,
                        )
                    )
                rows.append(dict(start_affinity=affinity, trajectory=trajectory))
            results[name] = rows
        finally:
            await store.close()
    return results


async def main():
    if (OUT / "runtime").exists():
        raise SystemExit("runtime already exists; choose an empty output directory")
    schema = json.loads((REPO / "_conf_schema.json").read_text(encoding="utf-8"))
    config = []

    def flatten(items, prefix="", hidden=False):
        for key, item in items.items():
            name = prefix + key
            invisible = hidden or item.get("invisible", False)
            if item.get("type") == "object":
                flatten(item["items"], name + ".", invisible)
            else:
                config.append(
                    dict(
                        path=name,
                        default=item.get("default"),
                        hidden=invisible,
                        description=item.get("description", ""),
                    )
                )

    flatten(schema)
    result = dict(
        tested_base_commit=subprocess.check_output(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
        ).strip(),
        source_hashes={
            name: hashlib.sha256(
                (REPO / name).read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for name in [
                "core/assessment.py",
                "core/relation_lexicon.py",
                "core/relation_policy.py",
                "core/decay.py",
                "services/favor_service.py",
                "_conf_schema.json",
                "main.py",
            ]
        },
        method="synthetic fixed-clock real service and SQLite; predefined Provider always friendly .99; not semantic accuracy",
        config_inventory=config,
        growth={},
        greetings=await greetings(),
        silence=await silence(),
    )
    for kind in ["normal_question", "friendly"]:
        for per_day in [2, 10]:
            result["growth"][f"{kind}-{per_day}"] = await growth(kind, per_day)
    (OUT / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = dict(
        settings=len(config),
        growth={k: v["first_reached"] for k, v in result["growth"].items()},
        greetings=[
            dict(
                text=x["text"],
                calls=x["provider_calls"],
                final=x["rows"][-1]["state"],
                statuses=[r["status"] for r in x["rows"]],
            )
            for x in result["greetings"]
        ],
        silence=result["silence"],
    )
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"{len(config)} settings; 800 synthetic growth events; 42 greeting attempts; 3 decay arms. Results: {OUT}"
    )


asyncio.run(main())
