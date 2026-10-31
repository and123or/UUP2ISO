"""GUI 组合根（骨架）。

WP0-T2 的职责只有一件事：证明 PySide6 + PySide6-Fluent-Widgets 依赖栈
在 ``.venv`` 内可导入、可创建窗口（离屏可测）。完整的组合根装配、
``NavigationService`` 与向导由 WP9-T1 / WP9-T3 替换本文件。

两个已踩过的坑（CHANGELOG 0.1.10）：

1. **顶层窗口必须由 Python 侧持有引用**。写成 ``create_main_window().show()`` 时，
   临时对象在 ``show()`` 返回后立即被回收，底层 ``QMainWindow`` 随之析构——
   表现是「窗口闪一下消失」，而 ``app.exec()`` 仍在空转（进程不退、Ctrl+C 也无反应）。
2. **Qt 的 C++ 事件循环不执行 Python 字节码**，SIGINT 只会被置位而不会被处理，
   因此纯 ``QApplication.exec()`` 下按 Ctrl+C 没有任何效果。用 200 ms 心跳定时器
   给解释器制造执行机会，是 Qt + Python 的标准解法。
"""

from __future__ import annotations

import signal
import sys
from types import FrameType

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow
from qfluentwidgets import Theme, setTheme

from uup2iso import __version__

APP_NAME = "UUP2ISO"
PLACEHOLDER_TEXT = "工程骨架已就绪：导航与向导将在 WP9-T1 / WP9-T3 接入。"
SIGINT_POLL_INTERVAL_MS = 200
"""Ctrl+C 的最坏响应延迟；越小越灵敏，代价是事件循环空转次数增加。"""


def create_main_window() -> QMainWindow:
    """构造主窗口（不含事件循环，便于单测与离屏冒烟）。"""
    window = QMainWindow()
    window.setWindowTitle(f"{APP_NAME} {__version__}")
    window.resize(960, 640)

    placeholder = QLabel(PLACEHOLDER_TEXT)
    placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
    window.setCentralWidget(placeholder)
    return window


def _install_sigint_handler(app: QApplication) -> None:
    """让 Ctrl+C 能终止 Qt 事件循环（详见模块文档「坑 2」）。

    定时器以 ``app`` 为父对象，由 Qt 持有生命周期，不会被 Python GC 回收。
    """
    timer = QTimer(app)
    timer.timeout.connect(lambda: None)
    timer.start(SIGINT_POLL_INTERVAL_MS)

    def _quit_on_sigint(_signum: int, _frame: FrameType | None) -> None:
        app.quit()

    try:
        signal.signal(signal.SIGINT, _quit_on_sigint)
    except ValueError:
        # 非主线程（如被嵌入测试宿主）无法安装信号处理器：忽略，不影响 GUI 本身
        return


def run_event_loop(app: QApplication) -> int:
    """在给定 app 上显示主窗口并进入事件循环。

    抽成独立函数是为了让测试能注入替身，从而验证「进入事件循环时窗口仍存活」，
    而不必真的阻塞在一个无法打断的循环里。

    Args:
        app: 已创建的 QApplication（或测试替身，只需实现 ``exec()``）。

    Returns:
        进程退出码（``app.exec()`` 的返回值）。
    """
    window = create_main_window()  # 关键：必须用变量持有引用，见模块文档「坑 1」
    window.show()
    try:
        return int(app.exec())
    finally:
        window.close()


def main() -> int:
    """``uup2iso-gui`` 图形入口，返回进程退出码。"""
    # QApplication.instance() 的静态类型是 QCoreApplication | None，需窄化后才能当 QApplication 用
    existing = QApplication.instance()
    app: QApplication = existing if isinstance(existing, QApplication) else QApplication(sys.argv)

    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    setTheme(Theme.AUTO)
    _install_sigint_handler(app)

    return run_event_loop(app)


if __name__ == "__main__":
    raise SystemExit(main())
