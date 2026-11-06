"""交付门禁：拒绝不匹配版本、缺失许可证及混入运行数据的发布包。"""

import ast
import json
import os
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
    for name in ("metadata.yaml", "main.py", "_conf_schema.json", "CHANGELOG.md"):
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
        "resources/fonts/BondSansSC-Regular.ttf",
        "resources/fonts/OFL.txt",
        "metadata.yaml",
        "main.py",
        "_conf_schema.json",
        "pages/dashboard/index.html",
        "resources/prompts/judge_prompt.txt",
        "resources/prompts/inject_template.txt",
        "resources/prompts/bond_behavior.txt",
        "resources/ranking/yachiyo-wave.png",
        "resources/ranking/yachiyo-peace.png",
        "resources/ranking/yachiyo-heart.png",
        "resources/ranking/yachiyo-hug.png",
        "resources/ranking/yachiyo-pout.png",
        "resources/ranking/yachiyo-laugh.png",
        "resources/ranking/yachiyo-normal-smile.png",
        "resources/ranking/yachiyo-normal-wave.png",
        "resources/ranking/yachiyo-normal-hug.png",
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


@pytest.mark.parametrize(
    "missing",
    [
        "requirements.txt",
        "logo.png",
        "resources/fonts/BondSansSC-Regular.ttf",
        "resources/fonts/OFL.txt",
        "resources/ranking/yachiyo-wave.png",
        "resources/ranking/yachiyo-peace.png",
        "resources/ranking/yachiyo-heart.png",
        "resources/ranking/yachiyo-hug.png",
        "resources/ranking/yachiyo-pout.png",
        "resources/ranking/yachiyo-laugh.png",
        "resources/ranking/yachiyo-normal-smile.png",
        "resources/ranking/yachiyo-normal-wave.png",
        "resources/ranking/yachiyo-normal-hug.png",
    ],
)
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


def test_runtime_logging_and_task_entrypoints_follow_store_rules():
    files = [ROOT / "main.py", ROOT / "__init__.py"]
    for directory in ("api", "core", "services", "storage"):
        files.extend((ROOT / directory).rglob("*.py"))
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            location = f"{relative}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Import):
                modules = {n.name.split(".")[0] for n in node.names}
                assert "logging" not in modules, location
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "logging", location
                if any(n.name == "logger" for n in node.names):
                    assert node.module == "astrbot.api", location
                if node.module == "asyncio" and any(
                    n.name == "create_task" for n in node.names
                ):
                    assert relative == "core/taskregistry.py", location
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "create_task":
                    assert relative == "core/taskregistry.py", location


@pytest.mark.parametrize("change", ["old", "empty", "mismatch"])
def test_current_changelog_rejects_extra_missing_or_wrong_version(project, change):
    path = project / "CHANGELOG.md"
    version = yaml.safe_load((project / "metadata.yaml").read_text(encoding="utf-8"))[
        "version"
    ]
    if change == "old":
        path.write_text(
            path.read_text(encoding="utf-8") + "\n## 0.1.0 — 2026-01-01\nold\n",
            encoding="utf-8",
        )
    elif change == "empty":
        path.write_text(f"# 更新说明\n\n## {version} — 2026-01-01\n", encoding="utf-8")
    else:
        path.write_text("## 9.9.9 — 2026-01-01\nwrong\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CHANGELOG"):
        check_project(project)


def test_current_changelog_matches_metadata(project):
    assert (
        check_project(project)
        == yaml.safe_load((project / "metadata.yaml").read_text(encoding="utf-8"))[
            "version"
        ]
    )


@pytest.mark.parametrize("version, expected", [("0.3.0", ""), ("0.5.5", "v0.5.5")])
def test_automatic_release_selects_only_version_increase(
    git_project, monkeypatch, version, expected
):
    import prepare_release

    source, run = git_project
    previous = run("rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(prepare_release, "check_project", lambda root: version)
    assert prepare_release.select_tag(previous, "main", source.parent) == expected


@pytest.mark.parametrize("version", ["0.2.0", "0.5.5-beta"])
def test_automatic_release_rejects_rollback_and_prerelease(
    git_project, monkeypatch, version
):
    import prepare_release

    source, run = git_project
    previous = run("rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(prepare_release, "check_project", lambda root: version)
    with pytest.raises(ValueError):
        prepare_release.select_tag(previous, "main", source.parent)


def test_automatic_release_rejects_missing_previous_commit(project):
    from prepare_release import select_tag

    with pytest.raises(ValueError, match="前一次 main"):
        select_tag("0" * 40, "main", project)
    with pytest.raises(subprocess.CalledProcessError):
        select_tag("a" * 40, "main", project)


def test_release_workflow_requires_ci_and_retains_immutable_draft_publish():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    release = yaml.safe_load(
        (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    )
    call = ci["jobs"]["release"]
    assert call["needs"] == ["test", "framework"]
    assert (
        call["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )
    assert call["uses"] == "./.github/workflows/release.yml"
    assert call["with"]["previous-ref"] == "${{ github.event.before }}"
    assert release["jobs"]["publish"]["needs"] == "build"
    assert release["permissions"] == {"contents": "read"}
    assert release["jobs"]["publish"]["permissions"] == {"contents": "write"}
    assert release["jobs"]["publish"]["if"] == "needs.build.outputs.tag != ''"
    for job in release["jobs"].values():
        tools_checkout = next(
            step
            for step in job["steps"]
            if step.get("with", {}).get("path") == "release-tools"
        )
        assert tools_checkout["with"]["ref"] == "${{ github.workflow_sha }}"
        assert tools_checkout["with"]["persist-credentials"] is False
    build_select = next(
        step for step in release["jobs"]["build"]["steps"] if step.get("id") == "select"
    )
    assert (
        '"$GITHUB_WORKSPACE/release-tools/scripts/prepare_release.py" --source-dir .'
        in build_select["run"]
    )
    assert (
        release["jobs"]["publish"]["steps"][-1]["run"]
        == 'bash "$GITHUB_WORKSPACE/release-tools/.github/release.sh"'
    )


@pytest.mark.parametrize(
    "failure", ["", "checksum", "tag", "validate", "push", "remote", "create"]
)
def test_release_stops_at_failed_boundary_before_publication(tmp_path, failure):
    bash = shutil.which("bash")
    if not bash and os.name == "nt":
        bash = str(Path(shutil.which("git")).parents[1] / "bin/bash.exe")
    if not bash or not Path(bash).is_file():
        pytest.skip("需要 Bash 验证发布脚本")
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    (tmp_path / "release").mkdir()
    call_log = tmp_path / "calls.txt"
    fake = r"""#!/usr/bin/env bash
name=$(basename "$0")
printf '%s %s\n' "$name" "$*" >> "$CALL_LOG"
case "$name:$*" in
  sha256sum:*) [[ "$FAILURE" != checksum ]] || exit 1 ;;
  python:*) [[ "$FAILURE" != validate ]] || exit 1 ;;
  git:*' tag -a '*) [[ "$FAILURE" != tag ]] || exit 1 ;;
  git:*' push origin '*) [[ "$FAILURE" != push ]] || exit 1 ;;
  git:rev-parse*) echo expected-object ;;
  gh:api*) if [[ "$FAILURE" == remote ]]; then echo changed-object; else echo expected-object; fi ;;
  gh:'release create '*) [[ "$FAILURE" != create ]] || exit 1 ;;
esac
exit 0
"""
    for name in ("sha256sum", "python", "git", "gh"):
        executable = mock_bin / name
        executable.write_text(fake, encoding="utf-8", newline="\n")
        executable.chmod(0o755)
    env = {
        **os.environ,
        "FAILURE": failure,
        "CALL_LOG": call_log.as_posix(),
        "TAG_INPUT": "v0.5.5",
        "TAG_OBJECT": "",
        "AUTOMATIC": "true",
        "GITHUB_REPOSITORY": "synthetic/bond",
    }
    result = subprocess.run(
        [
            bash,
            "-c",
            'export PATH="$(cd "$1" && pwd):$PATH"; cd "$2"; "$BASH" "$3"',
            "--",
            mock_bin.as_posix(),
            tmp_path.as_posix(),
            (ROOT / ".github/release.sh").as_posix(),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    calls = call_log.read_text(encoding="utf-8")
    if failure:
        assert result.returncode != 0, result.stdout + result.stderr
        assert "gh release edit" not in calls
        if failure != "create":
            assert "gh release create" not in calls
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "--verify-tag --draft" in calls
        assert (
            calls.splitlines()[-1]
            == "gh release edit v0.5.5 --repo synthetic/bond --draft=false"
        )


def test_dispatch_recovers_real_historical_tree_with_current_tools(tmp_path):
    # 主干 0.4.4 的真实历史树：没有新发布工具，CHANGELOG 含多个旧版本。
    historical_commit = "91caea9b460009d65daa83a086dfaf457f3787bd"
    source = tmp_path / "astrbot_plugin_bond"
    bash = shutil.which("bash")
    if not bash and os.name == "nt":
        bash = str(Path(shutil.which("git")).parents[1] / "bin/bash.exe")
    if not bash or not Path(bash).is_file():
        pytest.skip("需要 Bash 验证发布脚本")
    env = {**os.environ, "PYTHONUTF8": "1"}

    def command(*args, cwd=source, check=True, command_env=None):
        return subprocess.run(
            args,
            cwd=cwd,
            env=command_env or env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=check,
        )

    command(
        "git",
        "clone",
        "--no-tags",
        "--no-hardlinks",
        "--no-checkout",
        str(ROOT),
        str(source),
        cwd=tmp_path,
    )
    command("git", "checkout", "--detach", historical_commit)
    command("git", "update-ref", "refs/remotes/origin/main", historical_commit)
    assert not (source / "scripts/prepare_release.py").exists()
    assert not (source / ".github/release.sh").exists()
    from check_project import check_changelog

    with pytest.raises(ValueError, match="CHANGELOG"):
        check_changelog(source, "0.4.4")
    identity = (
        "-c",
        "user.name=Bond Test",
        "-c",
        "user.email=bond-test@example.invalid",
        "-c",
        "tag.gpgsign=false",
    )

    def annotate(target=historical_commit):
        command("git", *identity, "tag", "-a", "v0.4.4", target, "-m", "历史恢复测试")

    annotate()
    output = tmp_path / "output.txt"
    command(
        sys.executable,
        str(ROOT / "scripts/prepare_release.py"),
        "--source-dir",
        str(source),
        "--tag",
        "v0.4.4",
        command_env={**env, "GITHUB_OUTPUT": str(output)},
    )
    assert output.read_text(encoding="utf-8") == "tag=v0.4.4\n"
    archive = source / "release/astrbot_plugin_bond-v0.4.4.zip"
    command(sys.executable, "scripts/check_project.py")
    command(
        sys.executable,
        "scripts/build_package.py",
        "--tag",
        "v0.4.4",
        "--output",
        str(archive),
    )
    # 发布 runner 为 Linux；本机 Windows 生成的校验文件使用相同的 LF 换行。
    checksum = archive.with_suffix(".zip.sha256")
    checksum.write_text(
        checksum.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    shutil.copyfile(source / "CHANGELOG.md", archive.with_name("notes.md"))
    with zipfile.ZipFile(archive) as package:
        original = subprocess.check_output(
            ["git", "-C", str(source), "show", f"{historical_commit}:main.py"]
        )
        assert package.read("main.py").replace(b"\r\n", b"\n") == original.replace(
            b"\r\n", b"\n"
        )
        assert "scripts/prepare_release.py" not in package.namelist()

    # 只替代 GitHub 远端服务；Git、历史源码检查、标签验证和校验和均真实执行。
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    gh = mock_bin / "gh"
    gh.write_text(
        r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALL_LOG"
if [[ "$1" == api ]]; then
  printf '%s\n' "$EXPECTED_TAG_OBJECT"
elif [[ "$2" == create && "$RELEASE_EXISTS" == true ]]; then
  exit 1
fi
""",
        encoding="utf-8",
        newline="\n",
    )
    gh.chmod(0o755)
    call_log = tmp_path / "calls.txt"

    def publish(*, release_exists="false", remote_object=None):
        call_log.write_text("", encoding="utf-8")
        tag_object = command("git", "rev-parse", "refs/tags/v0.4.4").stdout.strip()
        publish_env = {
            **env,
            "CALL_LOG": call_log.as_posix(),
            "TAG_INPUT": "v0.4.4",
            "TAG_OBJECT": tag_object,
            "EXPECTED_TAG_OBJECT": remote_object or tag_object,
            "AUTOMATIC": "false",
            "GITHUB_REPOSITORY": "synthetic/bond",
            "RELEASE_EXISTS": release_exists,
        }
        result = command(
            bash,
            "-c",
            'export PATH="$(cd "$1" && pwd):$(cd "$4" && pwd):$PATH"; cd "$2"; "$BASH" "$3"',
            "--",
            mock_bin.as_posix(),
            source.as_posix(),
            (ROOT / ".github/release.sh").as_posix(),
            Path(sys.executable).parent.as_posix(),
            command_env=publish_env,
            check=False,
        )
        return result, call_log.read_text(encoding="utf-8")

    result, calls = publish()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--verify-tag --draft" in calls
    assert "release edit v0.4.4 --repo synthetic/bond --draft=false" in calls
    result, calls = publish(release_exists="true")
    assert result.returncode != 0
    assert "release edit" not in calls
    result, calls = publish(remote_object="changed-object")
    assert result.returncode != 0
    assert "release create" not in calls
    for kind in ("lightweight", "different-head", "outside-main"):
        command("git", "tag", "-d", "v0.4.4")
        if kind == "lightweight":
            command("git", "tag", "v0.4.4")
        elif kind == "different-head":
            annotate(f"{historical_commit}^")
        else:
            annotate()
            command(
                "git", "update-ref", "refs/remotes/origin/main", f"{historical_commit}^"
            )
        result, calls = publish()
        assert result.returncode != 0, kind
        assert calls == "", kind
    assert command("git", "status", "--porcelain", "--untracked-files=no").stdout == ""
