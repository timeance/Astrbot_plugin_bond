"""PR-2 三维关系的纯规则层。

本模块只负责状态校验、事件到三维增量的映射和称谓解析，不读写存储，
也不接受或计算任何总分。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from numbers import Real

from .decimal import round1

RELATION_DIMENSIONS = ("familiarity", "affinity", "trust")
RELATION_BOUNDS = {
    "familiarity": (0.0, 100.0),
    "affinity": (-100.0, 100.0),
    "trust": (-100.0, 100.0),
}


def affinity_bounds_for_undo(
    relation_delta: Mapping[str, object] | None,
    *,
    legacy_min: float,
    legacy_max: float,
) -> tuple[float, float]:
    """返回撤销流水时亲近度的值域。三维流水固定使用关系值域，旧标量流水保留配置值域。"""
    if relation_delta:
        return RELATION_BOUNDS["affinity"]
    return float(legacy_min), float(legacy_max)


class EventType(str, Enum):
    """PR-2 支持的关系事件类型。"""

    NORMAL_QUESTION = "normal_question"
    THANKS = "thanks"
    FRIENDLY = "friendly"
    JOKE = "joke"
    CRITICISM = "criticism"
    INSULT = "insult"
    MANIPULATION = "manipulation"
    APOLOGY = "apology"
    UNCERTAIN = "uncertain"


EVENT_TYPES = tuple(event.value for event in EventType)

# F：确认的交互暴露；A：温暖/疏离；T：涉及风险时的可靠性、诚实和边界尊重。
# 礼貌、示好或道歉本身不是可靠性修复证据。限幅仍由 FavorService/存储负责。
DEFAULT_RULES: dict[str, dict[str, float]] = {
    EventType.NORMAL_QUESTION: {"familiarity": 0.1},
    EventType.THANKS: {"familiarity": 0.2, "affinity": 0.5, "trust": 0.0},
    EventType.FRIENDLY: {"familiarity": 0.3, "affinity": 0.8, "trust": 0.0},
    EventType.JOKE: {"familiarity": 0.2, "affinity": 0.5},
    EventType.CRITICISM: {"familiarity": 0.1},
    EventType.INSULT: {"affinity": -1.0, "trust": -1.5},
    EventType.MANIPULATION: {"affinity": -0.5, "trust": -2.0},
    EventType.APOLOGY: {"familiarity": 0.1, "affinity": 0.7, "trust": 0.0},
    EventType.UNCERTAIN: {},
}

# 单事件上限按维度配置，避免单条高强度事件绕过本地策略造成大幅跳变。
DEFAULT_EVENT_CAPS: dict[str, float] = {
    "familiarity": 1.0,
    "affinity": 2.0,
    "trust": 2.0,
}

DEFAULT_THRESHOLDS: dict[str, dict[str, float]] = {
    "familiarity": {"eyeball": 10.0, "familiar": 40.0},
    "affinity": {"close": 40.0, "distant": -40.0},
    "trust": {"trusted": 40.0, "guarded": -40.0},
}

DEFAULT_LABELS: dict[str, str] = {
    "stranger": "陌生",
    "eyeball": "眼熟",
    "familiar": "熟悉",
    "close": "亲近",
    "distant": "疏远",
    "trusted": "信任",
    "guarded": "戒备",
}


def _number(value: object, name: str) -> float:
    """校验有限数值类型，不提前舍入。"""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} 必须是数字且不能是 bool")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数值")
    return number


def _bounded_number(value: object, name: str, lower: float, upper: float) -> float:
    number = _number(value, name)
    if number < lower or number > upper:
        raise ValueError(f"{name} 必须在 {lower:g}..{upper:g} 范围内")
    return round1(number)


def _event_name(event_type: str | EventType) -> str:
    if isinstance(event_type, EventType):
        event_type = event_type.value
    if not isinstance(event_type, str):
        raise ValueError("event_type 必须是字符串")
    value = event_type.strip()
    if value not in EVENT_TYPES:
        raise ValueError(f"未知关系事件类型: {value}")
    return value


@dataclass(frozen=True)
class RelationState:
    """三维关系状态，不包含总分。"""

    familiarity: float = 0.0
    affinity: float = 0.0
    trust: float = 0.0

    def __post_init__(self) -> None:
        for dimension in RELATION_DIMENSIONS:
            lower, upper = RELATION_BOUNDS[dimension]
            value = _bounded_number(getattr(self, dimension), dimension, lower, upper)
            object.__setattr__(self, dimension, value)

    def as_dict(self) -> dict[str, float]:
        """返回可直接交给存储服务的三维字段。"""
        return {
            "familiarity": self.familiarity,
            "affinity": self.affinity,
            "trust": self.trust,
        }


class RelationPolicy:
    """把已校验的事件类型和强度转换为三维请求增量。"""

    def __init__(
        self,
        rules: Mapping[str, Mapping[str, float]] | None = None,
        event_caps: Mapping[str, Mapping[str, float] | float] | None = None,
        *,
        positive_multiplier: float = 1.0,
    ) -> None:
        self.positive_multiplier = _number(positive_multiplier, "正向模式倍率")
        if self.positive_multiplier < 0:
            raise ValueError("正向模式倍率不能为负")
        self._rules = {event: dict(DEFAULT_RULES[event]) for event in EVENT_TYPES}
        for raw_event, raw_rule in (rules or {}).items():
            event = _event_name(raw_event)
            if not isinstance(raw_rule, Mapping):
                raise ValueError(f"事件规则 {event} 必须是映射")
            for dimension, value in raw_rule.items():
                if dimension not in RELATION_DIMENSIONS:
                    raise ValueError(f"未知关系维度: {dimension}")
                self._rules[event][dimension] = _number(
                    value, f"规则 {event}.{dimension}"
                )

        self._event_caps = {event: dict(DEFAULT_EVENT_CAPS) for event in EVENT_TYPES}
        for raw_event, raw_caps in (event_caps or {}).items():
            event = _event_name(raw_event)
            if isinstance(raw_caps, Mapping):
                for dimension, value in raw_caps.items():
                    if dimension not in RELATION_DIMENSIONS:
                        raise ValueError(f"未知关系维度: {dimension}")
                    cap = _number(value, f"单事件上限 {event}.{dimension}")
                    if cap < 0:
                        raise ValueError(f"单事件上限 {event}.{dimension} 不能为负")
                    self._event_caps[event][dimension] = cap
            else:
                cap = _number(raw_caps, f"单事件上限 {event}")
                if cap < 0:
                    raise ValueError(f"单事件上限 {event} 不能为负")
                self._event_caps[event] = {
                    dimension: cap for dimension in RELATION_DIMENSIONS
                }

    def snapshot(self):
        """诊断用规则快照；复制嵌套映射，不允许观察者修改计算规则。"""
        return {
            "positive_multiplier": self.positive_multiplier,
            "rules": {key: dict(value) for key, value in self._rules.items()},
            "event_caps": {key: dict(value) for key, value in self._event_caps.items()},
        }

    def limit_deltas(
        self, event_type, deltas, *, familiarity_multiplier=1.0, rounded=True
    ):
        """截断已应用曲线及模式的请求，不重复放大。

        正向上限随模式缩放；熟悉度正向上限还随其曲线缩放。
        负向上限始终使用用户配置的基础值。
        """
        event = _event_name(event_type)
        f_mult = _number(familiarity_multiplier, "熟悉度倍率")
        if f_mult < 0:
            raise ValueError("熟悉度倍率不能为负")
        result = {}
        for key in RELATION_DIMENSIONS:
            cap = self._event_caps[event][key]
            positive_cap = cap * self.positive_multiplier
            if key == "familiarity":
                positive_cap *= f_mult
            value = max(-cap, min(positive_cap, _number(deltas[key], key)))
            result[key] = round1(value) if rounded else value
        return result

    def base_deltas(
        self, event_type: str, intensity: float = 1, *, bounded: bool = True
    ) -> dict[str, float]:
        """默认先限幅、不舍入；bounded=False 供维护诊断保留限幅前的意图。"""
        event = _event_name(event_type)
        factor = _number(intensity, "intensity")
        if factor < 0:
            raise ValueError("intensity 不能为负")
        result = {}
        for dimension in RELATION_DIMENSIONS:
            requested = self._rules[event].get(dimension, 0.0) * factor
            cap = self._event_caps[event][dimension]
            result[dimension] = max(-cap, min(cap, requested)) if bounded else requested
        return result

    def scale_positive(self, deltas, *, rounded=True) -> dict[str, float]:
        """仅缩放正向请求一次；不重复限幅或改变负向请求。"""
        result = {}
        for key in RELATION_DIMENSIONS:
            value = _number(deltas[key], key)
            if value > 0:
                value *= self.positive_multiplier
            result[key] = round1(value) if rounded else value
        return result

    def deltas(
        self, event_type: str, intensity: float = 1, *, rounded=True
    ) -> dict[str, float]:
        """基础单次限幅后应用模式倍率；曲线由事务内回调按当前状态计算。"""
        return self.scale_positive(
            self.base_deltas(event_type, intensity), rounded=rounded
        )


class RelationResolver:
    """根据三维状态输出可并存的中文称谓。"""

    def __init__(
        self,
        thresholds: Mapping[str, Mapping[str, float]] | None = None,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        # 显式传入旧配置时保留旧解析；默认调用采用五档词典。
        self._legacy = thresholds is not None or labels is not None
        self._thresholds = {
            dimension: dict(values) for dimension, values in DEFAULT_THRESHOLDS.items()
        }
        for dimension, values in (thresholds or {}).items():
            if dimension not in self._thresholds:
                raise ValueError(f"未知阈值维度: {dimension}")
            if not isinstance(values, Mapping):
                raise ValueError(f"阈值 {dimension} 必须是映射")
            for key, value in values.items():
                if key not in self._thresholds[dimension]:
                    raise ValueError(f"未知阈值名称: {dimension}.{key}")
                lower, upper = RELATION_BOUNDS[dimension]
                self._thresholds[dimension][key] = _bounded_number(
                    value, f"阈值 {dimension}.{key}", lower, upper
                )

        familiarity = self._thresholds["familiarity"]
        if familiarity["eyeball"] > familiarity["familiar"]:
            raise ValueError("familiarity 阈值必须满足 eyeball <= familiar")
        affinity = self._thresholds["affinity"]
        if affinity["distant"] > affinity["close"]:
            raise ValueError("affinity 阈值必须满足 distant <= close")
        trust = self._thresholds["trust"]
        if trust["guarded"] > trust["trusted"]:
            raise ValueError("trust 阈值必须满足 guarded <= trusted")

        self._labels = dict(DEFAULT_LABELS)
        for key, value in (labels or {}).items():
            if key not in self._labels:
                raise ValueError(f"未知称谓键: {key}")
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"称谓 {key} 必须是非空字符串")
            self._labels[key] = value.strip()

    def describe(self, state: RelationState):
        """公开称谓与模型释义共用结果；旧显式配置保持原文案。"""
        from .relation_lexicon import RelationDescription, describe

        if not isinstance(state, RelationState):
            raise ValueError("state 必须是 RelationState")
        if self._legacy:
            return RelationDescription(
                "legacy",
                self.resolve(state),
                "当前关系态度由显式自定义规则描述，不改变人格与权限。",
                (),
                self.resolve_labels(state),
            )
        return describe(state)

    def _keys(self, state: RelationState) -> tuple[str, str | None, str | None]:
        familiarity_thresholds = self._thresholds["familiarity"]
        if state.familiarity >= familiarity_thresholds["familiar"]:
            familiarity = "familiar"
        elif state.familiarity >= familiarity_thresholds["eyeball"]:
            familiarity = "eyeball"
        else:
            familiarity = "stranger"

        affinity_thresholds = self._thresholds["affinity"]
        if state.affinity >= affinity_thresholds["close"]:
            affinity = "close"
        elif state.affinity <= affinity_thresholds["distant"]:
            affinity = "distant"
        else:
            affinity = None

        trust_thresholds = self._thresholds["trust"]
        if state.trust >= trust_thresholds["trusted"]:
            trust = "trusted"
        elif state.trust <= trust_thresholds["guarded"]:
            trust = "guarded"
        else:
            trust = None
        return familiarity, affinity, trust

    def resolve_labels(self, state: RelationState) -> tuple[str, ...]:
        """返回按熟悉度、亲近度、信任度排列的结构化称谓文本。"""
        if not isinstance(state, RelationState):
            raise ValueError("state 必须是 RelationState")
        if not self._legacy:
            return self.describe(state).labels
        familiarity, affinity, trust = self._keys(state)
        keys = [familiarity]
        if affinity:
            keys.append(affinity)
        if trust:
            keys.append(trust)
        return tuple(self._labels[key] for key in keys)

    def resolve(self, state: RelationState) -> str:
        """输出可并存的中文称谓，不合成总分或单一等级。"""
        if not isinstance(state, RelationState):
            raise ValueError("state 必须是 RelationState")
        if not self._legacy:
            return self.describe(state).title
        familiarity, affinity, trust = self._keys(state)
        familiarity_label = self._labels[familiarity]

        if affinity == "close" and trust == "trusted":
            close_trust = f"{self._labels['close']}且{self._labels['trusted']}"
            return f"{familiarity_label}、{close_trust}"

        if trust == "guarded" and familiarity in {"eyeball", "familiar"}:
            prefix = familiarity_label
            if affinity:
                prefix += f"、{self._labels[affinity]}"
            return f"{prefix}但{self._labels['guarded']}"

        labels = [familiarity_label]
        if affinity:
            labels.append(self._labels[affinity])
        if trust:
            labels.append(self._labels[trust])
        return "、".join(labels)


__all__ = [
    "DEFAULT_EVENT_CAPS",
    "DEFAULT_LABELS",
    "DEFAULT_RULES",
    "DEFAULT_THRESHOLDS",
    "EVENT_TYPES",
    "EventType",
    "RelationPolicy",
    "RelationResolver",
    "RelationState",
]
