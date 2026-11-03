"""初识节奏、低信息问候和衰减默认的行为回归。"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from astrbot_plugin_bond.core.assessment import locally_relevant
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.core.settings import snapshot, validate_patch
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend
from astrbot_plugin_bond.tests.test_assessment import (
    Provider,
    interaction,
    setup,
    verdict,
)


@pytest.mark.parametrize(
    "text",
    [
        "早上好",
        "晚上好",
        "中午好",
        "晚安喵",
        "晚安啦",
        "早安呀！🌞",
        "晚安哦哦",
        "HELLO呀",
        "你好啊",
    ],
)
def test_whole_greeting_cannot_gain_even_when_provider_always_says_friendly(
    tmp_path, text
):
    async def run():
        provider = Provider(result=verdict(text=text, kind="friendly", confidence=0.99))
        store, favor, service, _ = await setup(tmp_path, small=provider)
        try:
            for day in [0, 1, 50, 100, 200]:
                with patch("time.time", return_value=1767225600.0 + day * 86400):
                    assert (
                        await service.assess(
                            interaction(text=text, message=f"greeting-{day}"),
                            directed=True,
                        )
                        == "filtered"
                    )
            assert provider.calls == 0
            assert (await favor.get_relation("789", "456")).as_dict() == dict(
                familiarity=0, affinity=0, trust=0
            )
            assert await favor.query_logs("789", "456") == []
        finally:
            await store.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [
        "早上好，刚才那段程序哪里有问题？",
        "晚安，感谢你帮我找出了计算错误",
        "晚安喵函数为什么报错",
        "hello()",
        "请解释早上好的意思",
        "我要休息了，能跟我说声晚安吗？",
    ],
)
def test_greeting_filter_keeps_substantive_or_ambiguous_messages(text):
    assert locally_relevant(text, True)
    assert not locally_relevant(text, False)


def test_newcomer_becomes_familiar_without_affinity_or_trust_inflation(tmp_path):
    async def run():
        store = SQLiteBackend(tmp_path / "relations.db")
        await store.init()
        favor = FavorService(store, LevelTable.from_config(None))
        try:
            for n in range(50):
                day, turn = divmod(n, 10)
                with patch(
                    "time.time", return_value=1767225600 + day * 86400 + turn * 180
                ):
                    await favor.apply_relation_event(
                        "g", "u", "normal_question", event_id=f"q-{n}"
                    )
                    if n == 8:
                        assert await favor.relation_title("g", "u") == "陌生"
                    if n == 9:
                        assert await favor.relation_title("g", "u") == "眼熟之人"
            assert await favor.relation_title("g", "u") == "泛泛之交"
            assert (await favor.get_relation("g", "u")).as_dict() == dict(
                familiarity=5, affinity=0, trust=0
            )
            logs = await favor.query_logs("g", "u", limit=100)
            assert len(logs) == 50
            assert all(
                row["relation_delta"]
                == {"familiarity": 0.1, "affinity": 0.0, "trust": 0.0}
                for row in logs
            )
        finally:
            await store.close()

    asyncio.run(run())


def test_recommended_decay_and_explicit_old_configuration(tmp_path):
    async def run():
        values = snapshot({})["values"]
        assert values["decay.enabled"] is True
        assert values["decay.half_life_base"] == 30
        assert values["decay.half_life_growth"] == 1.0
        assert values["decay.half_life_max"] == 120
        old = snapshot(
            {"decay": {"enabled": False, "half_life_base": 10, "half_life_max": 60}}
        )["values"]
        assert (
            old["decay.enabled"] is False
            and old["decay.half_life_base"] == 10
            and old["decay.half_life_max"] == 60
        )
        store = SQLiteBackend(tmp_path / "relations.db")
        await store.init()
        favor = FavorService(
            store,
            LevelTable.from_config(None),
            decay_enabled=values["decay.enabled"],
            half_life_base=values["decay.half_life_base"],
            half_life_growth=values["decay.half_life_growth"],
            half_life_max=values["decay.half_life_max"],
            decay_floor_enabled=True,
        )
        start = 1767225600.0
        try:
            with patch("time.time", return_value=start):
                await favor.set_relation(
                    "g", "u", familiarity=40, affinity=20, trust=-20, event_id="seed"
                )
                before = await favor.query_logs("g", "u")
            with patch("time.time", return_value=start + 30 * 86400):
                assert (await favor.get_relation("g", "u")).as_dict() == dict(
                    familiarity=40, affinity=10, trust=-20
                )
                assert await favor.query_logs("g", "u") == before
                assert (await store.get("g", "u")).favor == 20
        finally:
            await store.close()

    asyncio.run(run())


def test_settings_do_not_offer_unused_render_or_silently_clamped_values():
    render_keys = {key for key in snapshot({})["values"] if key.startswith("render.")}
    assert render_keys == {"render.font_path"}
    for values in [
        {"inject.memory_count": 11},
        {"economy.repair_scale_high": 0.5},
        {"economy.repair_scale_low": 0},
    ]:
        with pytest.raises(ValueError):
            validate_patch(values)
    validate_patch({"inject.memory_count": 10, "economy.repair_scale_high": 1})
