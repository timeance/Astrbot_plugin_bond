"""宿主配置的一次性升级；保存失败时不启用未持久化的候选值。"""

import json
from copy import deepcopy

from ..core.config_migrations import (
    TRUST_CONFIG_VERSION,
    migrate_saved_defaults,
    trust_config_backup,
)


def upgrade_config(config, data_dir):
    previous = deepcopy(dict(config))
    candidate = deepcopy(previous)
    changed = migrate_saved_defaults(candidate)
    if not changed:
        return []
    if previous.get("trust_config_version", 0) < TRUST_CONFIG_VERSION:
        backup = data_dir / "trust-settings-before-0.6.json"
        data_dir.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            # 不覆盖首次升级前的备份；残缺文件也不能被当作备份成功。
            if not isinstance(json.loads(backup.read_text(encoding="utf-8")), dict):
                raise ValueError("信任设置备份损坏，请检查后重载")
        else:
            with backup.open("x", encoding="utf-8") as stream:
                json.dump(
                    trust_config_backup(previous), stream, ensure_ascii=False, indent=2
                )
    config.update(candidate)
    try:
        config.save_config()
    except BaseException:
        config.clear()
        config.update(previous)
        raise
    return changed
