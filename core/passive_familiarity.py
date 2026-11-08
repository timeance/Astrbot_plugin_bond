"""眼熟模式的配置契约；候选窗口和奖励结算使用同一份加载快照。"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PassiveFamiliarityConfig:
    amount: float = 0.3
    ceiling: float = 60.0
    cooldown_minutes: int = 30
    window_minutes: int = 10
    member_limit: int = 3

    def __post_init__(self):
        for value in (self.amount, self.ceiling):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 <= value <= 100
            ):
                raise ValueError("眼熟模式加分和上限须在 0～100 之间")
        for value in (self.cooldown_minutes, self.window_minutes):
            if type(value) is not int or not 1 <= value <= 1000000:
                raise ValueError("眼熟模式间隔和检查范围须为 1～1000000 的整数分钟")
        if type(self.member_limit) is not int or not 0 <= self.member_limit <= 1000000:
            raise ValueError("眼熟模式检查人数须为 0～1000000 的整数，0 表示不限制人数")

    @classmethod
    def from_config(cls, config=None):
        config = {} if config is None else config
        return cls(
            **{key: config[key] for key in cls.__dataclass_fields__ if key in config}
        )
