"""GUI 激活通道（日志 §6.1「GUI 启动时把已有窗口置前」）。

用 `QLocalServer`/`QLocalSocket`（Windows 下走命名管道）：第二个实例连上去写一行
``activate``，主实例收到后把窗口抬到前台，随后第二个实例以 **0** 退出——
这不是错误，是「程序已经在运行」的正常收场。

放 `gui/` 的原因：AGENTS.md 规定 PySide6 只允许出现在 `gui/` 内。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket

__all__ = ["SERVER_NAME", "ActivationServer", "activate_running_instance"]

SERVER_NAME = "UUP2ISO.Activation"
"""本地套接字名；测试可传唯一名，避免撞上真实运行中的实例。"""

_PAYLOAD = b"activate\n"


class ActivationServer:
    """主实例的激活监听端。

    Args:
        on_activate: 收到激活请求时调用（通常是「抬起已有窗口」）。
        name: 套接字名。
    """

    def __init__(self, on_activate: Callable[[], None], *, name: str = SERVER_NAME) -> None:
        self._callback = on_activate
        self._server = QLocalServer()
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def start(self) -> bool:
        """开始监听；同名残留（上次异常退出）先移除再监听。"""
        QLocalServer.removeServer(self._name)
        if not self._server.listen(self._name):
            return False
        self._server.newConnection.connect(self._handle_connection)
        return True

    def stop(self) -> None:
        self._server.close()
        QLocalServer.removeServer(self._name)

    def _handle_connection(self) -> None:
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(lambda: socket.readAll())
        socket.disconnected.connect(socket.deleteLater)
        self._callback()


def activate_running_instance(*, timeout_ms: int = 1500, name: str = SERVER_NAME) -> bool:
    """请求已有实例把窗口置前。

    Returns:
        ``True`` 表示已连上并发出请求；``False`` 表示对方没在监听（可能刚退出）。
    """
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout_ms):
        socket.abort()
        return False

    socket.write(_PAYLOAD)
    socket.flush()
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        socket.abort()
    return True
