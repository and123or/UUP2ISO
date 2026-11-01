"""GUI 组合根（骨架 + 启动序列）。

启动顺序（日志 §2.2、§6.1）：

1. 建 ``QApplication``（不写任何文件）；
2. **抢单实例** —— 抢不到就通过 `QLocalSocket` 请求已有窗口置前，然后以 **0** 退出，
   **绝不建日志、不清理**；
3. 抢到后启动激活监听，再走 `application.startup.bootstrap`（建日志 → 补审计只读 → 清理）；
4. 进事件循环；退出时先关日志（审计置只读）再释放单实例。

前两版踩过的坑仍然保留说明：

* 顶层窗口必须由 Python 侧持有引用（写成 ``create_main_window().show()`` 会秒退）；
* Qt 的 C++ 事件循环不执行 Python 字节码，SIGINT 需靠 200ms 心跳才能被处理。
"""

from __future__ import annotations

import signal
import sys
from types import FrameType

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow
from qfluentwidgets import Theme, setTheme

from uup2iso import __version__
from uup2iso.application.startup import (
    StartupConflictError,
    StartupReport,
    acquire_instance,
    bootstrap,
    release_instance,
)
from uup2iso.gui.activation import ActivationServer, activate_running_instance

APP_NAME = "UUP2ISO"
PLACEHOLDER_TEXT = "工程骨架已就绪：导航与向导将在 WP9-T1 / WP9-T3 接入。"
SIGINT_POLL_INTERVAL_MS = 200
"""Ctrl+C 的最坏响应延迟；越小越灵敏，代价是事件循环空转次数增加。"""

_ACTIVE_WINDOW: QMainWindow | None = None
"""当前主窗口；激活请求（第二个实例）需要拿到它才能抬到前台。"""


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
    global _ACTIVE_WINDOW
    window = create_main_window()  # 关键：必须用变量持有引用，见模块文档「坑 1」
    _ACTIVE_WINDOW = window
    window.show()
    try:
        return int(app.exec())
    finally:
        _ACTIVE_WINDOW = None
        window.close()


def bring_to_front() -> None:
    """把已有窗口抬到前台（第二个实例激活时调用）。"""
    window = _ACTIVE_WINDOW
    if window is None:
        return
    window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
    window.show()
    window.raise_()
    window.activateWindow()


def main() -> int:
    """``uup2iso-gui`` 图形入口，返回进程退出码。"""
    # QApplication.instance() 的静态类型是 QCoreApplication | None，需窄化后才能当 QApplication 用
    existing = QApplication.instance()
    app: QApplication = existing if isinstance(existing, QApplication) else QApplication(sys.argv)

    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    setTheme(Theme.AUTO)

    session = acquire_instance()
    if session is None:
        # 已有实例：请求它置前，然后自己退 0（不建日志、不清理、不写审计）
        activated = activate_running_instance()
        hint = "已激活正在运行的窗口" if activated else "未能连接到正在运行的窗口"
        print(f"{APP_NAME} 已在运行（{hint}），本次启动退出。", file=sys.stderr)
        return 0

    server = ActivationServer(bring_to_front)
    server.start()

    report: StartupReport | None = None
    try:
        try:
            report = bootstrap(entry="gui", session=session)
        except StartupConflictError:  # 理论上不可达（已抢到），保险起见不阻塞启动
            return 0

        report.sink.info("app.gui", "主窗口已创建", event="window_ready")
        _install_sigint_handler(app)
        return run_event_loop(app)
    finally:
        server.stop()
        if report is not None:
            report.close()  # 关日志（审计置只读）后释放单实例
        else:
            release_instance(session)


if __name__ == "__main__":
    raise SystemExit(main())
