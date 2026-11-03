"""亲近度时间衰减的纯函数。

使用 baseline + (stored - baseline) * 0.5 ** (闲置天数 / h)。这是产品节奏
模型，不是经过真人关系校准的记忆规律。新安装参数由配置与 main.py 装配。
推荐的固定半衰期直接取当前配置；可选巩固模式保留历史值，但读取和写入都
受当前基础值及上限约束。读取不改库，正常写入时才结算衰减。
"""

from __future__ import annotations

from .decimal import round1

# 半衰期下限（保险丝）：即使配置被改坏，最快也是每天 ~13% 的比例损失
HALF_LIFE_MIN = 5.0


def effective_favor(
    stored: float,
    updated_at: float,
    now: float,
    *,
    half_life: float,
    baseline: float,
    floor: float | None = None,
) -> float:
    """指数遗忘：存量距离按 0.5^(闲置天数/h) 衰减，向 baseline 收敛不越界。

    - updated_at <= 0（从未互动）：不衰减。
    - half_life 缺失/非法或低于 HALF_LIFE_MIN 时按 HALF_LIFE_MIN 计
      （保险丝：配置被改坏也不会快于每天 ~13% 的比例损失）。
    - floor（等级衰减地板，星露谷式）：衰减后的有效值不低于 floor 时取
      floor——挚爱最多衰到"亲密"下沿，久别重逢不掉出熟悉区间。仅当
      floor 高于 baseline 时生效（地板必须落在衰减目标之上才有意义）；
      stored 本身低于 floor 时不抬高（地板只托底不上涨）。
    - 返回值收敛到一位小数。
    """
    h = max(HALF_LIFE_MIN, float(half_life)) if half_life else HALF_LIFE_MIN
    if updated_at <= 0 or now <= updated_at:
        return round1(stored)
    idle_days = (now - updated_at) / 86400.0
    factor = 0.5 ** (idle_days / h)
    eff = baseline + (stored - baseline) * factor
    # 不越过 baseline（stored 在两侧时均收敛向它）
    if stored > baseline:
        eff = max(baseline, eff)
    elif stored < baseline:
        eff = min(baseline, eff)
    # 等级地板：托底不上涨（stored 已在地板下则维持衰减结果）
    if floor is not None and stored > floor > baseline:
        eff = max(float(floor), eff)
    return round1(eff)


def resolve_half_life(h: float, *, base: float, growth: float, h_max: float) -> float:
    """固定模式用当前基础值；巩固模式将历史值限制在当前配置范围内。"""
    base = max(HALF_LIFE_MIN, float(base))
    if growth <= 1:
        return base
    return min(max(base, float(h or 0)), max(base, float(h_max)))


def consolidate_half_life(
    h: float,
    *,
    base: float = 10.0,
    growth: float = 1.3,
    h_max: float = 60.0,
    positive: bool,
) -> float:
    """互动后结算新的半衰期（巩固规则）。

    - 正向互动：h = min(h_max, max(h, base) × growth)（首次互动从 base 起步）；
    - 非正向互动：使用当前配置解析后的 h，不额外巩固；
    - h 非法（<=0）时回落 base；输入输出均不低于 HALF_LIFE_MIN 保险丝。
      收敛到一位小数。
    """
    cur = resolve_half_life(h, base=base, growth=growth, h_max=h_max)
    if not positive:
        return round1(cur)
    return round1(
        min(
            max(HALF_LIFE_MIN, float(base), float(h_max)), cur * max(1.0, float(growth))
        )
    )
