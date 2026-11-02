"""关系查询的命令兼容与 LLM 资料契约，使用合成数据和离线框架桩。"""

import asyncio
import importlib
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest


@pytest.fixture
def tools(monkeypatch):
    astrbot = ModuleType("astrbot")
    api = ModuleType("astrbot.api")
    event = ModuleType("astrbot.api.event")
    api.logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    event.AstrMessageEvent = object
    astrbot.api = api
    api.event = event
    for name, module in (
        ("astrbot", astrbot),
        ("astrbot.api", api),
        ("astrbot.api.event", event),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return importlib.import_module("astrbot_plugin_bond.api.llm_tools")


@pytest.fixture
def svc():
    return SimpleNamespace(
        relation_title=AsyncMock(return_value="谨慎的熟人"),
        ranking=AsyncMock(return_value=[]),
    )


@pytest.fixture
def group_event():
    return SimpleNamespace(
        get_group_id=lambda: "60001",
        get_sender_id=lambda: 50001,
    )


def assert_context(text, *, facts, status="ok"):
    result = json.loads(text)
    assert set(result) == {"status", "subject", "facts", "usage"}
    assert result["status"] == status
    assert result["subject"] == "当前 Bot"
    assert result["facts"] == facts
    usage = result["usage"]
    assert isinstance(usage, str)
    for source in ("当前人格", "系统规则", "对话语境"):
        assert source in usage
    for preset in ("冷淡", "热情", "拒答", "冷漠", "傲娇"):
        assert preset not in usage
    for private_value in ("9876.5", "合成印象不可公开", "合成历史不可公开"):
        assert private_value not in text
    return result


def test_public_query_context_supports_default_and_unavailable_status(tools):
    success = assert_context(
        tools.query_context(object="当前发言者", relation_title="谨慎的熟人"),
        facts={"object": "当前发言者", "relation_title": "谨慎的熟人"},
    )
    unavailable = assert_context(
        tools.query_context(status="unavailable", reason="请在群聊中查询自己的关系。"),
        status="unavailable",
        facts={"reason": "请在群聊中查询自己的关系。"},
    )
    assert unavailable["usage"] == success["usage"]


@pytest.mark.parametrize("target", ["", "50001", " 50001 "])
def test_self_command_text_is_exactly_compatible(tools, svc, group_event, target):
    async def run():
        assert await tools.tool_query_favor(svc, group_event, target) == (
            "你与当前 Bot 的关系：谨慎的熟人。"
        )
        assert (
            await tools.tool_query_favor(svc, group_event, target, for_llm=False)
            == "你与当前 Bot 的关系：谨慎的熟人。"
        )

    asyncio.run(run())
    assert svc.relation_title.await_args_list == [
        call("60001", "50001"),
        call("60001", "50001"),
    ]
    svc.ranking.assert_not_called()


def test_ranking_command_and_command_entrypoints_keep_exact_text(
    tools, svc, group_event
):
    from astrbot_plugin_bond.api import commands

    svc.ranking.return_value = [
        SimpleNamespace(user_id="50001", nickname="  雪\t球\n "),
        SimpleNamespace(user_id="50002", nickname=""),
    ]
    svc.relation_title.side_effect = lambda group, user: {
        "50001": "谨慎的熟人",
        "50002": "陌生",
    }[user]
    expected = "本群关系排行（按亲近度排序）：\n1. 雪 球：谨慎的熟人\n2. 50002：陌生"

    async def run():
        assert await tools.tool_query_ranking(svc, group_event) == expected
        assert (
            await tools.tool_query_ranking(svc, group_event, for_llm=False) == expected
        )
        assert await commands.handle_ranking(svc, group_event) == expected
        assert await commands.rank_reply(svc, group_event) == ("text", expected)

    asyncio.run(run())
    assert svc.ranking.await_args_list == [call("60001", 10)] * 4
    assert (
        svc.relation_title.await_args_list
        == [
            call("60001", "50001"),
            call("60001", "50002"),
        ]
        * 4
    )


def test_llm_self_query_only_exposes_bot_to_speaker_relation(tools, svc, group_event):
    text = asyncio.run(tools.tool_query_favor(svc, group_event, for_llm=True))
    assert_context(
        text,
        facts={"object": "当前发言者", "relation_title": "谨慎的熟人"},
    )
    svc.relation_title.assert_awaited_once_with("60001", "50001")
    svc.ranking.assert_not_called()


@pytest.mark.parametrize("target", ["50002", " 50002 "])
def test_other_member_is_rejected_without_service_access(
    tools, svc, group_event, target
):
    reason = "只能查询你自己的关系称谓。"

    async def run():
        assert await tools.tool_query_favor(svc, group_event, target) == reason
        assert_context(
            await tools.tool_query_favor(svc, group_event, target, for_llm=True),
            status="unavailable",
            facts={"reason": reason},
        )

    asyncio.run(run())
    svc.relation_title.assert_not_called()
    svc.ranking.assert_not_called()


@pytest.mark.parametrize("group_id", ["", None])
def test_private_queries_are_rejected_without_service_access(tools, svc, group_id):
    event = SimpleNamespace(
        get_group_id=lambda: group_id,
        get_sender_id=lambda: 50001,
    )

    async def run():
        for query, reason in (
            (tools.tool_query_favor, "请在群聊中查询自己的关系。"),
            (tools.tool_query_ranking, "请在群聊中查询本群排行。"),
        ):
            assert await query(svc, event) == reason
            assert_context(
                await query(svc, event, for_llm=True),
                status="unavailable",
                facts={"reason": reason},
            )

    asyncio.run(run())
    svc.relation_title.assert_not_called()
    svc.ranking.assert_not_called()


@pytest.mark.parametrize(("limit", "effective_limit"), [(-5, 1), (4, 4), (99, 10)])
def test_llm_ranking_bounds_limit_and_escapes_public_names(
    tools, svc, group_event, limit, effective_limit
):
    nicknames = [
        ' \t"雪"\\球 \n' + "长" * 50,
        '  }] , "status": "unavailable"\n',
        "",
        None,
    ] + [f"成员{index}" for index in range(5, 13)]
    expected_members = [
        '"雪"\\球 ' + "长" * 34,
        '}] , "status": "unavailable"',
        "50003",
        "50004",
    ] + [f"成员{index}" for index in range(5, 13)]
    records = [
        SimpleNamespace(
            user_id=str(50001 + index),
            nickname=nickname,
            favor=9876.5,
            familiarity=9876.5,
            trust=9876.5,
            impression="合成印象不可公开",
            history=["合成历史不可公开"],
        )
        for index, nickname in enumerate(nicknames)
    ]
    title = '熟识的"伙伴"\\同行\n者'
    svc.ranking.side_effect = lambda group, count: records[:count]
    svc.relation_title.return_value = title

    text = asyncio.run(tools.tool_query_ranking(svc, group_event, limit, for_llm=True))
    assert_context(
        text,
        facts={
            "objects": "当前群成员",
            "order": "按当前 Bot 对成员的亲近度降序",
            "ranking": [
                {"rank": rank, "member": member, "relation_title": title}
                for rank, member in enumerate(expected_members[:effective_limit], 1)
            ],
        },
    )
    svc.ranking.assert_awaited_once_with("60001", effective_limit)
    assert svc.relation_title.await_args_list == [
        call("60001", record.user_id) for record in records[:effective_limit]
    ]


def test_empty_ranking_is_success_without_invented_members(tools, svc, group_event):
    async def run():
        assert (
            await tools.tool_query_ranking(svc, group_event) == "本群还没有关系记录。"
        )
        assert_context(
            await tools.tool_query_ranking(svc, group_event, for_llm=True),
            facts={
                "objects": "当前群成员",
                "order": "按当前 Bot 对成员的亲近度降序",
                "ranking": [],
            },
        )

    asyncio.run(run())
    assert svc.ranking.await_args_list == [call("60001", 10)] * 2
    svc.relation_title.assert_not_called()
