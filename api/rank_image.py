"""月读空间风格的公开排行；固定十人以内，仅绘制昵称、头像和关系称谓。"""

from __future__ import annotations

import io
import random
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

_ROOT = Path(__file__).resolve().parents[1]
_FONT = _ROOT / "resources/fonts/BondSansSC-Regular.ttf"
_RENDER_SLOTS = threading.BoundedSemaphore(2)
_CHARACTER_PATHS = tuple(
    _ROOT / "resources/ranking" / f"yachiyo-{pose}.png"
    for pose in (
        "wave",
        "peace",
        "heart",
        "hug",
        "pout",
        "laugh",
        "normal-smile",
        "normal-wave",
        "normal-hug",
    )
)
_CHARACTER_LOCK = threading.Lock()
_LAST_CHARACTER: Path | None = None
_LAST_CLEANUP = 0.0
_WIDTH = 960
_INK = "#f3f0ff"
_MUTED = "#aaa9cf"
_GOLD = "#f4d799"


def _font(custom: str, size: int):
    for path in (custom, str(_FONT)):
        if path:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    # 缺字时回退文字消息，避免向群内发送满是方框的图片。
    raise OSError("排行中文字体不可用")


def _label(value: object, limit: int = 80) -> str:
    text = str(value or "")[: limit * 2]
    text = "".join(c for c in text if not unicodedata.category(c).startswith("C"))
    return " ".join(text.split())[:limit]


def _fit(draw, text, font, width):
    if draw.textlength(text, font=font) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid] + "…", font=font) <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


def _center(draw, box, text, font, fill):
    x0, y0, x1, y1 = box
    draw.text(((x0 + x1) / 2, (y0 + y1) / 2), text, font=font, fill=fill, anchor="mm")


def _star(draw, x, y, radius, fill):
    draw.polygon(
        [
            (x, y - radius),
            (x + radius / 3, y - radius / 3),
            (x + radius, y),
            (x + radius / 3, y + radius / 3),
            (x, y + radius),
            (x - radius / 3, y + radius / 3),
            (x - radius, y),
            (x - radius / 3, y - radius / 3),
        ],
        fill=fill,
    )


def _avatar(image, draw, x, y, name, data, font, accent):
    size = 56
    photo = None
    if isinstance(data, bytes) and len(data) <= 131072:
        try:
            with Image.open(io.BytesIO(data)) as original:
                if (
                    original.format in {"PNG", "JPEG", "WEBP"}
                    and max(original.size) <= 512
                ):
                    photo = ImageOps.fit(
                        original.convert("RGB"),
                        (size, size),
                        method=Image.Resampling.LANCZOS,
                    )
        except (OSError, ValueError, Image.DecompressionBombError):
            pass
    draw.ellipse((x - 3, y - 3, x + size + 3, y + size + 3), fill=accent)
    if photo is None:
        draw.ellipse((x, y, x + size, y + size), fill="#343255")
        _center(draw, (x, y, x + size, y + size), name[:1] or "·", font, _INK)
    else:
        mask = Image.new("L", (size, size))
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        image.paste(photo, (x, y), mask)


def _output_dir():
    global _LAST_CLEANUP
    directory = Path(tempfile.gettempdir()) / "astrbot_plugin_bond" / "ranking"
    directory.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if now - _LAST_CLEANUP > 60:
        _LAST_CLEANUP = now
        # 仅扫描本插件的专用目录，不在系统临时目录扫描其他应用文件。
        for path in directory.glob("bond_rank_*.png"):
            try:
                if path.stat().st_mtime < now - 3600:
                    path.unlink()
            except OSError:
                pass
    return directory


def _load_character():
    global _LAST_CHARACTER
    # 选择与解码共用锁，使并发渲染不会同时选中上一款。
    with _CHARACTER_LOCK:
        candidates = [path for path in _CHARACTER_PATHS if path != _LAST_CHARACTER]
        random.shuffle(candidates)
        # 其他款均不可用时，仍可使用唯一可用的上一款。
        if _LAST_CHARACTER in _CHARACTER_PATHS:
            candidates.append(_LAST_CHARACTER)
        for path in candidates:
            try:
                with Image.open(path) as source:
                    character = source.convert("RGBA")
            except (OSError, ValueError, Image.DecompressionBombError):
                continue
            _LAST_CHARACTER = path
            return character
    return None


def render_ranking(
    rows: list[dict],
    self_rank: int | None = None,
    *,
    font_path: str = "",
    avatars: list[bytes | None] | None = None,
) -> str:
    """有界线程绘制；拒绝忙碌时排队分配大图，由调用方回退文字。"""
    if not _RENDER_SLOTS.acquire(blocking=False):
        raise RuntimeError("排行图片绘制繁忙")
    try:
        return _render(rows[:10], self_rank, font_path, (avatars or [])[:10])
    finally:
        _RENDER_SLOTS.release()


def _render(rows, self_rank, font_path, avatars):
    height = 380 + max(1, len(rows)) * 86 + 100
    image = Image.new("RGB", (_WIDTH, height))
    draw = ImageDraw.Draw(image)
    # 少量逐行渐变和固定星点，无逐像素 Python 循环。
    for y in range(height):
        t = y / height
        draw.line(
            (0, y, _WIDTH, y), fill=(int(18 + 8 * t), int(25 - 4 * t), int(58 + 4 * t))
        )
    for i in range(52):
        x, y = (i * 173 + 31) % 940 + 10, (i * 97 + 19) % 330
        if i % 7 == 0:
            _star(draw, x, y, 5, "#c1c2eb")
        else:
            draw.ellipse((x, y, x + 2, y + 2), fill="#727aa9")
    draw.arc((555, -155, 1050, 342), 5, 340, fill="#576599", width=2)
    draw.arc((575, -135, 1030, 322), 35, 325, fill="#b0bfe0", width=1)
    draw.line(
        [(28, 303), (178, 278), (330, 315), (484, 280), (612, 320), (930, 290)],
        fill="#536180",
        width=1,
    )
    for x, y in ((178, 278), (330, 315), (484, 280), (612, 320)):
        _star(draw, x, y, 4, _GOLD)

    title_font = _font(font_path, 48)
    heading_font = _font(font_path, 32)
    body_font = _font(font_path, 25)
    small_font = _font(font_path, 20)
    tiny_font = _font(font_path, 17)
    draw.rounded_rectangle(
        (42, 38, 255, 72), radius=16, fill="#303652", outline="#646b93"
    )
    _center(draw, (42, 38, 255, 72), "TSUKUYOMI · BOND", tiny_font, _GOLD)
    draw.text((42, 94), "月读空间", font=title_font, fill=_INK)
    draw.text((44, 169), "相处的轨迹，都在这里。", font=body_font, fill="#c2c7e6")
    draw.text((44, 218), "熟悉 · 亲近 · 信任", font=small_font, fill=_MUTED)
    character = _load_character()
    if character is not None:
        try:
            character.thumbnail((288, 288), Image.Resampling.LANCZOS)
            position = (
                642 + (288 - character.width) // 2,
                24 + (288 - character.height) // 2,
            )
            image.paste(character, position, character)
        finally:
            character.close()
    draw.line((42, 333, 918, 333), fill="#575d83", width=1)
    draw.text((44, 345), "本群关系排行", font=heading_font, fill=_INK)
    draw.text((915, 359), "按亲近度排序", font=small_font, fill=_MUTED, anchor="rm")

    for i, row in enumerate(rows):
        y = 400 + i * 86
        own = row.get("rank") == self_rank
        accent = "#a5dcec" if own else (_GOLD if i == 0 else "#8d91ba")
        draw.rounded_rectangle(
            (40, y, 920, y + 74),
            radius=17,
            fill="#303b60" if own else "#242d4d",
            outline=accent if own else "#444664",
            width=2 if own else 1,
        )
        _center(
            draw,
            (50, y, 105, y + 74),
            f"{i + 1:02d}",
            body_font,
            _GOLD if i < 3 else _MUTED,
        )
        member = _label(row.get("member")) or "群成员"
        title = _label(row.get("relation_title")) or "关系未记录"
        _avatar(
            image,
            draw,
            115,
            y + 9,
            member,
            avatars[i] if i < len(avatars) else None,
            body_font,
            accent,
        )
        nick = _fit(draw, member, body_font, 350)
        draw.text((190, y + 37), nick, font=body_font, fill=_INK, anchor="lm")
        if own:
            nx = min(550, int(202 + draw.textlength(nick, font=body_font)))
            draw.rounded_rectangle(
                (nx, y + 24, nx + 35, y + 50), radius=10, fill="#a5dcec"
            )
            _center(draw, (nx, y + 24, nx + 35, y + 50), "你", tiny_font, "#22314f")
        draw.rounded_rectangle(
            (600, y + 17, 896, y + 57), radius=20, fill="#353957", outline="#62678c"
        )
        _center(
            draw,
            (610, y + 17, 886, y + 57),
            _fit(draw, title, body_font, 266),
            body_font,
            "#e8dfff",
        )
    if not rows:
        _center(draw, (40, 400, 920, 474), "本群还没有关系记录", body_font, _MUTED)
    footer = height - 61
    draw.line((42, footer - 15, 918, footer - 15), fill="#434c70", width=1)
    draw.text((44, footer), "关系有层次，表达有个性。", font=small_font, fill=_MUTED)
    draw.text((916, footer), "BOND", font=small_font, fill=_GOLD, anchor="ra")

    with tempfile.NamedTemporaryFile(
        prefix="bond_rank_", suffix=".png", dir=_output_dir(), delete=False
    ) as handle:
        path = Path(handle.name)
    try:
        image.save(path, "PNG")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        image.close()
    return str(path)
