"""可信账号与数据作用域的规范化，不解析聊天正文。"""

from __future__ import annotations

import re

SCOPES = ("per_group", "global")
GLOBAL_GROUP = "__global__"


def account_id(value: str) -> str:
    if not is_account_id(value):
        raise ValueError("账号必须是 ASCII 正整数")
    return value


def is_account_id(value: object) -> bool:
    return (
        isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,31}", value) is not None
    )


def scope_mode(value: str) -> str:
    if value not in SCOPES:
        raise ValueError("未知数据作用域")
    return value


def group_key(mode: str, group_id: str | None) -> str | None:
    scope_mode(mode)
    if group_id is None:
        return None
    if mode == "global" and group_id == GLOBAL_GROUP:
        return GLOBAL_GROUP
    account_id(group_id)
    return GLOBAL_GROUP if mode == "global" else group_id
