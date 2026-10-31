"""资源采样（进程 §7.2）：CPU / 磁盘 / 网络，全部 ctypes 调 Win32，**不引 psutil**。

三点设计：

* **进程树累加**：CPU 与 IO 按「目标进程 + 其所有后代」求和，否则 DISM 把活干在子进程里时
  会被误判为 idle；
* **失败即视为有活动**：任何 API 取不到数据都返回 ``None``，看门狗把 ``None`` 当「仍在工作」，
  宁可漏报也不误杀（进程 §7.2 的明确取向）；
* **网络是系统级**：按进程统计网络字节需要 ETW，代价过高；规范接受系统级计数，
  方向保守（别的程序有流量时不会告警）。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Protocol, runtime_checkable

__all__ = ["NullSampler", "ResourceSampler", "WindowsSampler"]

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x00000002
_MAX_PATH = 260
_MAX_INTERFACE_NAME_LEN = 256
_MAXLEN_PHYSADDR = 8
_MAXLEN_IFDESCR = 256
_NO_ERROR = 0


@runtime_checkable
class ResourceSampler(Protocol):
    """看门狗需要的三类采样。返回 ``None`` 表示「采不到」，按有活动处理。"""

    def cpu_seconds(self, pid: int) -> float | None:
        """进程树累计 CPU 时间（秒）。"""
        ...

    def io_bytes(self, pid: int) -> tuple[int, int] | None:
        """进程树累计读写字节数 ``(read, write)``。"""
        ...

    def net_bytes(self) -> tuple[int, int] | None:
        """系统级收发字节数 ``(rx, tx)``（仅对含 network 画像的工具使用）。"""
        ...


class NullSampler:
    """永不采到数据的采样器：看门狗据此判定「无法观测」→ 永不告警。"""

    def cpu_seconds(self, pid: int) -> float | None:
        return None

    def io_bytes(self, pid: int) -> tuple[int, int] | None:
        return None

    def net_bytes(self) -> tuple[int, int] | None:
        return None


if sys.platform == "win32":  # pragma: no cover - 平台分支，Windows 下为常态

    class _FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * _MAX_PATH),
        ]

    class _MibIfRow(ctypes.Structure):
        # 字段顺序必须与 winipif.h 完全一致；全部成员自然对齐，无填充差异
        _fields_ = [
            ("wszName", wintypes.WCHAR * _MAX_INTERFACE_NAME_LEN),
            ("dwIndex", wintypes.DWORD),
            ("dwType", wintypes.DWORD),
            ("dwMtu", wintypes.DWORD),
            ("dwSpeed", wintypes.DWORD),
            ("dwPhysAddrLen", wintypes.DWORD),
            ("bPhysAddr", ctypes.c_ubyte * _MAXLEN_PHYSADDR),
            ("dwAdminStatus", wintypes.DWORD),
            ("dwOperStatus", wintypes.DWORD),
            ("dwLastChange", wintypes.DWORD),
            ("dwInOctets", wintypes.DWORD),
            ("dwInUcastPkts", wintypes.DWORD),
            ("dwInNUcastPkts", wintypes.DWORD),
            ("dwInDiscards", wintypes.DWORD),
            ("dwInErrors", wintypes.DWORD),
            ("dwInUnknownProtos", wintypes.DWORD),
            ("dwOutOctets", wintypes.DWORD),
            ("dwOutUcastPkts", wintypes.DWORD),
            ("dwOutNUcastPkts", wintypes.DWORD),
            ("dwOutDiscards", wintypes.DWORD),
            ("dwOutErrors", wintypes.DWORD),
            ("dwOutQLen", wintypes.DWORD),
            ("dwDescrLen", wintypes.DWORD),
            ("bDescr", ctypes.c_ubyte * _MAXLEN_IFDESCR),
        ]

    class WindowsSampler:
        """Win32 采样实现（见模块文档的三点设计）。"""

        def __init__(self) -> None:
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
            self._kernel32.OpenProcess.restype = wintypes.HANDLE
            self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        # --- 进程树 ---

        def _children_map(self) -> dict[int, list[int]]:
            snapshot = self._kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
            if snapshot in (None, wintypes.HANDLE(-1).value):
                return {}

            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
            mapping: dict[int, list[int]] = {}
            try:
                if not self._kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                    return mapping
                while True:
                    mapping.setdefault(int(entry.th32ParentProcessID), []).append(
                        int(entry.th32ProcessID)
                    )
                    if not self._kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
            finally:
                self._kernel32.CloseHandle(snapshot)
            return mapping

        def _tree_pids(self, pid: int) -> list[int]:
            children = self._children_map()
            collected: list[int] = []
            stack = [pid]
            while stack:
                current = stack.pop()
                if current in collected:
                    continue
                collected.append(current)
                stack.extend(children.get(current, ()))
            return collected

        def cpu_seconds(self, pid: int) -> float | None:
            total = 0.0
            measured = False
            for item in self._tree_pids(pid):
                handle = self._kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, item)
                if not handle:
                    continue
                try:
                    creation, exit_time, kernel, user = (
                        _FileTime(),
                        _FileTime(),
                        _FileTime(),
                        _FileTime(),
                    )
                    if not self._kernel32.GetProcessTimes(
                        handle,
                        ctypes.byref(creation),
                        ctypes.byref(exit_time),
                        ctypes.byref(kernel),
                        ctypes.byref(user),
                    ):
                        continue
                    total += (_to_seconds(kernel) or 0.0) + (_to_seconds(user) or 0.0)
                    measured = True
                finally:
                    self._kernel32.CloseHandle(handle)
            return total if measured else None

        def io_bytes(self, pid: int) -> tuple[int, int] | None:
            read_total = 0
            write_total = 0
            measured = False
            for item in self._tree_pids(pid):
                handle = self._kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, item)
                if not handle:
                    continue
                try:
                    counters = _IoCounters()
                    if not self._kernel32.GetProcessIoCounters(handle, ctypes.byref(counters)):
                        continue
                    read_total += int(counters.ReadTransferCount)
                    write_total += int(counters.WriteTransferCount)
                    measured = True
                finally:
                    self._kernel32.CloseHandle(handle)
            return (read_total, write_total) if measured else None

        def net_bytes(self) -> tuple[int, int] | None:
            """系统级收发字节数（32 位计数器，调用方需处理回绕）。"""
            size = wintypes.DWORD(0)
            if self._iphlpapi.GetIfTable(None, ctypes.byref(size), False) != _NO_ERROR:
                return None
            if size.value <= 0:
                return None

            buffer = ctypes.create_string_buffer(size.value)
            if self._iphlpapi.GetIfTable(buffer, ctypes.byref(size), False) != _NO_ERROR:
                return None

            count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
            row_size = ctypes.sizeof(_MibIfRow)
            base = ctypes.addressof(buffer) + ctypes.sizeof(wintypes.DWORD)
            rx_total = 0
            tx_total = 0
            for index in range(count):
                row = _MibIfRow.from_address(base + index * row_size)
                rx_total += int(row.dwInOctets)
                tx_total += int(row.dwOutOctets)
            return (rx_total, tx_total)

    def _to_seconds(value: _FileTime) -> float | None:
        raw = (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)
        return raw / 10_000_000 if raw >= 0 else None

else:  # pragma: no cover - 本项目只支持 Windows

    class WindowsSampler(NullSampler):
        """非 Windows 平台占位：一律返回 ``None``（看门狗不会告警）。"""
