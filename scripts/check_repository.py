"""检查 Git 跟踪文件中的运行数据、凭据及部署信息；只报告位置，不回显内容。"""

from __future__ import annotations

import codecs
import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_PATH = re.compile(
    r"tokens\.txt|(?:CLAUDE|AGENTS)\.local\.md|\.mimosa|Zone\.Identifier"
    r"|\.(?:db|sqlite|sqlite3)(?:-wal|-shm)?$|\.(?:pyc|log|token)$"
)
TOKEN = re.compile(
    r"ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]+" r"|https://[^/@\s]+:[^/@\s]+@github"
)
DEPLOYMENT = re.compile(
    r"/home/[a-z]|/www/|/AstrBot/|127\.0\.0\.1|:6185"
    r"|docker (?:exec|logs|restart) astrbot"
)
# 只移除官方仓库 URL 本身；同一行中的本机路径仍须检查。
ASTRBOT_URL = re.compile(
    r"https://(?:github\.com|raw\.githubusercontent\.com)/(?:AstrBotDevs|Soulter)/AstrBot"
    r"(?:/[^\s<>\"'`)]*)?(?=[\s<>\"'`)]|$)",
    re.IGNORECASE,
)
FONT_PATH = re.compile(r"[^'\":,\s]*fonts/[^'\":,\s]*")
ACCOUNT = re.compile(r"\b1[0-9]{9}\b")


def inspect_file(name: str, content: bytes) -> list[str]:
    path = PurePosixPath(name)
    findings = []
    if (
        PRIVATE_PATH.search(name)
        or any(part.startswith(".env") for part in path.parts)
        or set(path.parts)
        & {
            "data",
            ".venv",
            ".venv-framework",
            "artifacts",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
        }
    ):
        findings.append(f"{name}: 禁止跟踪的私有或运行文件")
    if content.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        encoding = "utf-8-sig"
    # Windows 文本可能带 BOM；其他二进制中的可识别凭据也不能因 NUL 跳过。
    for number, line in enumerate(
        content.decode(encoding, errors="replace").splitlines(), 1
    ):
        if TOKEN.search(line):
            findings.append(f"{name}:{number}: 疑似凭据")
        # 模式定义只豁免部署/号码检查；凭据扫描仍覆盖检查脚本自身。
        if name == "scripts/check_repository.py":
            continue
        deployment_text = ASTRBOT_URL.sub("", line)
        if name == "api/rank_image.py":
            deployment_text = FONT_PATH.sub("", deployment_text)
        if DEPLOYMENT.search(deployment_text):
            findings.append(f"{name}:{number}: 疑似部署信息，请使用占位符")
        # 此规则仅为号码启发式，不能识别所有账号或替代人工脱敏。
        if "tests" not in path.parts and ACCOUNT.search(line):
            findings.append(f"{name}:{number}: 疑似账号，请核对是否为合成数据")
    return findings


def check_repository(root: Path = ROOT) -> list[str]:
    tracked = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"])
    findings = []
    for raw_name in tracked.split(b"\0"):
        if not raw_name:
            continue
        name = raw_name.decode("utf-8")
        path = root / name
        if path.is_symlink():
            findings.append(f"{name}: 跟踪文件不得为符号链接")
            continue
        findings.extend(inspect_file(name, path.read_bytes()))
    return findings


def main() -> None:
    findings = check_repository()
    if findings:
        print("\n".join(findings))
        raise SystemExit(1)
    print("PASS Git 跟踪文件检查（不包含未跟踪文件；仍需人工核对私密信息）")


if __name__ == "__main__":
    main()
