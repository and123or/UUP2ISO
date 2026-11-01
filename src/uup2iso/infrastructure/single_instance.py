"""单实例守卫（日志 §6.1、需求 FR9.4）。

平台实现是 Windows 命名互斥量 ``Local\\UUP2ISO.SingleInstance``（同用户会话级）。

为什么用互斥量而不是锁文件：进程被强杀时内核会释放它的句柄，不会留下「僵尸锁」；
而锁文件在崩溃后需要额外的陈旧检测逻辑。

**顺序契约**（日志 §2.2）：单实例校验必须发生在「建日志文件」之前——
冲突实例不建日志、不清理、不写审计。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Final

__all__ = ["DEFAULT_MUTEX_NAME", "SingleInstanceGuard"]

DEFAULT_MUTEX_NAME: Final[str] = r"Local\UUP2ISO.SingleInstance"
"""互斥量名（日志 §6.1）。同用户会话级，跨用户互斥不在本期范围。"""

_ERROR_ALREADY_EXISTS = 183
_SYNCHRONIZE = 0x00100000


class SingleInstanceGuard:
    """命名互斥量的持有者。

    Args:
        name: 互斥量名；测试可传唯一名避免与真实实例互相干扰。

    Example:
        >>> guard = SingleInstanceGuard()
        >>> if not guard.acquire():   # 已有实例在跑
        ...     raise SystemExit(5)
    """

    def __init__(self, name: str = DEFAULT_MUTEX_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._owner = False

    # -- 生命周期 ---------------------------------------------------------

    def acquire(self) -> bool:
        """尝试成为唯一实例。

        Returns:
            ``True`` 表示抢到；``False`` 表示已有实例在运行（**不抛错**，由调用方决定行为：
            GUI 激活已有窗口退 0，CLI 输出 ``already_running`` 退 5）。
        """
        if self._handle is not None:
            return self._owner

        if sys.platform != "win32":  # pragma: no cover - 本项目只支持 Windows
            self._owner = True
            return True

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        handle = kernel32.CreateMutexW(None, 1, self._name)
        last_error = ctypes.get_last_error()

        if not handle:
            # 创建失败（极少见）按「抢到」处理：宁可重复启动，也不要因为互斥机制坏了起不来
            self._owner = True
            return True

        self._handle = int(handle)
        self._owner = last_error != _ERROR_ALREADY_EXISTS
        if not self._owner:
            # 已有实例：释放我们这份句柄引用，避免自己把别人的互斥量「续命」
            kernel32.CloseHandle(ctypes.c_void_p(self._handle))
            self._handle = None
        return self._owner

    def release(self) -> None:
        """释放互斥量（进程退出前调用；异常退出时内核会自动回收）。"""
        if self._handle is None:
            return
        if sys.platform == "win32":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.ReleaseMutex(ctypes.c_void_p(self._handle))
            kernel32.CloseHandle(ctypes.c_void_p(self._handle))
        self._handle = None
        self._owner = False

    # -- 状态 -------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def held(self) -> bool:
        """当前对象是否持有互斥量。"""
        return self._owner and self._handle is not None

    @property
    def already_running(self) -> bool:
        """是否因「已有实例」而失败（区别于还没调用 ``acquire``）。"""
        return self._handle is None and not self._owner

    # -- 上下文管理 -------------------------------------------------------

    def __enter__(self) -> SingleInstanceGuard:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
