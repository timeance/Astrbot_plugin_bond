"""统一关系模式：仅缩放正向请求与正向每日额度。"""

from collections.abc import Mapping
import math
from numbers import Real

from .relation_policy import RELATION_DIMENSIONS, RelationPolicy

DEFAULT_DAILY_CAPS = {
    "familiarity": (5.0, 0.0),
    "affinity": (10.0, 8.0),
    "trust": (2.0, 5.0),
}


def positive_mode_multiplier(mode: str = "galgame") -> float:
    """模式只决定统一正向倍率。"""
    if mode not in ("realistic", "galgame"):
        raise ValueError(f"未知关系增长模式: {mode}")
    return 2.0 if mode == "galgame" else 1.0


def build_relation_profile(
    mode: str = "galgame",
    *,
    rules=None,
    event_caps=None,
    caps: Mapping | None = None,
) -> tuple[RelationPolicy, dict]:
    """显式基础配置优先；模式不修改基础规则，不触及模型或衰减配置。"""
    multiplier = positive_mode_multiplier(mode)
    base_caps = dict(DEFAULT_DAILY_CAPS)
    for dimension, pair in (caps or {}).items():
        if dimension not in RELATION_DIMENSIONS:
            raise ValueError(f"未知关系维度: {dimension}")
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError(f"每日额度 {dimension} 必须是正/负额度对")
        if any(
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
            or value < 0
            for value in pair
        ):
            raise ValueError(f"每日额度 {dimension} 必须是非负有限数值")
        base_caps[dimension] = tuple(float(value) for value in pair)
    return (
        RelationPolicy(rules, event_caps, positive_multiplier=multiplier),
        {
            key: (positive * multiplier, negative)
            for key, (positive, negative) in base_caps.items()
        },
    )
