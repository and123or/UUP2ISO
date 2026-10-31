"""GUI 依赖栈冒烟测试（离屏）。

验证 WP0-T2 的核心结论：PySide6 + PySide6-Fluent-Widgets 在 ``.venv``
内可导入、可创建主窗口。真正的界面由 WP9-T1 起实现。
"""

from __future__ import annotations

import pytest

from uup2iso import __version__

pytestmark = pytest.mark.gui


def test_fluent_widgets_importable() -> None:
    import qfluentwidgets

    assert qfluentwidgets is not None


def test_main_window_title_contains_version(qapp: object) -> None:
    from uup2iso.gui.app import create_main_window

    window = create_main_window()
    try:
        assert __version__ in window.windowTitle()
        assert window.centralWidget() is not None
    finally:
        window.close()


def test_cli_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI 入口可运行且输出版本号（完整命令集属 WP1-T5）。"""
    from typer.testing import CliRunner

    from uup2iso.cli.main import app

    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
