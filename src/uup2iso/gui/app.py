"""GUI 组合根（骨架）。

WP0-T2 的职责只有一件事：证明 PySide6 + PySide6-Fluent-Widgets 依赖栈
在 ``.venv`` 内可导入、可创建窗口（离屏可测）。完整的组合根装配、
``NavigationService`` 与向导由 WP9-T1 / WP9-T3 替换本文件。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow
from qfluentwidgets import Theme, setTheme

from uup2iso import __version__

APP_NAME = "UUP2ISO"
PLACEHOLDER_TEXT = "工程骨架已就绪：导航与向导将在 WP9-T1 / WP9-T3 接入。"


def create_main_window() -> QMainWindow:
    """构造主窗口（不含事件循环，便于单测与离屏冒烟）。"""
    window = QMainWindow()
    window.setWindowTitle(f"{APP_NAME} {__version__}")
    window.resize(960, 640)

    placeholder = QLabel(PLACEHOLDER_TEXT)
    placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
    window.setCentralWidget(placeholder)
    return window


def main() -> int:
    """``uup2iso-gui`` 图形入口，返回进程退出码。"""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    setTheme(Theme.AUTO)

    create_main_window().show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
