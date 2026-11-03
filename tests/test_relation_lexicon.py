"""表达合同：覆盖五档组合，保留矛盾态度与有效状态生命周期。"""

import asyncio
from itertools import product

import pytest

from astrbot_plugin_bond.core.relation_lexicon import BAND_LABELS, CELLS, TERMS
from astrbot_plugin_bond.core.relation_policy import RelationResolver, RelationState
from astrbot_plugin_bond.core.levels import LevelTable
from astrbot_plugin_bond.services.favor_service import FavorService
from astrbot_plugin_bond.storage.sqlite_backend import SQLiteBackend


def test_all_combinations_have_distinct_dimension_meanings():
    resolver = RelationResolver()
    centers = ((0, 3, 20, 50, 85), (-70, -25, 0, 25, 70), (-70, -25, 0, 25, 70))
    seen = set()
    for bands in product(range(5), repeat=3):
        desc = resolver.describe(
            RelationState(*(centers[i][b] for i, b in enumerate(bands)))
        )
        assert desc.bands == bands
        assert desc.title and desc.meaning and desc.key in TERMS
        assert len(desc.labels) == 3
        assert all(label in desc.text for label in desc.labels)
        assert desc.labels == tuple(
            BAND_LABELS[i][band] for i, band in enumerate(bands)
        )
        seen.add(desc.key)
    assert seen == set(TERMS)
    assert 40 <= len(seen) <= 60
    assert len(CELLS) == 5
    assert len({title for title, _ in TERMS.values()}) == len(TERMS)


@pytest.mark.parametrize(
    "dimension,value,expected",
    [
        (0, 0.9, 0),
        (0, 1, 1),
        (0, 4.9, 1),
        (0, 5, 2),
        (0, 39.9, 2),
        (0, 40, 3),
        (0, 69.9, 3),
        (0, 70, 4),
        (1, -40.1, 0),
        (1, -40, 0),
        (1, -39.9, 1),
        (1, -10.1, 1),
        (1, -10, 2),
        (1, 9.9, 2),
        (1, 10, 3),
        (1, 39.9, 3),
        (1, 40, 4),
        (2, -40, 0),
        (2, -39.9, 1),
        (2, -10, 2),
        (2, 0, 2),
        (2, 10, 3),
        (2, 40, 4),
    ],
)
def test_boundaries_are_explicit_and_zero_is_not_distrust(dimension, value, expected):
    values = [0, 0, 0]
    values[dimension] = value
    assert (
        RelationResolver().describe(RelationState(*values)).bands[dimension] == expected
    )


def test_mixed_attitudes_keep_trust_independent_and_do_not_invent_history():
    resolver = RelationResolver()
    warm_guarded = resolver.describe(RelationState(90, 80, -80))
    distant_trusted = resolver.describe(RelationState(90, -80, 80))
    assert warm_guarded.title == "亲近却戒备"
    assert distant_trusted.title == "疏远但信赖"
    assert "亲密" in warm_guarded.text and "戒备" in warm_guarded.text
    assert "疏远" in distant_trusted.text and "信赖" in distant_trusted.text
    for title, meaning in TERMS.values():
        assert not any(
            word in title + meaning
            for word in ("患难", "背叛过", "重归于好", "恋人", "主人")
        )
    assert resolver.resolve(RelationState()) == "陌生"


def test_explicit_legacy_configuration_and_description_remain_consistent():
    resolver = RelationResolver(thresholds={}, labels={"trusted": "可信"})
    state = RelationState(90, 0, 60)
    assert resolver.resolve(state) == "熟悉、可信"
    assert resolver.describe(state).title == resolver.resolve(state)
    assert "亲近程度：可信" not in resolver.describe(state).text
    assert (
        RelationResolver(thresholds={}).resolve(RelationState(90, 65, -60))
        == "熟悉、亲近但戒备"
    )


def test_effective_temporary_restart_undo_and_render_do_not_mutate_history(tmp_path):
    async def run():
        db = SQLiteBackend(tmp_path / "relations.db")
        await db.init()
        service = FavorService(db, LevelTable.from_config(None))
        await service.set_relation(
            "g", "u", familiarity=85, affinity=70, trust=70, event_id="seed"
        )
        original = await service.query_logs("g", "u")
        assert await service.relation_title("g", "u") == "亲密挚友"
        for _ in range(3):
            await service.relation_title("g", "u")
        assert await service.query_logs("g", "u") == original
        await service.set_temporary(
            "g", "u", dict(familiarity=85, affinity=-70, trust=70), 600, "synthetic"
        )
        assert await service.relation_title("g", "u") == "疏远但信赖"
        await db.close()
        await db.init()
        assert await service.relation_title("g", "u") == "疏远但信赖"
        await service.set_temporary("g", "u", None, 0, "synthetic")
        assert await service.relation_title("g", "u") == "亲密挚友"
        assert await service.query_logs("g", "u") == original
        await service.undo_log(original[0]["id"])
        assert await service.relation_title("g", "u") == "陌生"
        await db.close()

    asyncio.run(run())
