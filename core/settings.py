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
            "inject.enabled",
            "relation_mode",
            "ignored_bot_ids",
            "decay.grace_days",
            "decay.maintenance_strategy",
            "timezone",
        ],
    ),
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
LABELS = {
    "assessment.enabled": (
        "自动评估",
        "根据你与 Bot 的互动，更新熟悉度、亲近度和信任度。关闭后仅停止自动评估。",
    ),
    "assessment.assessment_mode": (
        "评估模式",
        "单模型只用日常模型；分级复核会按需复核，必须复核却未完成时不评分。",
    ),
    "assessment.small_provider_id": (
        "日常评估模型",
        "启用时必填，手动选择已配置的文本模型；不自动跟随聊天模型。",
    ),
    "assessment.large_provider_id": (
        "复核模型（可选）",
        "用于复核难判互动。留空不调用；需要复核而无法完成时不评分。",
    ),
    "inject.enabled": (
        "在回复中体现关系",
        "让 Bot 参考彼此的关系和对你的印象来回复。全局空间不带入其他群的印象和流水。",
    ),
    "ignored_bot_ids": (
        "忽略的机器人账号",
        "每项一个平台账号 ID；这些账号不参与成员记录和关系评估。",
    ),
    "relation_mode": (
        "关系模式",
        "基础模式使用所填参数；Galgame 将加分及每日加分上限各乘 2，其余不变。不改写设置，不重算历史。",
    ),
    "ranking_access": (
        "排行查询权限",
        "可设所有人或仅主人。仅主人按主人 QQ 名单核验，未填名单则无人可查；所有查询入口共用此权限，群内其他人仍能看到已发出的榜单。",
    ),
    "ranking_image": (
        "图片排行",
        "图片最多展示 10 人的昵称、头像和关系，不显示分值。头像由 QQ 提供，生成图片失败时改用文字。",
    ),
    "diagnostics.enabled": (
        "记录系统诊断",
        "记录评估与关系变化，按所选详细程度保留诊断内容。",
    ),
    "diagnostics.detail_level": (
        "诊断详细程度",
        "按所选级别记录；完整上下文包含 Bond 观察到的回复请求。",
    ),
    "diagnostics.max_size_mb": (
        "诊断容量上限（MB）",
        "所有 Bot 和数据空间合计；达到上限后清理旧记录。",
    ),
    "default_scope_mode": (
        "默认数据空间",
        "按群分别记录，或在同一 Bot 跨群共用关系；指定 Bot 的选择优先。切换不迁移历史，也不披露其他群的印象和流水。",
    ),
    "master_ids": (
        "主人 QQ",
        "用于主人标记和仅主人查榜，不授予管理权限，也不免除评估。多个 QQ 用英文逗号分隔。",
    ),
    "group_whitelist": (
        "启用 Bond 的群",
        "留空启用所有群；填写后仅在所列群记录、评估、注入和响应指令。每项一个群号，对所有 Bot 生效，已有记录保留。",
    ),
    "timezone": (
        "关系额度换日时区",
        "如 Asia/Shanghai，决定关系额度和同日递减的换日时间；调用预算固定在 UTC 00:00 刷新。",
    ),
    "memory_count": (
        "近期事件条数",
        "最多补充 10 条当前群的事件类别和理由，不带原文或分值。0 关闭；全局空间不注入近期事件。",
    ),
    "memory_days": (
        "近期事件回顾天数",
        "普通事件的回顾天数，重要事件可更久。0 取消时间筛选，不删除流水。",
    ),
    "memory_sig_threshold": (
        "重要事件亲近度阈值",
        "亲近度变化绝对值达到此数时，延长事件回顾期。0 关闭延长；不看其他两维。",
    ),
    "memory_sig_window_mult": (
        "重要事件回顾倍数",
        "重要事件的回顾天数乘以此数；1 表示与普通事件相同。",
    ),
    "interval": (
        "印象更新频率",
        "每积累设定条数的未汇总评估流水（默认 8 条），在关系变化后尝试更新。撤销记录不计，零变化流水计入；原文会发给模型，失败保留原印象。",
    ),
    "points_mode": (
        "分点记录印象",
        "开启后分点记录、合并相近要点，保留现有标签；关闭时汇总成一句话并更新标签。不清除分值和流水。",
    ),
    "judge.provider_id": (
        "印象汇总模型（可选）",
        "留空使用宿主默认模型，可能不同于当前群模型。汇总另行调用，不占互动评估预算。",
    ),
    "judge.timeout_sec": (
        "印象汇总超时（秒）",
        "超时保留原印象；0 表示不设超时。",
    ),
    "ranking_limit": (
        "排行显示人数",
        "公开排行最多显示 10 人，超出 10 的设置不生效。",
    ),
    "text_wake_enabled": (
        "文字查榜",
        "开启后可用指定短语查询本群排行，无需斜杠，仍遵守排行权限。关闭仅停用文字查榜。",
    ),
    "text_wake_ranking": (
        "排行唤醒短语",
        "多个短语用英文逗号分隔，须匹配整条消息；忽略首尾空白。",
    ),
    "font_path": (
        "排行图片字体路径",
        "留空使用内置开源中文字体，无需下载；自定义字体不可用时回退内置字体。",
    ),
    "rows_per_col": (
        "旧每列行数（已停用）",
        "当前排行固定单列，最多 10 人，此项不改变布局。",
    ),
    "decay.familiarity_baseline": (
        "熟悉度基线",
        "范围 0～100；高于基线时逐渐靠近基线，低于或等于基线时不变。",
    ),
    "decay.affinity_baseline": (
        "亲近度对称基线",
        "范围 0～100。填 20 时，高于 +20 向 +20 靠近，低于 −20 向 −20 靠近，区间内不变；托底可能更早止跌。",
    ),
    "decay.trust_baseline": (
        "信任度对称基线",
        "范围 0～100。填 20 时，高于 +20 向 +20 靠近，低于 −20 向 −20 靠近，区间内不变。",
    ),
    "decay.familiarity_enabled": (
        "启用熟悉度衰减",
        "开启后随时间向基线下降，低于或等于基线时不变；关闭后停止。临时关系期间暂停。",
    ),
    "decay.familiarity_half_life": (
        "熟悉度半衰期（天）",
        "至少 5 天。每经过此天数，熟悉度距基线的差值约减半，对已有和新成员均生效。",
    ),
    "decay.enabled": (
        "启用亲近度衰减",
        "开启后向同号基线靠近，正负基线之间不变；关闭后停止。临时关系期间暂停。",
    ),
    "floor_enabled": (
        "启用亲近度档位托底",
        "为较高的亲近度保留衰减下限，避免长期不互动后降得过低。只影响亲近度，不保证关系称谓保持不变。",
    ),
    "decay.trust_enabled": (
        "启用信任度衰减",
        "开启后向同号基线靠近，正负基线之间不变；关闭后停止。临时关系期间暂停。",
    ),
    "decay.trust_half_life": (
        "信任度半衰期（天）",
        "至少 5 天。每经过此天数，信任度距同号基线的差值约减半，不越过基线或变号。",
    ),
    "half_life_base": (
        "亲近度半衰期（天）",
        "至少 5 天。距同号基线的差值按此速度减半，托底可提前止跌；巩固倍数大于 1 时，此值为个人半衰期下限。",
    ),
    "half_life_growth": (
        "亲近度半衰期巩固倍数",
        "1 为固定半衰期；大于 1 时，每次亲近度实际增加后延长半衰期，直到上限。不重算历史互动。",
    ),
    "half_life_max": (
        "亲近度最长半衰期（天）",
        "仅巩固倍数大于 1 时使用，也限制已有成员；不应低于所设的亲近度半衰期。",
    ),
    "economy.enabled": (
        "启用亲近度增长规则",
        "控制亲近度的过滤、倍率、同日递减和增长抑制；关闭则跳过。不影响另两维、模式倍率或每日额度。",
    ),
    "preset": (
        "旧缺省参数预设",
        "只为缺失的增长参数补默认值，显式设置优先；独立于基础/Galgame 模式。",
    ),
    "noise_floor": (
        "亲近度微小变化阈值",
        "亲近度增减的绝对值小于此数时不计入亲近度，先过滤再算倍率；0 关闭过滤。",
    ),
    "negative_weight": (
        "亲近度扣分倍率",
        "亲近度扣分乘以此数；1 不变，0 不扣。不影响信任扣分，仍受单次和每日上限限制。",
    ),
    "same_day_decay": (
        "同日亲近度增长递减",
        "范围 0～1，仅减少亲近度加分。默认同日依次为 100%、75%、50%、25%，最低 25%；0 关闭。",
    ),
    "repair_threshold": (
        "亲近度增长抑制触发值",
        "单次亲近度扣分达到此数后，暂时降低后续加分；0 关闭。不恢复信任度。",
    ),
    "repair_hours": (
        "亲近度增长抑制时长（小时）",
        "触发后持续多久，再按关系档位系数调整。0 关闭抑制窗口。",
    ),
    "repair_factor": (
        "抑制期亲近度加分倍率",
        "范围 0～1。0.5 按一半加分，0 暂停加分，1 不抑制；亲密档位系数可进一步压低。",
    ),
    "repair_scale_high": (
        "亲密档位抑制系数",
        "亲近度处于亲密及以上时，按此倍数延长抑制时间，并降低加分倍率；1 不额外调整。",
    ),
    "repair_scale_low": (
        "一般档位抑制时长系数",
        "友好及以下的抑制时间乘以此数，1 不延长；亲密系数为 1 时也使用此值。",
    ),
    "relation_daily_caps.familiarity_up": (
        "熟悉度每日加分上限",
        "每人每天最多增加多少熟悉度。Galgame 为此值的 2 倍；0 表示不增加。",
    ),
    "relation_daily_caps.familiarity_down": (
        "熟悉度每日扣分上限",
        "每人每天最多扣多少熟悉度；0 禁止互动扣分，时间衰减另算。",
    ),
    "relation_daily_caps.affinity_up": (
        "亲近度每日加分上限",
        "每人每天最多增加多少亲近度。Galgame 为此值的 2 倍；0 表示不增加。",
    ),
    "relation_daily_caps.affinity_down": (
        "亲近度每日扣分上限",
        "每人每天最多扣多少亲近度；0 禁止扣分。加分不抵消已用额度，时间衰减另算。",
    ),
    "relation_daily_caps.trust_up": (
        "信任度每日加分上限",
        "每人每天正向增信的额度；默认事件不增信。Galgame 为 2 倍，0 禁止增加。",
    ),
    "relation_daily_caps.trust_down": (
        "信任度每日扣分上限",
        "每人每天最多扣多少信任度；0 禁止互动扣分，时间衰减另算。",
    ),
    "economy.level_mult.yanwu": (
        "亲近度 −100～不足 0 加分倍率",
        "此段亲近度加分乘以这个数，Galgame 再乘 2；0 暂停此段加分。",
    ),
    "economy.level_mult.mosheng": (
        "亲近度 0～不足 10 加分倍率",
        "此段亲近度加分乘以这个数，Galgame 再乘 2；0 暂停此段加分。",
    ),
    "economy.level_mult.renshi": (
        "亲近度 10～不足 30 加分倍率",
        "此段亲近度加分乘以这个数，Galgame 再乘 2；0 暂停此段加分。",
    ),
    "economy.level_mult.youhao": (
        "亲近度 30～不足 55 加分倍率",
        "此段亲近度加分乘以这个数，Galgame 再乘 2；0 暂停此段加分。",
    ),
    "economy.level_mult.qinmi": (
        "亲近度 55～不足 80 加分倍率",
        "此段亲近度加分乘以这个数，Galgame 再乘 2；0 暂停此段加分。",
    ),
    "economy.level_mult.zhiyou": (
        "亲近度 80～不足 95 加分倍率",
        "此段亲近度加分乘以这个数，Galgame 再乘 2；0 暂停此段加分。",
    ),
    "economy.level_mult.zhiai": (
        "亲近度 95～100 加分倍率",
        "此段亲近度加分乘以这个数，Galgame 再乘 2；0 暂停此段加分。",
    ),
    "decay.grace_days": (
        "关系保温期（天）",
        "即使一段时间没有互动，彼此的熟悉感也不会马上消失。超过这段时间后，关系才会逐渐淡化。0 表示不设保温期。",
    ),
}

for _key, _range in (
    ("under10", "0～不足 10"),
    ("under30", "10～不足 30"),
    ("under55", "30～不足 55"),
    ("under80", "55～不足 80"),
    ("under95", "80～不足 95"),
    ("at95", "95～100"),
):
    LABELS[f"familiarity_growth.{_key}"] = (
        f"熟悉度 {_range} 加分倍率",
        "此段熟悉度加分乘以这个数，Galgame 再乘 2；0 暂停此段加分。",
    )


def fields():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(
            encoding="utf-8"
        )
    )
    result = []

    def visit(path, node, group, title):
        if path in {"decay.baseline", "economy.preset"}:
            return
        if group != "basic" and path in SECTIONS["basic"][1]:
            return
        if node["type"] == "object":
            for key, child in node["items"].items():
                visit(path + "." + key, child, group, title)
            return
        key = path.rsplit(".", 1)[-1]
        label, hint = LABELS.get(
            path,
            LABELS.get(
                key,
                (node.get("description", key), node.get("hint", "")),
            ),
        )
        result.append(
            dict(
                path=path,
                group=group,
                title=title,
                label=label,
                hint=hint,
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
