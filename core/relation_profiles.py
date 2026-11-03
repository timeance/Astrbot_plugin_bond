"""关系增长模板：构造规则和每日限幅，不保存或重算成员状态。"""

from .relation_policy import DEFAULT_RULES, RelationPolicy


def build_relation_profile(mode: str) -> tuple[RelationPolicy, dict]:
    """返回独立规则实例及每日正/负额度；Galgame 只加快 F/A 正向请求。"""
    if mode not in ("realistic", "galgame"):
        raise ValueError(f"未知关系增长模式: {mode}")

    caps = dict(familiarity=(2, 0), affinity=(3, 6), trust=(1, 4))
    if mode == "realistic":
        return RelationPolicy(), caps

    # 在规则构造时缩放，沿用 RelationPolicy 的强度、舍入和单次上限。
    rules = {
        event: {
            dimension: (
                value * 1.25
                if dimension in ("familiarity", "affinity") and value > 0
                else value
            )
            for dimension, value in rule.items()
        }
        for event, rule in DEFAULT_RULES.items()
    }
    caps.update(familiarity=(3, 0), affinity=(4.5, 6))
    return RelationPolicy(rules=rules), caps
