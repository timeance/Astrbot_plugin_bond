"""自由称谓的配置、公开格式与独立语义复核协议。"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass

from .relation_lexicon import LEXICON_VERSION

TITLE_VERSION = "2:" + LEXICON_VERSION


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def score_buckets(values):
    return [math.floor(value / 5) for value in values]


@dataclass(frozen=True)
class TitleConfig:
    enabled: bool = False
    provider_id: str = ""
    refresh_cooldown_hours: float = 12
    daily_generation_limit: int = 30
    generation_timeout_seconds: float = 30

    @classmethod
    def from_config(cls, raw):
        values = dict(raw or {})
        mode = values.pop("mode", "")
        if "enabled" not in values and mode:
            if mode not in {"fixed", "llm"}:
                raise ValueError("称谓模式无效")
            values["enabled"] = mode == "llm"
        return cls(**values)

    def __post_init__(self):
        if type(self.enabled) is not bool or not isinstance(self.provider_id, str):
            raise ValueError("称谓开关或模型配置无效")
        for key, lo, hi in (
            ("refresh_cooldown_hours", 12, 720),
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
            or not 0 <= self.daily_generation_limit <= 30
        ):
            raise ValueError("称谓每日调用上限应为 0..30")


def _object(raw):
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON 字段重复")
            result[key] = value
        return result

    if not isinstance(raw, str) or len(raw) > 512:
        raise ValueError("称谓响应须为有界 JSON")
    data = json.loads(raw, object_pairs_hook=unique_fields)
    if not isinstance(data, dict):
        raise ValueError("称谓响应须为 JSON 对象")
    return data


def parse_title(raw, description=None):
    """本地只检验公开格式；关系含义交给独立模型复核。"""
    data = _object(raw)
    if set(data) != {"title"} or not isinstance(data["title"], str):
        raise ValueError("称谓须只含 title 字段")
    value = data["title"]
    # 归一化前检查，避免控制符、全角账号和数字变体藏入公开文字。
    if any(unicodedata.category(char)[0] in "CNZ" for char in value):
        raise ValueError("称谓不能包含控制字符、数字或空白")
    title = unicodedata.normalize("NFKC", value)
    if not 2 <= len(title) <= 12 or any(
        not unicodedata.category(char).startswith("L") and char not in "·、"
        for char in title
    ):
        raise ValueError("称谓须为 2..12 字，不含账号、URL 或格式标记")
    return title


def parse_title_review(raw):
    data = _object(raw)
    if set(data) != {"valid"} or type(data["valid"]) is not bool:
        raise ValueError("称谓复核须只含布尔 valid 字段")
    return data["valid"]
