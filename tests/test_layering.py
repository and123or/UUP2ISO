"""分层依赖方向门禁（项目计划 WP0-T1 验收要点 / GUI框架 §4.1）。

允许的依赖方向：

* ``core``            → core
* ``infrastructure``  → core, infrastructure
* ``application``     → core, infrastructure, application
* ``gui``             → core, application, gui
* ``cli``             → core, application, cli

禁止：core 反向依赖任何外层；infrastructure 依赖 application/gui/cli；
gui 与 cli 互相依赖。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "uup2iso"
LAYERS = ("core", "infrastructure", "application", "gui", "cli")

ALLOWED: dict[str, frozenset[str]] = {
    "core": frozenset({"core"}),
    "infrastructure": frozenset({"core", "infrastructure"}),
    "application": frozenset({"core", "infrastructure", "application"}),
    "gui": frozenset({"core", "application", "gui"}),
    "cli": frozenset({"core", "application", "cli"}),
}

# 包根文件（src/uup2iso/__init__.py 等）不隶属任何一层，作为伞包允许引用各层。
ALLOWED_ROOT: frozenset[str] = frozenset(LAYERS)


def _python_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _layer_of(module: str) -> str | None:
    """把 ``uup2iso.<layer>....`` 解析成层名；包自身与未知前缀返回 None。"""
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "uup2iso" and parts[1] in LAYERS:
        return parts[1]
    return None


def _target_layer(node: ast.ImportFrom, current: Path) -> str | None:
    """解析 ``from ... import`` 的绝对模块名（支持相对导入）。"""
    if node.level == 0:
        return _layer_of(node.module or "")

    # 相对导入：以当前文件所在包为基准上溯 level-1 层
    parts = list(current.relative_to(PACKAGE_ROOT.parent).with_suffix("").parts)
    base = parts[:-1]
    if node.level > 1:
        base = base[: -(node.level - 1)]
    module = ".".join([*base, node.module]) if node.module else ".".join(base)
    return _layer_of(module)


def _imported_layers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                layer = _layer_of(alias.name)
                if layer:
                    found.add(layer)
        elif isinstance(node, ast.ImportFrom):
            layer = _target_layer(node, path)
            if layer:
                found.add(layer)
    return found


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_illegal_cross_layer_import(path: Path) -> None:
    relative = path.relative_to(PACKAGE_ROOT).parts
    if len(relative) == 1:
        owner, allowed = "uup2iso", ALLOWED_ROOT
    else:
        owner = relative[0]
        assert owner in LAYERS, f"{path} 不在已知分层目录内，请检查目录结构"
        allowed = ALLOWED[owner]

    illegal = _imported_layers(path) - allowed
    assert not illegal, (
        f"{path.relative_to(PACKAGE_ROOT.parent)} 违反分层：{owner} 不得依赖 {sorted(illegal)}"
    )


def test_all_layers_exist() -> None:
    """六层目录骨架齐全（GUI框架 §4.2）。"""
    for layer in LAYERS:
        assert (PACKAGE_ROOT / layer).is_dir(), f"缺少分层目录 src/uup2iso/{layer}"


def test_layer_fixture_detects_violation(tmp_path: Path) -> None:
    """自检：扫描器确实能识别违规导入（避免门禁空转）。"""
    sample = PACKAGE_ROOT / "core" / "_layering_probe_tmp.py"
    sample.write_text("from uup2iso.gui import app\n", encoding="utf-8")
    try:
        assert _imported_layers(sample) == {"gui"}
        assert _imported_layers(sample) - ALLOWED["core"] == {"gui"}
    finally:
        sample.unlink(missing_ok=True)
