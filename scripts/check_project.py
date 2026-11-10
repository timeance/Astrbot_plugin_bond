"""检查元数据、开发基线默认值及初始化赋值顺序；不导入插件或框架。"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def check_changelog(root: Path, version: str) -> None:
    notes = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+)$", notes, re.MULTILINE)
    if len(headings) != 1 or not re.fullmatch(
        rf"{re.escape(version)} — \d{{4}}-\d{{2}}-\d{{2}}", headings[0]
    ):
        raise ValueError("CHANGELOG 只保留与插件版本一致的当前版本说明")
    if not notes.split(f"## {headings[0]}", 1)[1].strip():
        raise ValueError("CHANGELOG 当前版本说明不能为空")


def check_project(root: Path = ROOT) -> str:
    metadata = yaml.safe_load((root / "metadata.yaml").read_text(encoding="utf-8"))
    tree = ast.parse((root / "main.py").read_text(encoding="utf-8"))
    plugin = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BondPlugin"
    )
    registration = next(
        node
        for node in plugin.decorator_list
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "register"
    )
    values = [ast.literal_eval(arg) for arg in registration.args]
    for key, index in (("name", 0), ("author", 1), ("version", 3), ("repo", 4)):
        if metadata[key] != values[index]:
            raise ValueError(f"metadata.yaml 与 @register 的 {key} 不一致")
    if metadata["name"] != "astrbot_plugin_bond":
        raise ValueError("插件标识必须为 astrbot_plugin_bond")
    if metadata["repo"].lower() != "https://github.com/timeance/astrbot_plugin_bond":
        raise ValueError("源码地址不是 Bond 目标仓库")

    schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
    # 自动模型调用、诊断与联动默认关闭。
    for section, key in (
        ("judge", "enabled"),
        ("judge", "follow_maisoul_replyer"),
        ("relation_title", "enabled"),
        ("coupling", "enabled"),
        ("assessment", "enabled"),
        ("diagnostics", "enabled"),
    ):
        if schema[section]["items"][key]["default"] is not False:
            raise ValueError(f"开发基线要求 {section}.{key} 默认关闭")
    if schema["inject"]["items"]["enabled"]["default"] is not True:
        raise ValueError("在回复中体现关系应默认开启")

    init = next(
        node
        for node in plugin.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    assigned: dict[str, int] = {}

    def mark(target: ast.AST, lineno: int) -> None:
        if isinstance(target, ast.Name):
            assigned.setdefault(target.id, lineno)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                mark(element, lineno)

    for node in ast.walk(init):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                mark(target, node.lineno)
        elif isinstance(node, ast.AugAssign):
            mark(node.target, node.lineno)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            mark(node.target, node.lineno)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            mark(node.target, node.lineno)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    mark(item.optional_vars, node.lineno)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            assigned.setdefault(node.name, node.lineno)
        elif isinstance(node, ast.NamedExpr):
            mark(node.target, node.lineno)
    bad = [
        node.id
        for node in ast.walk(init)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id in assigned
        and assigned[node.id] > node.lineno
    ]
    if bad:
        raise ValueError(f"__init__ 局部变量先用后赋值: {bad}")
    check_changelog(root, str(metadata["version"]))
    return str(metadata["version"])


if __name__ == "__main__":
    print(f"PASS 元数据、开发基线默认值与赋值顺序，版本 {check_project()}")
