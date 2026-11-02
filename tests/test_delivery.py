"""交付门禁：拒绝不匹配版本、缺失许可证及混入运行数据的发布包。"""

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_package  # noqa: E402
from build_package import (  # noqa: E402
    MAX_PACKAGE_BYTES,
    require_committed_source,
    validate_archive,
    validate_tag,
)
from check_project import check_project  # noqa: E402


@pytest.fixture
def project(tmp_path):
    for name in ("metadata.yaml", "main.py", "_conf_schema.json"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    return tmp_path


def test_registration_mismatch_is_rejected(project):
    metadata = project / "metadata.yaml"
    content = yaml.safe_load(metadata.read_text(encoding="utf-8"))
    content["version"] = "9.9.9"
    metadata.write_text(yaml.safe_dump(content), encoding="utf-8")
    with pytest.raises(ValueError, match="version 不一致"):
        check_project(project)


def test_unsafe_baseline_default_is_rejected(project):
    path = project / "_conf_schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["judge"]["items"]["enabled"]["default"] = True
    path.write_text(json.dumps(schema), encoding="utf-8")
    with pytest.raises(ValueError, match="judge.enabled 默认关闭"):
        check_project(project)


def package(tmp_path, *, missing=None, extra=None):
    path = tmp_path / "package.zip"
    files = [
        "LICENSE",
        "NOTICE",
        "README.md",
        "requirements.txt",
        "logo.png",
        "metadata.yaml",
        "main.py",
        "_conf_schema.json",
        "pages/dashboard/index.html",
        "resources/prompts/judge_prompt.txt",
        "resources/prompts/inject_template.txt",
        "resources/prompts/bond_behavior.txt",
    ]
    with zipfile.ZipFile(path, "w") as archive:
        for name in files:
            if name != missing:
                archive.writestr(name, "fixture")
        if extra:
            archive.writestr(extra, "fixture")
    return path


def test_package_requires_license(tmp_path):
    with pytest.raises(ValueError, match="缺少文件.*LICENSE"):
        validate_archive(package(tmp_path, missing="LICENSE"))


@pytest.mark.parametrize("extra", ["data/bond.db", "tests/test_core.py", "../secret"])
def test_package_rejects_private_or_development_files(tmp_path, extra):
    with pytest.raises(ValueError):
        validate_archive(package(tmp_path, extra=extra))


def test_runtime_package_is_accepted(tmp_path):
    validate_archive(package(tmp_path))


def test_release_rejects_mismatched_tag():
    with pytest.raises(ValueError, match="版本不一致"):
        validate_tag("v9.9.9", "0.1.0-dev")


def test_package_requires_behavior_prompt_resource(tmp_path):
    with pytest.raises(ValueError, match="缺少文件.*bond_behavior"):
        validate_archive(
            package(tmp_path, missing="resources/prompts/bond_behavior.txt")
        )


@pytest.mark.parametrize("missing", ["requirements.txt", "logo.png"])
def test_package_requires_installation_assets(tmp_path, missing):
    with pytest.raises(ValueError, match="缺少文件"):
        validate_archive(package(tmp_path, missing=missing))


def test_package_rejects_store_size_overflow(tmp_path):
    archive = package(tmp_path)
    with archive.open("ab") as stream:
        stream.truncate(MAX_PACKAGE_BYTES + 1)
    with pytest.raises(ValueError, match="16MB"):
        validate_archive(archive)


@pytest.fixture
def git_project(tmp_path, monkeypatch):
    def run(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    run("init", "-q")
    source = tmp_path / "metadata.yaml"
    source.write_text("version: 0.3.0\n", encoding="utf-8")
    run("add", "metadata.yaml")
    run(
        "-c",
        "user.name=Bond Test",
        "-c",
        "user.email=bond-test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )
    monkeypatch.setattr(build_package, "ROOT", tmp_path)
    return source, run


@pytest.mark.parametrize("staged", [False, True])
def test_package_rejects_changed_tracked_source(git_project, staged):
    source, run = git_project
    source.write_text("version: 9.9.9\n", encoding="utf-8")
    if staged:
        run("add", "metadata.yaml")
    with pytest.raises(ValueError, match="先提交或还原"):
        require_committed_source()


def test_package_accepts_committed_source_with_untracked_output(git_project):
    source, _ = git_project
    source.with_name("package.zip").write_bytes(b"untracked output")
    require_committed_source()


@pytest.mark.parametrize("kind", ["lightweight", "different-head", "outside-main"])
def test_release_rejects_wrong_tag_source(git_project, kind):
    source, run = git_project
    run("update-ref", "refs/remotes/origin/main", "HEAD")
    if kind == "outside-main":
        source.write_text("version: 0.3.0\n# new commit\n", encoding="utf-8")
        run("add", "metadata.yaml")
        run(
            "-c",
            "user.name=Bond Test",
            "-c",
            "user.email=bond-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "unmerged",
        )
    if kind == "lightweight":
        run("tag", "v0.3.0")
    else:
        run(
            "-c",
            "user.name=Bond Test",
            "-c",
            "user.email=bond-test@example.invalid",
            "-c",
            "tag.gpgsign=false",
            "tag",
            "-a",
            "v0.3.0",
            "-m",
            "fixture",
        )
    if kind == "different-head":
        run(
            "-c",
            "user.name=Bond Test",
            "-c",
            "user.email=bond-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-qm",
            "later",
        )
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        validate_tag("v0.3.0", "0.3.0")


def test_release_accepts_annotated_tag_on_main(git_project):
    _, run = git_project
    run("update-ref", "refs/remotes/origin/main", "HEAD")
    run(
        "-c",
        "user.name=Bond Test",
        "-c",
        "user.email=bond-test@example.invalid",
        "-c",
        "tag.gpgsign=false",
        "tag",
        "-a",
        "v0.3.0",
        "-m",
        "fixture",
    )
    validate_tag("v0.3.0", "0.3.0")
