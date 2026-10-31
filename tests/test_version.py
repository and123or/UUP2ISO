"""版本三处同步校验（docs/治理/版本与变更管理.md §3）。"""

from __future__ import annotations

import tomllib
from importlib.metadata import version as dist_version
from pathlib import Path

import uup2iso

WORKSPACE = Path(__file__).resolve().parents[1]

# 同步点之一：断言值必须与 pyproject.toml / __init__.py 一致（改版本时三处同改）。
EXPECTED_VERSION = "0.1.12"


def test_package_version_constant() -> None:
    assert uup2iso.__version__ == EXPECTED_VERSION


def test_pyproject_version_matches() -> None:
    data = tomllib.loads((WORKSPACE / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == EXPECTED_VERSION


def test_installed_metadata_matches() -> None:
    assert dist_version("uup2iso") == EXPECTED_VERSION


def test_requires_python_matches_docs() -> None:
    """依赖栈约束 Python 3.12–3.13（3.14 尚无 PySide6 wheel）。"""
    data = tomllib.loads((WORKSPACE / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["requires-python"] == ">=3.12,<3.14"


def test_long_description_exists() -> None:
    """readme 指向的文件必须存在，否则 sdist/wheel 元数据构建失败。"""
    assert (WORKSPACE / "README.md").is_file()
