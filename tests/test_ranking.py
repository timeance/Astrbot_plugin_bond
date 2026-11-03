"""公开排行的权限、图片隐私、离线字体和失败路径。"""

import asyncio
import io
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image, ImageDraw

from astrbot_plugin_bond.api import rank_image


@pytest.fixture
def query(monkeypatch):
    module = ModuleType("astrbot.api.event")
    module.AstrMessageEvent = object
    monkeypatch.setitem(sys.modules, "astrbot.api.event", module)
    from astrbot_plugin_bond.api import commands, llm_tools

    service = SimpleNamespace(
        ranking=AsyncMock(
            return_value=[
                SimpleNamespace(
                    user_id="8001",
                    nickname="合成昵称",
                    favor=87.6,
                    impression="私有证据",
                )
            ]
        ),
        relation_title=AsyncMock(return_value="亲近却戒备"),
        is_master=lambda uid: uid == "8001",
    )
    event = SimpleNamespace(
        get_group_id=lambda: "7001",
        get_sender_id=lambda: "8001",
        message_str="好感排行",
    )
    return commands, llm_tools, service, event


@pytest.mark.parametrize("access", ["master", "invalid"])
def test_all_ranking_entries_deny_before_read_or_avatar_download(query, access):
    commands, tools, service, event = query
    event.get_sender_id = lambda: "8002"
    event.is_admin = lambda: True  # 群管理员或昵称自称主人都不能替代配置账号。
    event.get_sender_name = lambda: "主人"
    avatars = SimpleNamespace(load=AsyncMock())

    async def run():
        result = await tools.tool_query_ranking(
            service, event, ranking_access=access, for_llm=True
        )
        assert json.loads(result)["status"] == "unavailable"
        assert "ranking" not in json.loads(result)["facts"]
        assert (
            await commands.rank_reply(
                service, event, ranking_access=access, avatar_service=avatars
            )
        )[0] == "text"
        assert (
            await commands.try_text_wake(
                service,
                event,
                {"好感排行"},
                ranking_access=access,
                avatar_service=avatars,
            )
        )[0] == "text"

    asyncio.run(run())
    service.ranking.assert_not_called()
    service.relation_title.assert_not_called()
    avatars.load.assert_not_called()


def test_master_image_uses_only_public_rows_and_avatars(query, monkeypatch):
    commands, tools, service, event = query
    renderer = Mock(return_value="synthetic-card.png")
    monkeypatch.setattr(rank_image, "render_ranking", renderer)
    avatars = SimpleNamespace(load=AsyncMock(return_value=[b"synthetic-avatar"]))
    result = asyncio.run(
        commands.rank_reply(
            service, event, ranking_access="master", avatar_service=avatars
        )
    )
    assert result == ("image", "synthetic-card.png")
    avatars.load.assert_awaited_once_with(["8001"])
    assert renderer.call_args.args == (
        [{"rank": 1, "member": "合成昵称", "relation_title": "亲近却戒备"}],
        1,
    )
    assert renderer.call_args.kwargs == {
        "font_path": "",
        "avatars": [b"synthetic-avatar"],
    }
    assert "87.6" not in str(renderer.call_args) and "私有证据" not in str(
        renderer.call_args
    )
    service.ranking.assert_awaited_once_with("7001", 10)


def test_all_mode_allows_nonmaster_and_empty_master_list_denies(query):
    commands, tools, service, event = query
    service.is_master = lambda uid: False

    async def run():
        assert (
            json.loads(
                await tools.tool_query_ranking(
                    service, event, ranking_access="all", for_llm=True
                )
            )["status"]
            == "ok"
        )
        service.ranking.reset_mock()
        assert (
            json.loads(
                await tools.tool_query_ranking(
                    service, event, ranking_access="master", for_llm=True
                )
            )["status"]
            == "unavailable"
        )
        service.ranking.assert_not_called()

    asyncio.run(run())


def test_disabled_image_skips_network_and_failed_render_reuses_text(query, monkeypatch):
    commands, _, service, event = query
    avatars = SimpleNamespace(load=AsyncMock(return_value=[None]))
    expected = "本群关系排行（按亲近度排序）：\n1. 合成昵称：亲近却戒备"
    assert asyncio.run(
        commands.rank_reply(service, event, image_enabled=False, avatar_service=avatars)
    ) == ("text", expected)
    avatars.load.assert_not_called()
    monkeypatch.setattr(
        rank_image, "render_ranking", Mock(side_effect=OSError("synthetic disk full"))
    )
    assert asyncio.run(commands.rank_reply(service, event, avatar_service=avatars)) == (
        "text",
        expected,
    )
    assert service.ranking.await_count == 2  # 每个请求只查一次，失败不重查。


def test_empty_ranking_does_not_render_or_fetch_avatars(query, monkeypatch):
    commands, _, service, event = query
    service.ranking.return_value = []
    renderer = Mock()
    monkeypatch.setattr(rank_image, "render_ranking", renderer)
    avatars = SimpleNamespace(load=AsyncMock())
    assert asyncio.run(commands.rank_reply(service, event, avatar_service=avatars)) == (
        "text",
        "本群还没有关系记录。",
    )
    renderer.assert_not_called()
    avatars.load.assert_not_called()


def test_render_contains_nickname_avatar_and_relation_but_ignores_private_fields(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(rank_image.tempfile, "gettempdir", lambda: str(tmp_path))
    source = Image.new("RGB", (64, 64), (50, 220, 110))
    output = io.BytesIO()
    source.save(output, "PNG")
    words = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        words.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    rows = [
        dict(
            rank=1,
            member="合成昵称",
            relation_title="亲近却戒备",
            user_id="secret-id",
            favor=87.6,
            trust=-71.3,
            impression="私有证据",
        )
    ]
    path = Path(rank_image.render_ranking(rows, 1, avatars=[output.getvalue()]))
    with Image.open(path) as card:
        assert card.width == 960 and card.height < 1500
        assert card.getpixel((143, 437)) == (50, 220, 110)
    assert "合成昵称" in words and "亲近却戒备" in words
    assert all(
        secret not in "|".join(words)
        for secret in ("87.6", "71.3", "secret-id", "私有证据")
    )
    assert len(list(tmp_path.rglob("*.png"))) == 1
    path.unlink()


def test_render_bounds_long_names_ten_rows_and_bad_avatar(tmp_path, monkeypatch):
    monkeypatch.setattr(rank_image.tempfile, "gettempdir", lambda: str(tmp_path))
    rows = [
        dict(rank=i + 1, member="昵称" * 1000, relation_title="关系" * 1000)
        for i in range(50)
    ]
    path = Path(
        rank_image.render_ranking(
            rows, avatars=[b"not an image"] * 50, font_path="missing-custom-font"
        )
    )
    with Image.open(path) as image:
        assert image.size == (960, 1340)
    path.unlink()


def test_render_busy_fails_without_creating_files(tmp_path, monkeypatch):
    monkeypatch.setattr(rank_image.tempfile, "gettempdir", lambda: str(tmp_path))
    rank_image._RENDER_SLOTS.acquire()
    rank_image._RENDER_SLOTS.acquire()
    try:
        with pytest.raises(RuntimeError, match="繁忙"):
            rank_image.render_ranking([])
        assert list(tmp_path.iterdir()) == []
    finally:
        rank_image._RENDER_SLOTS.release()
        rank_image._RENDER_SLOTS.release()


@pytest.mark.parametrize("nickname", [None, "", " \t\n"])
def test_missing_nickname_never_exposes_account(query, nickname):
    _, tools, service, event = query
    service.ranking.return_value[0].nickname = nickname
    public, self_rank, avatar_ids = asyncio.run(tools.ranking_rows(service, event, 10))
    assert public == [dict(rank=1, member="未命名成员", relation_title="亲近却戒备")]
    assert self_rank == 1 and avatar_ids == ["8001"]
    assert "8001" not in tools.ranking_text(public)


def test_oversized_avatar_is_rejected_before_pixel_decode(monkeypatch):
    source = Image.new("RGB", (1024, 1024), "green")
    payload = io.BytesIO()
    source.save(payload, "PNG")
    assert len(payload.getvalue()) < 131072
    canvas = Image.new("RGB", (80, 80))
    draw = ImageDraw.Draw(canvas)
    font = rank_image._font("", 25)
    convert = Mock(side_effect=AssertionError("oversized image must not decode"))
    monkeypatch.setattr(Image.Image, "convert", convert)
    rank_image._avatar(canvas, draw, 10, 10, "合成", payload.getvalue(), font, "white")
    convert.assert_not_called()
