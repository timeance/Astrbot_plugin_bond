"""已保存配置的默认值迁移。

AstrBot 的配置持久化规则：已保存的键永远优先于 `_conf_schema.json` 的
默认值，schema 默认值变更不会自动写回存量配置。因此"只改默认值"对保存
过配置的旧安装不生效。普通文案仅替换已知旧默认；0.6.0 的信任配置是明确
标注的不兼容升级，仅第一次统一 T 专属值，不触碰成员数据和共享设置。
"""

# v1.24.0 → v1.24.1（PR #41）：主人版态度指引语义从"低段位＝赌气别扭"
# 改为"正负号分界"——正值低段不再有冲突叙事。保存过 v1.24.0 默认值的
# 老安装需要迁移。新值必须与当前 schema 默认值保持一致。
_MASTER_GUIDANCE_V124 = {
    "mosheng": (
        "冷战/别扭期，爱答不理、说话带刺，但心里在等主人先低头",
        "有点生分，提不起劲亲昵，但不生气也不冷战——正常说话，就是不黏人，像各忙各的老熟人",
    ),
    "renshi": (
        "小别扭还没消，嘴上不饶人，可心里在意主人、盼着主人来哄",
        "关系不错，温和亲近，相处自在，感情在慢慢升温——还没到特别黏的程度，但已经是能自然说话撒娇的关系",
    ),
    "youhao": (
        "和好了，会撒娇耍赖、跟主人要专属待遇",
        "感情稳定，会撒娇耍赖、跟主人要专属待遇，偶尔耍小性子也理直气壮",
    ),
}

TRUST_CONFIG_VERSION = 1
TRUST_DEFAULTS = {
    "relation_daily_caps": {"trust_up": 2, "trust_down": 8},
    "decay": {"trust_enabled": True, "trust_half_life": 60, "trust_baseline": 20},
}


def trust_config_backup(cfg: dict) -> dict:
    """仅备份即将覆盖的信任字段，不包含 Provider 或密钥。"""
    return {
        section: {key: (cfg.get(section) or {}).get(key) for key in defaults}
        for section, defaults in TRUST_DEFAULTS.items()
    }


def migrate_saved_defaults(cfg: dict) -> list:
    """迁移旧默认及已知配置结构，保留用户的开关选择。

    返回被迁移的配置键路径；空列表＝无需
    迁移。态度指引只替换与旧默认逐字一致的值；眼熟开关仅转换结构，不改变真假值。
    """
    changed = []
    version = cfg.get("trust_config_version", 0)
    if type(version) is not int or version < 0:
        raise ValueError("信任配置升级标记无效")
    if version < TRUST_CONFIG_VERSION:
        for section, defaults in TRUST_DEFAULTS.items():
            values = cfg.setdefault(section, {})
            for key, value in defaults.items():
                values[key] = value
                changed.append(f"{section}.{key}")
        cfg["trust_config_version"] = TRUST_CONFIG_VERSION
        changed.append("trust_config_version")
    # 0.5.7 将嵌套开关改为普通配置行，保留存量用户的明确选择。
    passive = cfg.get("passive_familiarity")
    if isinstance(passive, dict) and type(passive.get("enabled")) is bool:
        cfg["passive_familiarity"] = passive["enabled"]
        changed.append("passive_familiarity")
    title = cfg.get("relation_title")
    if isinstance(title, dict):
        mode = title.get("mode")
        if mode:
            if mode not in {"fixed", "llm"}:
                raise ValueError("旧称谓模式无效")
            title["enabled"] = mode == "llm"
            title["mode"] = ""
            changed.extend(["relation_title.enabled", "relation_title.mode"])
            # 仅旧模式下的已知默认额度升级为 30，保留新配置中有效的小额度。
            for key, old, new in (
                ("refresh_cooldown_hours", 6, 12),
                ("daily_generation_limit", 10, 30),
            ):
                if type(title.get(key)) in (int, float) and title[key] == old:
                    title[key] = new
                    changed.append(f"relation_title.{key}")
        # 存量越界值显式迁移并持久化，避免运行时静默忽略已保存的设置。
        for key, minimum, maximum, default in (
            ("refresh_cooldown_hours", 12, 720, 12),
            ("daily_generation_limit", 0, 30, 30),
        ):
            value = title.get(key)
            if type(value) in (int, float) and not minimum <= value <= maximum:
                title[key] = default
                changed.append(f"relation_title.{key}")
    levels = cfg.get("levels")
    if not isinstance(levels, dict):
        return changed
    for key, (old, new) in _MASTER_GUIDANCE_V124.items():
        tier = levels.get(key)
        if isinstance(tier, dict) and tier.get("master_guidance") == old:
            tier["master_guidance"] = new
            changed.append(f"levels.{key}.master_guidance")
    return changed
