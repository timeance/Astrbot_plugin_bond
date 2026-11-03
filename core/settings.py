"""Bond 页面可编辑的详细设置；沿用宿主配置，不保存第二份状态。"""

from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo


class SettingsRevisionError(ValueError):
    """提交的配置版本已失效或缺失。"""


# 基础配置与详细参数共用宿主配置；评估高级参数由评估页编辑。
SECTIONS = {
    "basic": (
        "基础设置",
        [
            "assessment.enabled",
            "assessment.assessment_mode",
            "assessment.small_provider_id",
            "assessment.large_provider_id",
            "inject.enabled",
            "default_scope_mode",
            "group_whitelist",
            "ignored_bot_ids",
            "master_ids",
            "relation_mode",
        ],
    ),
    "rules": (
        "关系变化",
        ["timezone", "decay", "economy"],
    ),
    "memory": (
        "印象与近期事件",
        ["inject", "impression", "judge.provider_id", "judge.timeout_sec"],
    ),
    "commands": ("查询与排行", ["command", "render.font_path"]),
    "diagnostics": ("关系诊断", ["diagnostics"]),
}
LABELS = {
    "assessment.enabled": (
        "自动评估互动",
        "根据指向 Bot 的互动更新关系，会调用日常评估模型。保存并重载插件后生效。",
    ),
    "assessment.assessment_mode": (
        "评估模式",
        "single 使用日常模型；tiered 在需要时调用复核模型。复核模型可留空。",
    ),
    "assessment.small_provider_id": (
        "日常评估模型",
        "启用自动评估时必填，选择 AstrBot 中已配置的文本生成模型。",
    ),
    "assessment.large_provider_id": (
        "复核模型（可选）",
        "用于分级评估中容易误判的互动，留空时不调用复核模型。",
    ),
    "inject.enabled": (
        "回复体现关系",
        "向回复上下文补充关系与成员印象，保留当前人格和接话策略。",
    ),
    "ignored_bot_ids": (
        "忽略的机器人账号",
        "这些账号的消息不参与关系记录与评估。填写平台账号 ID，每项一个。",
    ),
    "relation_mode": (
        "关系节奏模板",
        "写实沿用当前节奏；Galgame略加快正向熟悉度和亲近度，并提高每日上限。信任、负向变化与防刷规则不变；重载后生效，不重置数据。",
    ),
    "ranking_access": (
        "排行查询权限",
        "所有人或仅主人；主人按上方主人QQ与可信发言者ID匹配，未配置主人时仅主人模式无人可查。命令、文字唤醒、模型工具统一检查，不授予管理权限。",
    ),
    "ranking_image": (
        "图片排行",
        "命令与文字唤醒使用月读空间风格图片，显示前10名的昵称、头像和称谓，不显示分值。向QQ头像服务请求榜内账号的头像；失败用占位，绘图失败退回文字。",
    ),
    "diagnostics.enabled": (
        "关系诊断记录",
        "默认关闭；本机记录脱敏状态和耗时，不新增原文。按7天及20MB上限清理，可能提前轮转；重载后生效。",
    ),
    "default_scope_mode": (
        "默认关系模式",
        "按群：各群分别记录；全局：同一 Bot 跨群共用关系。页面顶部为指定 Bot 选择的模式优先；切换不复制或合并历史。",
    ),
    "master_ids": (
        "主人 QQ",
        "多个 QQ 号用英文逗号分隔。用于关系身份标记；仅主人排行模式也按此名单核验。不授予管理权限、不豁免评分，称呼服从当前人格。",
    ),
    "group_whitelist": (
        "启用 Bond 的群（白名单）",
        "留空对所有群生效；填写后仅在这些群记录、评估、注入关系和响应 Bond 群聊指令。群号用逗号分隔，对所有 Bot 生效；已有记录保留。",
    ),
    "timezone": (
        "每日限额时区",
        "决定关系每日限额的换日时间，例如 Asia/Shanghai。复核次数按 UTC 换日。",
    ),
    "memory_count": (
        "近期事件条数",
        "向当前群补充事件类别/理由，最多10条；0关闭。不包含原文或分值。",
    ),
    "memory_days": (
        "近期事件保留天数",
        "只回顾这个天数内的事件；0取消时间筛选，不删除历史流水。",
    ),
    "memory_sig_threshold": (
        "重要事件变化阈值",
        "达到这个变化幅度的事件使用更长的回顾时间；0 表示关闭。",
    ),
    "memory_sig_window_mult": ("重要事件时间倍数", "重要事件的回顾天数乘以这个数。"),
    "interval": (
        "印象更新间隔",
        "每累计多少次有效评估，后台更新一次成员印象。更新会调用模型。",
    ),
    "points_mode": (
        "分别记录印象要点",
        "开启后合并相近要点并保留权重；关闭时使用一句话印象。",
    ),
    "judge.provider_id": (
        "印象汇总模型（可选）",
        "留空由宿主解析默认模型，可能不同于当前群模型。印象会另行调用，不计入互动评估次数预算。",
    ),
    "judge.timeout_sec": (
        "印象汇总超时（秒）",
        "超过这个时间停止等待；0 表示不设超时。",
    ),
    "ranking_limit": ("内部排行条数", "公开群聊排行仍最多显示前 10 名。"),
    "text_wake_enabled": ("文字唤醒排行", "允许用下方短语查询本群排行，无需输入斜杠。"),
    "text_wake_ranking": ("排行唤醒短语", "多个短语用英文逗号分隔，须与整条消息一致。"),
    "font_path": (
        "排行图片字体",
        "可选。留空使用打包的开源中文字体；自定义字体不可用时回退到内置字体。",
    ),
    "rows_per_col": ("排行每列行数", "超过这个数量后换到下一列。"),
    "decay.enabled": (
        "启用时间衰减",
        "新安装默认开启；仅亲近度向基线靠近，熟悉度和信任度不变。临时关系期间暂停。",
    ),
    "floor_enabled": (
        "限制衰减后的关系等级",
        "按旧亲近度数值档位托底，不等于三维称谓最多变化两级。",
    ),
    "half_life_base": (
        "基础半衰期（天）",
        "至少5天。巩固倍数为1时全部成员使用此值；大于1时作为已有巩固值的下限。重载后也对已有成员生效。",
    ),
    "half_life_growth": (
        "正向互动的巩固倍数",
        "默认1，使用当前基础半衰期；大于1才保留并延长个人巩固值。改变倍数不反推历史互动次数。",
    ),
    "half_life_max": (
        "最长半衰期（天）",
        "巩固模式下限制已有和新成员的半衰期；不应低于基础值。固定模式不使用此项。",
    ),
    "economy.enabled": (
        "启用关系增长规则",
        "对亲近度变化应用重复衰减、负面权重和修复期规则。",
    ),
    "preset": (
        "缺省增长预设",
        "只为缺失参数提供默认；仅切换此项不会覆盖下方已保存参数。",
    ),
    "noise_floor": (
        "忽略微小变化",
        "亲近度变化的绝对值低于这个数时不计入。0 表示关闭。",
    ),
    "negative_weight": ("负向变化倍数", "亲近度下降幅度乘以这个数；1 表示不放大。"),
    "same_day_decay": (
        "当日重复增长衰减",
        "同一天正向互动越多，后续增长越少；0 表示关闭。",
    ),
    "repair_threshold": (
        "恢复抑制触发幅度",
        "默认0关闭；启用后负面事件暂时压低后续亲近度增长。这不是信任修复。",
    ),
    "repair_hours": (
        "恢复抑制时长（小时）",
        "仅当触发幅度大于0时，降低窗口内正向亲近度增长。",
    ),
    "repair_factor": (
        "恢复抑制增长倍数",
        "仅当恢复抑制启用时，正向亲近度变化乘以这个数。",
    ),
    "repair_scale_high": (
        "亲密关系修复系数",
        "亲密及以上关系的修复期使用这个系数调整。",
    ),
    "repair_scale_low": ("一般关系修复系数", "友好及以下关系的修复期时长乘以这个数。"),
}


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
                (node.get("description", key), "该等级的正向亲近度变化乘以这个数。"),
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
    values = {}
    for field in definitions:
        value = config
        for part in field["path"].split("."):
            value = value.get(part, {}) if isinstance(value, dict) else {}
        values[field["path"]] = field["default"] if value == {} else value
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
                    )
                )
                else 0
            )
            if path in {"decay.half_life_base", "decay.half_life_max"}:
                minimum = 5
            if path in {
                "decay.half_life_growth",
                "inject.memory_sig_window_mult",
                "economy.repair_scale_high",
                "economy.repair_scale_low",
            }:
                minimum = 1
            maximum = 1000000
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
