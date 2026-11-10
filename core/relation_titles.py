"""动态称谓的公开表达契约；不允许模型输出数值、身份或自由文本资料。"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass

from .relation_lexicon import LEXICON_VERSION, CELLS, TERMS

TITLE_VERSION = "1:" + LEXICON_VERSION


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


@dataclass(frozen=True)
class TitleConfig:
    mode: str = "fixed"
    provider_id: str = ""
    refresh_cooldown_hours: float = 6
    daily_generation_limit: int = 10
    generation_timeout_seconds: float = 30

    def __post_init__(self):
        if self.mode not in {"fixed", "llm"} or not isinstance(self.provider_id, str):
            raise ValueError("称谓模式或模型配置无效")
        for key, lo, hi in (
            ("refresh_cooldown_hours", 0.1, 720),
            ("generation_timeout_seconds", 1, 120),
        ):
            value = getattr(self, key)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not lo <= value <= hi
            ):
                raise ValueError(f"{key} 应在 {lo}..{hi} 范围内")
        if (
            type(self.daily_generation_limit) is not int
            or not 0 <= self.daily_generation_limit <= 1000
        ):
            raise ValueError("称谓每日调用上限应为 0..1000")


def allowed_titles(description):
    """按关系事实约束词族；负向混合关系暂不开启动态表达。

    所有可输出文字来自本地审定词族，模型只能选择措辞，不能复制印象中
    的姓名、私人资料、虚构经历或关系承诺。人格不同不强求词语不同。
    """
    f, a, t = description.bands
    if f == 0 or a < 2 or t < 2:
        return ()
    nouns = (
        (),
        ("新相识", "眼熟之人"),
        ("相识", "熟面孔"),
        ("熟人",),
        ("熟人", "老熟人"),
    )[f]
    if a >= 3 and f >= 2:
        nouns = ("朋友", "伙伴")
    if a == 2:
        prefixes = {
            2: ("平常往来的", "偶尔聊聊的"),
            3: ("相处放心的", "可以放心的"),
            4: ("值得信赖的", "可信的"),
        }[t]
    elif a == 3:
        prefixes = {
            2: ("有好感的", "想亲近的"),
            3: ("亲切又放心的", "亲近而放心的"),
            4: ("亲近又可信的", "亲切而信赖的"),
        }[t]
    else:
        prefixes = {
            2: ("亲密而保留的", "亲近但仍保留的"),
            3: ("亲密又放心的", "亲密而安心的"),
            4: ("亲密又信赖的", "亲密而可信的"),
        }[t]
    # 词典的 F3/F8/F20/A25 展示边界不单独使动态称谓失效。
    base_title = TERMS[CELLS[f][a][t]][0]
    return tuple(dict.fromkeys([base_title] + [p + n for p in prefixes for n in nouns]))


def parse_title(raw, description):
    data = json.loads(raw)
    if (
        not isinstance(data, dict)
        or set(data) != {"title"}
        or not isinstance(data["title"], str)
    ):
        raise ValueError("称谓须只含 title 字段")
    title = unicodedata.normalize("NFKC", data["title"])
    if not 2 <= len(title) <= 12 or title not in allowed_titles(description):
        raise ValueError("称谓超出当前关系允许的公开表达")
    return title
