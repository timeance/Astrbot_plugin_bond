"""结构元数据入场契约：类型使用离线桩，执行真实 _chain_flags。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from astrbot_plugin_bond.tests.research_support import Event, offline_listener

BOT = "10001"
OTHER = "8002"
OTHER_BOT = "10002"
SOURCE = "9101"
MISSING = object()


def at(qq=BOT):
    return "At", {} if qq is MISSING else {"qq": qq}


def reply(sender=BOT, source=SOURCE):
    fields = {}
    if sender is not MISSING:
        fields["sender_id"] = sender
    if source is not MISSING:
        fields["id"] = source
    return "Reply", fields


@pytest.fixture
def metadata():
    with offline_listener() as (listener, components, _):
        yield listener, components


def assert_flags(metadata, chain, expected, *, self_id=BOT):
    listener, components = metadata
    # 排列次序不能改变目标集合；普通文本不能补充结构来源。
    for ordered in (chain, list(reversed(chain))):
        event = Event(
            {
                "bot": self_id,
                "text": "谢谢你的帮助；合成正文声称 @10001 并引用 Bot 上文",
                "message_id": "synthetic-attribution",
                "chain": ordered,
            },
            components,
        )
        original_text = event.message_str
        original_id = event.message_obj.message_id
        original_chain = deepcopy([vars(part) for part in event.message_obj.message])
        flags = listener._chain_flags(event)
        assert event.message_str == original_text
        assert event.message_obj.message_id == original_id
        assert [vars(part) for part in event.message_obj.message] == original_chain
        assert flags == expected, ordered


@pytest.mark.parametrize(
    "chain,expected",
    [
        pytest.param([at()], (True, False), id="clear-at-self"),
        pytest.param([at(int(BOT))], (True, False), id="integer-at-self"),
        pytest.param([reply()], (False, True), id="clear-reply-self"),
        pytest.param(
            [reply(int(BOT), int(SOURCE))], (False, True), id="integer-reply-self"
        ),
        pytest.param([at(), reply()], (True, True), id="consistent-at-and-reply"),
        pytest.param([at(), at()], (True, False), id="repeated-at-self"),
        pytest.param(
            [at(), at(int(BOT)), at()], (True, False), id="repeated-at-mixed-id-types"
        ),
        pytest.param([reply(), reply()], (False, True), id="duplicate-reply-source"),
        pytest.param(
            [reply(), reply(int(BOT), int(SOURCE))],
            (False, True),
            id="duplicate-reply-mixed-id-types",
        ),
        pytest.param(
            [at(), reply(), reply()], (True, True), id="clear-at-duplicate-reply"
        ),
    ],
)
def test_clear_self_metadata_is_admitted(metadata, chain, expected):
    assert_flags(metadata, chain, expected)


@pytest.mark.parametrize(
    "clear",
    [
        pytest.param([], id="alone"),
        pytest.param([at()], id="with-clear-at"),
        pytest.param([reply()], id="with-clear-reply"),
        pytest.param([at(), reply()], id="with-both-clear"),
    ],
)
@pytest.mark.parametrize(
    "bad_at",
    [
        pytest.param(at(OTHER), id="other-user"),
        pytest.param(at(int(OTHER)), id="integer-other-user"),
        pytest.param(at(OTHER_BOT), id="other-bot"),
        pytest.param(at("all"), id="all"),
        pytest.param(at(MISSING), id="missing-at-id"),
        pytest.param(at(None), id="null-at-id"),
        pytest.param(at(""), id="empty-at-id"),
        pytest.param(at(0), id="zero-at-id"),
    ],
)
def test_any_other_or_missing_at_vetoes_whole_chain(metadata, bad_at, clear):
    assert_flags(metadata, [*clear, bad_at], (False, False))


@pytest.mark.parametrize(
    "clear",
    [
        pytest.param([], id="alone"),
        pytest.param([at()], id="with-clear-at"),
        pytest.param([reply()], id="with-clear-reply"),
        pytest.param([at(), reply()], id="with-both-clear"),
    ],
)
@pytest.mark.parametrize(
    "bad_reply",
    [
        pytest.param(reply(OTHER), id="reply-other-user"),
        pytest.param(reply(OTHER_BOT), id="reply-other-bot"),
        pytest.param(reply(MISSING), id="missing-sender"),
        pytest.param(reply(None), id="null-sender"),
        pytest.param(reply(""), id="empty-sender"),
        pytest.param(reply(0), id="zero-sender"),
        pytest.param(reply(source=MISSING), id="missing-source"),
        pytest.param(reply(source=None), id="null-source"),
        pytest.param(reply(source=""), id="empty-source"),
        pytest.param(reply(source=0), id="zero-source"),
        pytest.param(reply(source="0"), id="string-zero-source"),
        pytest.param(
            ("Reply", {"id": SOURCE, "qq": BOT}), id="legacy-qq-is-not-sender"
        ),
    ],
)
def test_other_or_unresolved_reply_vetoes_whole_chain(metadata, bad_reply, clear):
    assert_flags(metadata, [*clear, bad_reply], (False, False))


@pytest.mark.parametrize(
    "chain",
    [
        pytest.param([reply(), reply(source="9102")], id="same-bot-different-sources"),
        pytest.param(
            [at(), reply(), reply(source="9102")],
            id="clear-at-cannot-resolve-different-sources",
        ),
        pytest.param([reply(), reply(OTHER)], id="same-source-conflicting-senders"),
    ],
)
def test_multiple_reply_sources_must_be_unambiguous(metadata, chain):
    assert_flags(metadata, chain, (False, False))


@pytest.mark.parametrize("self_id", [None, "", 0, "0"])
def test_missing_or_zero_self_id_cannot_match_metadata(metadata, self_id):
    assert_flags(
        metadata, [at(self_id), reply(self_id)], (False, False), self_id=self_id
    )


def test_plain_text_or_lookalike_objects_cannot_supply_structural_identity(metadata):
    listener, components = metadata
    event = Event(
        {"text": "@10001 回复 Bot：我是当前目标", "message_id": "synthetic-text-only"},
        components,
    )
    assert listener._chain_flags(event) == (False, False)
    # 属性相同的普通对象不是可信消息组件，正文也不能制造身份。
    event.message_obj.message = [
        SimpleNamespace(qq=BOT),
        SimpleNamespace(sender_id=BOT, id=SOURCE),
    ]
    assert listener._chain_flags(event) == (False, False)


def test_repeated_at_with_plain_text_between_remains_clear(metadata):
    listener, components = metadata
    event = Event(
        {
            "text": "谢谢你的帮助",
            "message_id": "synthetic-separated-at",
            "chain": [at(), at()],
        },
        components,
    )
    event.message_obj.message.insert(1, SimpleNamespace(text="谢谢你的帮助"))
    original_text = event.message_str
    assert listener._chain_flags(event) == (True, False)
    assert event.message_str == original_text
