"""第三方依赖声明与安装门禁（项目计划 WP0-T2 验收要点 / GUI框架 §3.3）。

三件事：

1. 声明在 ``pyproject.toml`` 的库必须真的装进当前 ``.venv``；
2. UI 库只允许出现在 ``uup2iso.gui`` 内；
3. ``core`` / ``cli`` 不得引入任何 UI 依赖（保证 CLI 无 GUI 负担）。
"""

from __future__ import annotations

import ast
import importlib.util
import tomllib
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = WORKSPACE / "src" / "uup2iso"

# 发行名 -> 可导入的顶层模块名
DIST_TO_MODULE: dict[str, str] = {
    "PySide6": "PySide6",
    "PySide6-Fluent-Widgets": "qfluentwidgets",
    "httpx": "httpx",
    "pydantic": "pydantic",
    "typer": "typer",
    "jsonschema": "jsonschema",
    "lxml": "lxml",
    "pytest": "pytest",
    "pytest-qt": "pytestqt",
    "dirty-equals": "dirty_equals",
    "ruff": "ruff",
    "mypy": "mypy",
    "pyinstaller": "PyInstaller",
}

UI_MODULES = frozenset({"PySide6", "qfluentwidgets", "shiboken6"})


def _pyproject() -> dict:
    return tomllib.loads((WORKSPACE / "pyproject.toml").read_text(encoding="utf-8"))


def _declared() -> set[str]:
    project = _pyproject()["project"]
    names = {_dist_name(spec) for spec in project["dependencies"]}
    for specs in project.get("optional-dependencies", {}).values():
        names |= {_dist_name(spec) for spec in specs}
    return names


def _dist_name(spec: str) -> str:
    for sep in ("[", ">", "<", "=", "!", "~", ";", " "):
        spec = spec.split(sep)[0]
    return spec.strip()


def test_all_declared_dependencies_are_installed() -> None:
    missing = []
    for dist in sorted(_declared()):
        module = DIST_TO_MODULE.get(dist)
        assert module is not None, f"新增依赖 {dist} 未登记到 DIST_TO_MODULE，请补映射"
        if importlib.util.find_spec(module) is None:
            missing.append(dist)
    assert not missing, f"以下依赖已声明但未安装进 .venv: {missing}"


def test_no_dual_qt_bindings() -> None:
    """PySide6 与 PyQt6 同时存在会导致 Qt 绑定冲突，必须二选一。"""
    assert importlib.util.find_spec("PySide6") is not None
    assert importlib.util.find_spec("PyQt6") is None, (
        "检测到 PyQt6：Fluent 控件库必须使用 PySide6 变体（PySide6-Fluent-Widgets），"
        "不得引入 PyQt-Fluent-Widgets"
    )


def _toplevel_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_ui_libraries_only_in_gui_package() -> None:
    offenders = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        layer = path.relative_to(PACKAGE_ROOT).parts[0]
        if layer == "gui":
            continue
        hits = _toplevel_imports(path) & UI_MODULES
        if hits:
            offenders.append(f"{path.relative_to(WORKSPACE)} -> {sorted(hits)}")
    assert not offenders, "UI 库越界（只允许 uup2iso.gui 使用）：\n" + "\n".join(offenders)


@pytest.mark.parametrize("layer", ["core", "cli"])
def test_core_and_cli_have_no_ui_imports_at_all(layer: str) -> None:
    for path in sorted((PACKAGE_ROOT / layer).rglob("*.py")):
        assert not (_toplevel_imports(path) & UI_MODULES), f"{path} 不得导入 UI 库"
