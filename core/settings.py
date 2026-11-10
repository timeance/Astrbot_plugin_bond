"""Bond 页面可编辑的详细设置；沿用宿主配置，不保存第二份状态。"""

from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from .level_economy import EconomyConfig
from .naming import LEVEL_NAME_BY_KEY


class SettingsRevisionError(ValueError):
    """提交的配置版本已失效或缺失。"""


# 基础配置与详细参数共用宿主配置；评估高级参数由评估页编辑。
SECTIONS = {
    "basic": (
        "基础设置",
        [
            "assessment.enabled",
            "assessment.small_provider_id",
            "assessment.large_provider_id",
            "assessment.assessment_mode",
            "group_whitelist",
            "default_scope_mode",
            "master_ids",
            "passive_familiarity",
            "relation_mode",
            "ignored_bot_ids",
            "decay.grace_days",
            "decay.maintenance_strategy",
            "timezone",
        ],
    ),
    "titles": ("个性化功能", ["inject.enabled", "relation_title"]),
    "familiarity": (
        "熟悉度变化",
        [
            "relation_daily_caps.familiarity_up",
            "relation_daily_caps.familiarity_down",
            "passive_familiarity_rules",
            "familiarity_growth",
            "decay.familiarity_enabled",
            "decay.familiarity_half_life",
            "decay.familiarity_baseline",
        ],
    ),
    "affinity": (
        "亲近度变化",
        [
            "relation_daily_caps.affinity_up",
            "relation_daily_caps.affinity_down",
            "economy",
            "decay.enabled",
            "decay.half_life_base",
            "decay.half_life_growth",
            "decay.half_life_max",
            "decay.affinity_baseline",
            "decay.floor_enabled",
        ],
    ),
    "trust": (
        "信任度变化",
        [
            "relation_daily_caps.trust_up",
            "relation_daily_caps.trust_down",
            "decay.trust_enabled",
            "decay.trust_half_life",
            "decay.trust_baseline",
        ],
    ),
    "memory": (
        "印象与近期事件",
        ["inject", "impression", "judge.provider_id", "judge.timeout_sec"],
    ),
    "commands": ("查询与排行", ["command", "render.font_path"]),
    "diagnostics": ("系统诊断", ["diagnostics"]),
}


def fields():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(
            encoding="utf-8"
        )
    )
    result = []

    def visit(path, node, group, title):
        if path in {"decay.baseline", "economy.preset", "relation_title.mode"}:
            return
        if group != "basic" and path in SECTIONS["basic"][1]:
            return
        if group != "titles" and path == "inject.enabled":
            return
        if node["type"] == "object":
            for key, child in node["items"].items():
                visit(path + "." + key, child, group, title)
            return
        key = path.rsplit(".", 1)[-1]
        result.append(
            dict(
                path=path,
                group=group,
                title=title,
                label=node.get("description", key),
                hint=node.get("hint", ""),
                type=node["type"],
                default=node.get("default"),
                options=node.get("options", []),
                special=node.get("_special"),
            )
        )

    for group, (title, paths) in SECTIONS.items():
        for path in paths:
            node = schema
            for part in path.split("."):
                if "items" in node:
                    node = node["items"]
                node = node[part]
            visit(path, node, group, title)
    return result


def snapshot(config):
    definitions = fields()
    # 关闭规则时仍展示基础参数；只解析副本，不把兼容预设写回宿主配置。
    economy = EconomyConfig.from_config(
        {**(config.get("economy") or {}), "enabled": True}
    )
    economy_defaults = {
        "economy." + key: value
        for key, value in vars(economy).items()
        if key != "level_mult"
    }
    economy_defaults.update(
        {
            "economy.level_mult." + key: economy.level_mult[name]
            for key, name in LEVEL_NAME_BY_KEY.items()
        }
    )
    values = {}
    for field in definitions:
        value = config
        for part in field["path"].split("."):
            value = value.get(part, {}) if isinstance(value, dict) else {}
        fallback = economy_defaults.get(field["path"], field["default"])
        values[field["path"]] = fallback if value == {} else value
    revision = hashlib.sha256(
        json.dumps(
            {"values": values, "assessment": config.get("assessment") or {}},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return dict(fields=definitions, values=values, revision=revision)


def validate_patch(values):
    if not isinstance(values, dict) or not values:
        raise ValueError("请提交要修改的设置")
    from .relation_titles import TitleConfig

    TitleConfig.from_config(
        {
            key.split(".", 1)[1]: value
            for key, value in values.items()
            if key.startswith("relation_title.")
        }
    )
    from .passive_familiarity import PassiveFamiliarityConfig

    passive_values = {
        path.split(".", 1)[1]: value
        for path, value in values.items()
        if path.startswith("passive_familiarity_rules.")
    }
    PassiveFamiliarityConfig.from_config(passive_values)
    allowed = {f["path"]: f for f in fields()}
    for path, value in values.items():
        if path not in allowed:
            raise ValueError("不支持的设置")
        f = allowed[path]
        typ = f["type"]
        if typ == "bool":
            valid = type(value) is bool
        elif typ == "list":
            from .scope import account_id

            valid = isinstance(value, list) and len(value) <= 1000
            if valid:
                for item in value:
                    account_id(item)
        elif typ == "int":
            valid = type(value) is int
        elif typ == "float":
            valid = type(value) in (int, float) and math.isfinite(value)
        else:
            valid = isinstance(value, str) and len(value) <= 4096
        if not valid:
            raise ValueError("设置类型无效")
        if f["options"] and value not in f["options"]:
            raise ValueError("选项无效")
        if type(value) in (int, float):
            minimum = (
                1
                if path.endswith(
                    (
                        "interval",
                        "ranking_limit",
                        "rows_per_col",
                        "half_life_base",
                        "half_life_max",
                        "trust_half_life",
                        "familiarity_half_life",
                    )
                )
                else 0
            )
            if path in {
                "decay.half_life_base",
                "decay.half_life_max",
                "decay.trust_half_life",
                "decay.familiarity_half_life",
            }:
                minimum = 5
            if path in {
                "decay.half_life_growth",
                "inject.memory_sig_window_mult",
                "economy.repair_scale_high",
                "economy.repair_scale_low",
            }:
                minimum = 1
            maximum = (
                100
                if path
                in {
                    "decay.familiarity_baseline",
                    "decay.affinity_baseline",
                    "decay.trust_baseline",
                }
                else 1000000
            )
            if path == "diagnostics.max_size_mb":
                minimum, maximum = 1, 10000
            if path == "relation_title.refresh_cooldown_hours":
                minimum, maximum = 12, 720
            if path == "relation_title.daily_generation_limit":
                maximum = 30
            if path == "inject.memory_count":
                maximum = 10
            if path in {"economy.repair_factor", "economy.same_day_decay"}:
                maximum = 1
            if not minimum <= value <= maximum:
                raise ValueError("数值超出范围")
        if path == "master_ids":
            from .identity import parse_master_ids
            from .scope import account_id

            for item in parse_master_ids(value):
                account_id(item)
        if path == "timezone":
            ZoneInfo(value)
