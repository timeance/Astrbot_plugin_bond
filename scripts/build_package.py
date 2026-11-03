"""从已提交的 HEAD 构建并验证发布包；可选校验已合并的 annotated tag。"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

from check_project import ROOT, check_project

# 官方发布指南的 16MB 上限，按十进制字节保守检查。
MAX_PACKAGE_BYTES = 16_000_000


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def validate_tag(tag: str, version: str) -> None:
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?", tag):
        raise ValueError("tag 格式须为 v主版本.次版本.修订版，可附预发布后缀")
    if tag != f"v{version}":
        raise ValueError("tag 与插件版本不一致")
    ref = f"refs/tags/{tag}"
    if git("cat-file", "-t", ref) != "tag":
        raise ValueError("发布须使用带说明的 annotated tag")
    if git("rev-parse", f"{ref}^{{commit}}") != git("rev-parse", "HEAD"):
        raise ValueError("tag 与当前检查的代码不一致")
    subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", "HEAD", "origin/main"],
        check=True,
    )


def require_committed_source() -> None:
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("打包读取 HEAD，请先提交或还原已跟踪文件的改动")


def validate_archive(archive: Path) -> None:
    if archive.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("发布包超过 16MB 上限")
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None:
            raise ValueError("ZIP 校验失败")
        names = set(package.namelist())
        required = {
            "LICENSE",
            "NOTICE",
            "README.md",
            "requirements.txt",
            "logo.png",
            "resources/fonts/BondSansSC-Regular.ttf",
            "resources/fonts/OFL.txt",
            "metadata.yaml",
            "main.py",
            "_conf_schema.json",
            "pages/dashboard/index.html",
            "resources/prompts/judge_prompt.txt",
            "resources/prompts/inject_template.txt",
            "resources/prompts/bond_behavior.txt",
        }
        missing = required - names
        if missing:
            raise ValueError(f"发布包缺少文件: {sorted(missing)}")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"不安全归档路径: {name}")
            if any(
                part
                in {
                    "tests",
                    ".github",
                    "scripts",
                    "data",
                    "__pycache__",
                    ".pytest_cache",
                    ".ruff_cache",
                    ".venv",
                    ".venv-framework",
                    "artifacts",
                }
                for part in path.parts
            ) or re.search(r"\.(db|sqlite|sqlite3)(-wal|-shm)?$|\.pyc$|\.log$", name):
                raise ValueError(f"发布包混入开发或运行数据: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag")
    args = parser.parse_args()
    require_committed_source()
    version = check_project()
    if args.tag:
        validate_tag(args.tag, version)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=zip", "-o", str(output), "HEAD"],
        check=True,
    )
    validate_archive(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{digest}  {output.name}\n", encoding="utf-8"
    )
    print(f"PASS 发布包与源码许可文件: {output.name}, sha256={digest}")


if __name__ == "__main__":
    main()
