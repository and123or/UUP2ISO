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


class _RecordingApp:
    """替身 QApplication：只实现 ``run_event_loop`` 用到的 ``exec()``。

    用它替代真实事件循环，是为了在不阻塞测试的前提下检查
    「进入事件循环那一刻，主窗口是否仍然存活」。
    """

    def __init__(self) -> None:
        self.exec_called = False
        self.top_level_windows = 0

    def exec(self) -> int:
        from PySide6.QtWidgets import QApplication

        self.exec_called = True
        self.top_level_windows = len(QApplication.topLevelWidgets())
        return 0


def test_event_loop_keeps_window_alive(qapp: object) -> None:
    """回归：`create_main_window().show()` 会让临时对象被 GC，窗口秒退。

    若把引用丢掉，进入 ``exec()`` 时顶层窗口数为 0（实测表现为「窗口闪一下消失，
    但进程一直卡在事件循环里，Ctrl+C 也无效」）。
    """
    from uup2iso.gui.app import run_event_loop

    stub = _RecordingApp()
    assert run_event_loop(stub) == 0  # type: ignore[arg-type]
    assert stub.exec_called
    assert stub.top_level_windows >= 1, "进入事件循环时主窗口已消失：引用被提前回收"


def test_cli_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI 入口可运行且输出版本号（完整命令集属 WP1-T5）。"""
    from typer.testing import CliRunner

    from uup2iso.cli.main import app

    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
