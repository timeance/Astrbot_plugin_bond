"""公开内容检查的误报、旁路与日志脱敏回归。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_repository import inspect_file  # noqa: E402

OFFICIAL = "https://github.com/AstrBotDevs/AstrBot/blob/v4.28.0/pyproject.toml"
LOCAL = "/" + "home/alice/bot"
FONT = "/" + "AstrBot/data/plugin_data/example/fonts/font.otf"


def inspect(name, text):
    return inspect_file(name, text.encode("utf-8"))


@pytest.mark.parametrize("owner", ["AstrBotDevs", "Soulter"])
def test_official_repository_url_is_not_a_deployment_path(owner):
    url = OFFICIAL.replace("AstrBotDevs", owner)
    assert inspect("docs/guide.md", f"[宿主依赖]({url})") == []


def test_official_url_does_not_hide_neighboring_deployment_path():
    assert inspect("docs/guide.md", f"{OFFICIAL} local={LOCAL}")
    assert inspect("docs/guide.md", OFFICIAL.replace("github.com", "example.invalid"))


def test_font_exception_only_removes_the_font_path_in_rank_renderer():
    assert inspect("api/rank_image.py", f'"{FONT}"') == []
    assert inspect("api/rank_image.py", f'"{FONT}", "{LOCAL}"')
    assert inspect("docs/guide.md", f'"{FONT}"')


@pytest.mark.parametrize(
    "name", ["data/bond.db", "backup.sqlite-wal", ".env", "tokens.txt", ".venv/file.py"]
)
def test_private_paths_are_reported_even_for_binary_files(name):
    assert inspect_file(name, b"\0binary")


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_" + "a" * 36,
        "github_pat_" + "abc123",
        "https://user:secret@" + "github.com/repo",
    ],
)
def test_credentials_are_redacted_and_checked_inside_scanner(secret):
    for name in ("docs/guide.md", "scripts/check_repository.py"):
        result = inspect(name, secret)
        assert result == [f"{name}:1: 疑似凭据"]
        assert secret not in "\n".join(result)


def test_placeholder_does_not_hide_another_account_on_the_same_line():
    number = "1" + "9" * 9
    assert inspect("docs/guide.md", f"123456789 {number}")
    assert inspect("tests/test_fixture.py", number) == []


def test_ordinary_text_and_images_are_accepted():
    assert inspect("README.md", "bot-A / group-A / user-A") == []
    assert inspect_file("logo.png", b"PNG\0binary") == []


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_windows_encoded_credentials_are_detected(encoding):
    secret = "ghp_" + "a" * 36
    assert inspect_file("note.txt", secret.encode(encoding)) == ["note.txt:1: 疑似凭据"]


def test_binary_prefix_does_not_hide_plain_credentials():
    secret = ("ghp_" + "a" * 36).encode()
    assert inspect_file("note.bin", b"header\0" + secret) == ["note.bin:1: 疑似凭据"]
