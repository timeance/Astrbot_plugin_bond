"""选择当前版本；普通 main 提交不发布，版本回退或标签冲突须人工处理。"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

import yaml

from check_project import ROOT, check_project


def select_tag(previous_ref: str, tag: str, root: Path = ROOT) -> str:
    version = check_project(root)
    if not previous_ref:
        if tag != f"v{version}":
            raise ValueError("tag 与插件版本不一致")
        return tag
    if not re.fullmatch(r"[0-9a-f]{40}", previous_ref) or set(previous_ref) == {"0"}:
        raise ValueError("自动发布需要有效的前一次 main 提交")
    previous = yaml.safe_load(
        subprocess.check_output(
            ["git", "-C", str(root), "show", f"{previous_ref}:metadata.yaml"], text=True
        )
    )["version"]
    if str(previous) == version:
        return ""
    if not all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in (str(previous), version)):
        raise ValueError("自动发布仅接受正式版本号")
    if tuple(map(int, version.split("."))) <= tuple(map(int, str(previous).split("."))):
        raise ValueError("自动发布版本必须递增")
    return f"v{version}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-ref", default="")
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    tag = select_tag(args.previous_ref, args.tag)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"tag={tag}\n")
    print(f"准备发布 {tag}" if tag else "版本未变化，跳过发布")


if __name__ == "__main__":
    main()
