"""GUI 激活通道门禁（日志 §6.1）。

第二个实例连上主实例的本地套接字并请求激活；主实例收到后把窗口抬到前台。
用唯一套接字名，避免撞上真实运行中的程序。
"""

from __future__ import annotations

import time
import uuid

import pytest

from uup2iso.gui.activation import ActivationServer, activate_running_instance

pytestmark = pytest.mark.gui


def unique_name() -> str:
    return f"UUP2ISO.Test.{uuid.uuid4().hex}"


def pump_until(qapp: object, predicate: object, timeout: float = 3.0) -> bool:
    """处理 Qt 事件直到条件成立或超时（离屏环境下事件仍需泵）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()  # type: ignore[attr-defined]
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.02)
    return False


def test_activation_roundtrip(qapp: object) -> None:
    name = unique_name()
    fired: list[int] = []
    server = ActivationServer(lambda: fired.append(1), name=name)

    assert server.start() is True
    try:
        assert activate_running_instance(timeout_ms=2000, name=name) is True
        assert pump_until(qapp, lambda: bool(fired)), "服务端未收到激活请求"
    finally:
        server.stop()


def test_activation_callback_runs_once_per_request(qapp: object) -> None:
    name = unique_name()
    fired: list[int] = []
    server = ActivationServer(lambda: fired.append(1), name=name)
    assert server.start()
    try:
        assert activate_running_instance(timeout_ms=2000, name=name)
        assert pump_until(qapp, lambda: len(fired) >= 1)
        first = len(fired)

        assert activate_running_instance(timeout_ms=2000, name=name)
        assert pump_until(qapp, lambda: len(fired) > first)
    finally:
        server.stop()


def test_activate_without_server_returns_false(qapp: object) -> None:
    assert activate_running_instance(timeout_ms=300, name=unique_name()) is False


def test_server_restart_after_stop(qapp: object) -> None:
    """同名残留必须能重新监听（上次异常退出会留下套接字）。"""
    name = unique_name()
    first = ActivationServer(lambda: None, name=name)
    assert first.start()
    first.stop()

    second = ActivationServer(lambda: None, name=name)
    try:
        assert second.start() is True
    finally:
        second.stop()
