"""未接纳评估不得借由访问或分类更新任何维护时间。"""

import asyncio
from unittest.mock import patch

import pytest

from astrbot_plugin_bond.tests.test_assessment import (
    Provider,
    interaction,
    setup,
    verdict,
)


@pytest.mark.parametrize("case", ["should_not_update", "uncertain", "failure"])
def test_unaccepted_assessment_preserves_maintenance(tmp_path, case):
    async def run():
        provider = Provider(
            result=verdict(
                kind="uncertain" if case == "uncertain" else "thanks",
                should_update=case != "should_not_update",
            ),
            error=RuntimeError("合成模型故障") if case == "failure" else None,
        )
        with patch("time.time", return_value=1_800_000_000.0):
            store, favor, assessor, _ = await setup(tmp_path, small=provider)
            await favor.set_relation(
                "789",
                "456",
                familiarity=60,
                affinity=70,
                trust=-50,
                event_id="synthetic-seed",
            )
            before = await store.get("789", "456")
        try:
            with patch("time.time", return_value=1_800_000_000.0 + 9 * 86400):
                status = await assessor.assess(interaction(), directed=True)
                assert status == ("failed" if case == "failure" else "abstained")
                assert await store.get("789", "456") == before
                # 审计调用账本可以变化，关系事件和所有维护资料不能变化。
                assert len(await favor.query_logs("789", "456")) == 1
        finally:
            await store.close()

    asyncio.run(run())
