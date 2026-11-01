"""Bond 提示词模板管理服务。

本模块只负责模板的读取、校验、选择、预览和独立 JSON 持久化。
评估提示词的输出协议始终由程序追加，不能被用户模板替换或删除。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from pathlib import Path
from string import Formatter
from typing import Any

from ..core.assessment import SYSTEM_PROMPT
from ..core.scope import account_id

PROMPT_KINDS = frozenset({"assessment", "behavior"})
TEMPLATE_MODES = frozenset({"default", "custom"})
MAX_TEMPLATE_LENGTH = 4000

_BEHAVIOR_FIELDS = frozenset({"relationship", "impression", "events"})
_BEHAVIOR_RESOURCE = (
    Path(__file__).resolve().parents[1] / "resources" / "prompts" / "bond_behavior.txt"
)
DEFAULT_BEHAVIOR_PROMPT = _BEHAVIOR_RESOURCE.read_text(encoding="utf-8").strip()
DEFAULT_ASSESSMENT_PROMPT = SYSTEM_PROMPT

# 这是程序协议，不属于可编辑模板。自定义 assessment resolve 时总会追加它。
ASSESSMENT_OUTPUT_CONTRACT = SYSTEM_PROMPT

_DEFAULT_TEMPLATES = {
    "assessment": DEFAULT_ASSESSMENT_PROMPT,
    "behavior": DEFAULT_BEHAVIOR_PROMPT,
}


def _new_state() -> dict[str, Any]:
    return {
        "version": 1,
        "global": {
            kind: {"mode": "default", "template": ""} for kind in sorted(PROMPT_KINDS)
        },
        "groups": {},
    }


class PromptService:
    """管理全局模板与 `(bot_id, group_id)` 群级覆盖的同步纯服务。"""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.load_error: str | None = None
        self._settings: dict[str, Any] | None = None
        try:
            self._settings = self._load()
        except (ValueError, TypeError, OSError):
            self.load_error = (
                "提示词文件无法读取或校验失败；请修复或恢复 prompts.json 后重载插件"
            )

    def _require_available(self) -> None:
        if self.load_error:
            raise ValueError(self.load_error)

    # ---------- public contract ----------

    def resolve(
        self, kind: str, bot_id: str | None = None, group_id: str | None = None
    ) -> str:
        """返回指定 Bot 在指定真实群中的最终提示词。"""
        self._require_available()
        kind = self._validate_kind(kind)
        bot_id, group_id = self._validate_optional_scope(bot_id, group_id)
        with self._lock:
            record, source = self._effective_record(kind, bot_id, group_id)
            template = (
                _DEFAULT_TEMPLATES[kind]
                if record["mode"] == "default"
                else record["template"]
            )
            if kind == "assessment" and source != "builtin":
                return f"{template.rstrip()}\n\n{ASSESSMENT_OUTPUT_CONTRACT}"
            if kind == "assessment" and source == "builtin":
                return ASSESSMENT_OUTPUT_CONTRACT
            return template

    def get_settings(
        self,
        kind: str | None = None,
        bot_id: str | None = None,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        """读取配置快照；带完整身份时同时返回实际生效来源。

        - `get_settings()` 返回完整可序列化配置快照。
        - `get_settings(kind)` 返回该类的默认模板、全局设置和群覆盖。
        - `get_settings(kind, bot_id, group_id)` 追加 `effective` 结果。
        """
        self._require_available()
        if kind is not None:
            kind = self._validate_kind(kind)
        bot_id, group_id = self._validate_optional_scope(bot_id, group_id)
        with self._lock:
            if kind is None:
                if bot_id is not None or group_id is not None:
                    raise ValueError("指定 bot_id/group_id 时必须同时指定 kind")
                return copy.deepcopy(self._settings)

            result = {
                "kind": kind,
                "default": _DEFAULT_TEMPLATES[kind],
                "global": copy.deepcopy(self._settings["global"][kind]),
                "groups": copy.deepcopy(self._settings["groups"]),
            }
            if bot_id is not None and group_id is not None:
                record, source = self._effective_record(kind, bot_id, group_id)
                result["effective"] = {
                    "source": source,
                    "mode": record["mode"],
                    "template": self.resolve(kind, bot_id, group_id),
                }
            return result

    def set_template(
        self,
        kind: str,
        bot_id: str | None = None,
        group_id: str | None = None,
        mode: str = "default",
        template: str = "",
    ) -> None:
        """设置全局或群级模板，并在成功持久化后立即更新当前实例。"""
        self._require_available()
        kind = self._validate_kind(kind)
        bot_id, group_id = self._validate_optional_scope(bot_id, group_id)
        mode = self._validate_mode(mode)
        self._validate_template(kind, template, mode=mode)

        with self._lock:
            candidate = copy.deepcopy(self._settings)
            record = {"mode": mode, "template": template if mode == "custom" else ""}
            if bot_id is None:
                candidate["global"][kind] = record
            else:
                candidate["groups"].setdefault(bot_id, {}).setdefault(group_id, {})[
                    kind
                ] = record
            self._commit(candidate)

    def reset(
        self,
        kind: str | None = None,
        bot_id: str | None = None,
        group_id: str | None = None,
    ) -> None:
        """恢复默认配置；可只清除一个全局类或一个群级覆盖。"""
        self._require_available()
        if kind is not None:
            kind = self._validate_kind(kind)
        bot_id, group_id = self._validate_optional_scope(bot_id, group_id)
        if kind is None and (bot_id is not None or group_id is not None):
            raise ValueError("指定 bot_id/group_id 时必须同时指定 kind")

        with self._lock:
            candidate = copy.deepcopy(self._settings)
            if kind is None:
                candidate = _new_state()
            elif bot_id is None:
                candidate["global"][kind] = {"mode": "default", "template": ""}
            else:
                groups = candidate["groups"]
                bot_groups = groups.get(bot_id)
                if bot_groups is not None:
                    group_record = bot_groups.get(group_id)
                    if group_record is not None:
                        group_record.pop(kind, None)
                        if not group_record:
                            bot_groups.pop(group_id, None)
                    if not bot_groups:
                        groups.pop(bot_id, None)
            self._commit(candidate)

    def preview(
        self,
        kind: str,
        bot_id: str | None = None,
        group_id: str | None = None,
        *,
        mode: str | None = None,
        template: str | None = None,
    ) -> str:
        """用固定合成样本渲染已保存模板或未保存草稿，不写入文件。"""
        self._require_available()
        kind = self._validate_kind(kind)
        bot_id, group_id = self._validate_optional_scope(bot_id, group_id)
        if (bot_id is None) != (group_id is None):
            raise ValueError("预览群级模板时必须同时提供 bot_id 和 group_id")

        if (mode is None) != (template is None):
            raise ValueError("预览草稿时必须同时提供 mode 和 template")
        if mode is not None and template is not None:
            mode = self._validate_mode(mode)
            self._validate_template(kind, template, mode=mode)
            # 群关闭覆盖后的预览与保存后的继承规则一致。
            selected_mode = mode
            if mode == "default" and bot_id is not None:
                with self._lock:
                    inherited = self._settings["global"][kind]
                    selected_mode = inherited["mode"]
                    template = inherited["template"]
            rendered = self._render_preview(
                kind,
                _DEFAULT_TEMPLATES[kind] if selected_mode == "default" else template,
            )
            if kind == "assessment" and selected_mode == "custom":
                rendered = f"{rendered.rstrip()}\n\n{ASSESSMENT_OUTPUT_CONTRACT}"
            return rendered

        if bot_id is None:
            with self._lock:
                settings = self._settings["global"][kind]
                preview_template = (
                    _DEFAULT_TEMPLATES[kind]
                    if settings["mode"] == "default"
                    else settings["template"]
                )
                rendered = self._render_preview(kind, preview_template)
                if kind == "assessment" and settings["mode"] == "custom":
                    rendered = f"{rendered.rstrip()}\n\n{ASSESSMENT_OUTPUT_CONTRACT}"
                return rendered

        return self._render_preview(kind, self.resolve(kind, bot_id, group_id))

    @classmethod
    def validate(cls, kind: str, template: str, mode: str = "custom") -> None:
        """校验模板；合法时返回 None，非法时抛出 ValueError。"""
        kind = cls._validate_kind(kind)
        mode = cls._validate_mode(mode)
        cls._validate_template(kind, template, mode=mode)

    # ---------- loading and persistence ----------

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _new_state()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("提示词 JSON 无法读取") from exc
        return self._normalize_state(raw)

    def _commit(self, candidate: dict[str, Any]) -> None:
        candidate = self._normalize_state(candidate)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                json.dump(
                    candidate, handle, ensure_ascii=False, indent=2, sort_keys=True
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
        self._settings = candidate

    @classmethod
    def _normalize_state(cls, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("提示词 JSON 版本无效")
        global_settings = raw.get("global")
        groups = raw.get("groups")
        if not isinstance(global_settings, dict) or not isinstance(groups, dict):
            raise ValueError("提示词 JSON 结构无效")

        state = _new_state()
        for kind in PROMPT_KINDS:
            state["global"][kind] = cls._normalize_record(
                global_settings.get(kind), kind
            )
        for bot_id, bot_groups in groups.items():
            bot_id = cls._validate_identity(bot_id, "bot_id")
            if not isinstance(bot_groups, dict):
                raise ValueError("群级提示词结构无效")
            for group_id, group_settings in bot_groups.items():
                group_id = cls._validate_identity(group_id, "group_id")
                if not isinstance(group_settings, dict):
                    raise ValueError("群级提示词结构无效")
                for kind, record in group_settings.items():
                    kind = cls._validate_kind(kind)
                    state["groups"].setdefault(bot_id, {}).setdefault(group_id, {})[
                        kind
                    ] = cls._normalize_record(record, kind)
        return state

    @classmethod
    def _normalize_record(cls, record: Any, kind: str) -> dict[str, str]:
        if record is None:
            return {"mode": "default", "template": ""}
        if not isinstance(record, dict):
            raise ValueError("提示词设置结构无效")
        mode = cls._validate_mode(record.get("mode"))
        template = record.get("template", "")
        cls._validate_template(kind, template, mode=mode)
        return {"mode": mode, "template": template if mode == "custom" else ""}

    # ---------- validation and selection ----------

    @staticmethod
    def _validate_kind(kind: str) -> str:
        if not isinstance(kind, str) or kind not in PROMPT_KINDS:
            raise ValueError("未知提示词类型")
        return kind

    @staticmethod
    def _validate_mode(mode: str) -> str:
        if not isinstance(mode, str) or mode not in TEMPLATE_MODES:
            raise ValueError("提示词模式必须是 default 或 custom")
        return mode

    @staticmethod
    def _validate_identity(value: str, name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{name} 必须是字符串账号")
        try:
            return account_id(value)
        except ValueError as exc:
            raise ValueError(f"{name} 不是合法真实账号") from exc

    @classmethod
    def _validate_optional_scope(
        cls, bot_id: str | None, group_id: str | None
    ) -> tuple[str | None, str | None]:
        if (bot_id is None) != (group_id is None):
            raise ValueError("bot_id 和 group_id 必须同时提供")
        if bot_id is None:
            return None, None
        return cls._validate_identity(bot_id, "bot_id"), cls._validate_identity(
            group_id, "group_id"
        )

    @classmethod
    def _validate_template(cls, kind: str, template: Any, *, mode: str) -> None:
        if not isinstance(template, str):
            raise TypeError("模板必须是字符串")
        if len(template) > MAX_TEMPLATE_LENGTH:
            raise ValueError(f"模板长度不能超过 {MAX_TEMPLATE_LENGTH} 个字符")
        if mode == "default":
            if template.strip():
                raise ValueError("default 模式不能携带自定义模板")
            return
        if not template.strip():
            raise ValueError("custom 模式必须提供非空模板")

        allowed = _BEHAVIOR_FIELDS if kind == "behavior" else frozenset()
        try:
            fields = Formatter().parse(template)
            for _literal, field_name, format_spec, conversion in fields:
                if field_name is None:
                    continue
                if (
                    not field_name
                    or field_name not in allowed
                    or format_spec
                    or conversion is not None
                ):
                    raise ValueError("模板只允许明示字段占位符")
        except ValueError as exc:
            if str(exc) == "模板只允许明示字段占位符":
                raise
            raise ValueError(f"模板语法错误：{exc}") from exc

    def _effective_record(
        self, kind: str, bot_id: str, group_id: str
    ) -> tuple[dict[str, str], str]:
        group_record = (
            self._settings["groups"].get(bot_id, {}).get(group_id, {}).get(kind)
        )
        if group_record is not None and group_record["mode"] == "custom":
            return group_record, "group"
        global_record = self._settings["global"][kind]
        if global_record["mode"] == "custom":
            return global_record, "global"
        return global_record, "builtin"

    @staticmethod
    def _render_preview(kind: str, template: str) -> str:
        if kind == "assessment":
            return template
        return template.format(
            relationship="熟悉的群友",
            impression="愿意交流，偶尔会开玩笑",
            events="最近有过一次友善的交流",
        )
