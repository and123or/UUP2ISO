"""UAC 提权：非管理员时**提权重启自己并等待**（P1-3）。

Windows 不允许提升**当前进程**的权限——只能启动一个提权的新进程。
所以这里的做法是：

1. 非管理员且在跑 `run`（非 dry-run）→ 用 `ShellExecuteExW(verb="runas")` 重启自己；
2. 父进程 `WaitForSingleObject` 等子进程结束，**把它的退出码原样返回**；
3. 用户取消 UAC（错误 1223）→ 返回 :data:`ELEVATION_CANCELLED`（退出码 3）。

为什么不直接让转换器自己提权（它内部会 `start cmd.exe -verb runas`）：
那样父进程拿不到子进程的输出与退出码，等于"把控制权丢给一个我们看不见的窗口"——
用户会以为程序卡死（这正是 0.1.38 之前踩过的坑）。
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable
from pathlib import Path

__all__ = [
    "ELEVATION_CANCELLED",
    "is_admin",
    "relaunch_elevated",
]

ELEVATION_CANCELLED = 3
"""用户在 UAC 对话框点了「否」——按"用户取消"处理（退出码 3）。"""

_ERROR_CANCELLED = 1223


def is_admin() -> bool:
    """当前进程是否以管理员身份运行。"""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_elevated(
    argv: list[str] | None = None,
    *,
    cwd: Path | None = None,
    on_wait: Callable[[], None] | None = None,
) -> int:
    """以管理员身份重启自身并等待，返回子进程退出码。

    Args:
        argv: 传给子进程的参数（默认取 ``sys.argv[1:]``）。
        cwd: 子进程工作目录（默认当前目录）。
        on_wait: 父进程等待期间的回调（每 60 s 一次，用于打印"仍在运行"）。
        on_wait: 父进程等待期间的回调（每 60 s 一次，用于打印"仍在运行"）。

    Returns:
        子进程退出码；用户取消 UAC 时返回 :data:`ELEVATION_CANCELLED`。

    Raises:
        RuntimeError: 非 Windows 或拿不到可执行文件时（调用方应降级为"提示用户手动提权"）。
    """
    import ctypes
    from ctypes import wintypes

    if sys.platform != "win32":
        raise RuntimeError("提权仅支持 Windows")

    args = list(sys.argv[1:] if argv is None else argv)
    executable, parameters = _command_for(sys.executable, sys.argv[0], args)

    # 显式声明用到的 API 签名，避免 64 位下指针被截断
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    see_mask_noclose_process = 0x00000040
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = see_mask_noclose_process
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = parameters
    info.lpDirectory = str(cwd or Path.cwd())
    info.nShow = 1  # SW_SHOWNORMAL：新控制台窗口

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == _ERROR_CANCELLED:
            return ELEVATION_CANCELLED
        raise RuntimeError(f"提权启动失败（Windows 错误 {error}）")

    if not info.hProcess:
        raise RuntimeError("提权启动后拿不到进程句柄")

    try:
        # 分片等待：父窗口不能 30 分钟一片空白（会被误判卡死）
        wait_timeout = 60_000
        while True:
            result = kernel32.WaitForSingleObject(info.hProcess, wait_timeout)
            if result != 0x00000102:  # WAIT_TIMEOUT
                break
            if on_wait is not None:
                with contextlib.suppress(Exception):
                    on_wait()
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
        return int(exit_code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


def _command_for(executable: str, argv0: str, args: list[str]) -> tuple[str, str]:
    """决定"用什么启动、带什么参数"。

    * 打包后 / console-script（`.exe`）：直接用它，参数原样；
    * 源码运行（`python -m uup2iso.cli.main`）：用解释器 + 模块名 + 参数。
    """
    from shlex import join as _join

    first = Path(argv0)
    if first.suffix.lower() == ".exe" and first.is_file():
        return str(first), _join(args)
    return executable, _join(["-m", "uup2iso.cli.main", *args])
