"""交付门禁：拒绝不匹配版本、缺失许可证及混入运行数据的发布包。"""

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from build_package import validate_archive, validate_tag  # noqa: E402
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
        "metadata.yaml",
        "main.py",
        "_conf_schema.json",
        "pages/dashboard/index.html",
        "resources/prompts/judge_prompt.txt",
        "resources/prompts/inject_template.txt",
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
