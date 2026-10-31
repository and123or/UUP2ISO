"""静默看门狗：四信号联合判定「疑似卡死」（进程 §7.2）。

关键取向：**只看 CPU 会误判**。DISM/wimlib 大部分时间在等 IO，aria2c 在等网络，
CPU 常常接近 0。因此要求四条信号**同时**无活动才告警，任一有活动就重置计时。

另一条硬约束：**绝不自动终止**。告警只是 WARN + 证据，由用户决定继续等还是终止。

窗口数由参数算出（`ceil(hang_warn_minutes × 60 / hang_sample_seconds)`），不是常量：
默认 10 分钟 / 30 秒 = 20 个窗口；改成 1 分钟 / 30 秒就是 2 个。
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

__all__ = ["FourSignalWatchdog", "HangEvidence", "WatchdogConfig", "WatchdogState"]

_MIN_MINUTES = 1
_MAX_MINUTES = 120
_MIN_SAMPLE = 10
_MAX_SAMPLE = 300


@dataclass(frozen=True, slots=True)
class WatchdogConfig:
    """看门狗参数（进程 §7.2 的默认值 + 合理区间）。

    Attributes:
        hang_warn_minutes: 连续无活动多少分钟才告警，默认 10，允许 1–120。
        hang_sample_seconds: 采样间隔，默认 30 秒，允许 10–300。
        cpu_idle_seconds: 单个窗口内 CPU 增量低于此值算「无 CPU 活动」，默认 0.5 秒。
        io_idle_bytes_per_s: 磁盘读写速率阈值，默认 256 KB/s。
        net_idle_bytes_per_s: 网络收发速率阈值，默认 32 KB/s（仅 network 画像工具）。
    """

    hang_warn_minutes: int = 10
    hang_sample_seconds: int = 30
    cpu_idle_seconds: float = 0.5
    io_idle_bytes_per_s: int = 256 * 1024
    net_idle_bytes_per_s: int = 32 * 1024

    def __post_init__(self) -> None:
        if not (_MIN_MINUTES <= self.hang_warn_minutes <= _MAX_MINUTES):
            raise ValueError(f"hang_warn_minutes 必须在 {_MIN_MINUTES}–{_MAX_MINUTES} 之间")
        if not (_MIN_SAMPLE <= self.hang_sample_seconds <= _MAX_SAMPLE):
            raise ValueError(f"hang_sample_seconds 必须在 {_MIN_SAMPLE}–{_MAX_SAMPLE} 之间")
        if self.cpu_idle_seconds < 0 or self.io_idle_bytes_per_s < 0:
            raise ValueError("阈值不能为负")

    def window_count(self) -> int:
        """需要连续多少个「无活动窗口」才告警（由参数算出，非常量）。"""
        return max(1, math.ceil(self.hang_warn_minutes * 60 / self.hang_sample_seconds))


@dataclass(frozen=True, slots=True)
class HangEvidence:
    """告警证据（写入事件，便于判断「真卡」还是「慢」）。"""

    cpu_delta: float
    io_read: int
    io_write: int
    net_rx: int = 0
    net_tx: int = 0
    elapsed_seconds: float = 0.0

    def as_text(self) -> str:
        """单行可读文本（GUI/CLI 告警与日志用）。"""
        return (
            f"cpu_delta={self.cpu_delta:.2f}s "
            f"io_read={_mb(self.io_read)}MB io_write={_mb(self.io_write)}MB "
            f"net_rx={_mb(self.net_rx)}MB net_tx={_mb(self.net_tx)}MB "
            f"elapsed={self.elapsed_seconds:.0f}s"
        )


def _mb(value: int) -> str:
    return f"{value / (1024 * 1024):.2f}"


@dataclass(slots=True)
class WatchdogState:
    """纯逻辑状态机（与 asyncio/时钟解耦，便于单测逐窗口驱动）。"""

    config: WatchdogConfig
    has_network: bool
    idle_windows: int = 0
    warnings_suppressed: bool = False

    _last_cpu: float | None = field(default=None, repr=False)
    _last_io: tuple[int, int] | None = field(default=None, repr=False)
    _last_net: tuple[int, int] | None = field(default=None, repr=False)

    def observe(
        self,
        *,
        output_since_last: bool,
        cpu_seconds: float | None,
        io_bytes: tuple[int, int] | None,
        net_bytes: tuple[int, int] | None,
    ) -> HangEvidence | None:
        """喂入一次采样，返回告警证据（``None`` 表示不告警）。

        任一信号「有活动」即重置计数；采样失败（``None``）按「有活动」处理——
        宁可漏报也不误杀。
        """
        cpu_delta = _delta(cpu_seconds, self._last_cpu)
        io_delta = _delta_pair(io_bytes, self._last_io)
        net_delta = _delta_pair(net_bytes, self._last_net)

        self._last_cpu = cpu_seconds
        self._last_io = io_bytes
        self._last_net = net_bytes

        window = self.config.hang_sample_seconds
        cpu_active = cpu_delta is None or cpu_delta >= self.config.cpu_idle_seconds
        io_active = (
            io_delta is None
            or (io_delta[0] + io_delta[1]) >= self.config.io_idle_bytes_per_s * window
        )
        if self.has_network:
            # 只有含 network 画像的工具才校验网络；不含时该信号不参与判定
            net_active = (
                net_delta is None
                or (net_delta[0] + net_delta[1]) >= self.config.net_idle_bytes_per_s * window
            )
        else:
            net_active = False

        if output_since_last or cpu_active or io_active or net_active:
            self.idle_windows = 0
            return None

        self.idle_windows += 1
        if self.idle_windows < self.config.window_count():
            return None

        self.idle_windows = 0
        return HangEvidence(
            cpu_delta=cpu_delta or 0.0,
            io_read=io_delta[0] if io_delta else 0,
            io_write=io_delta[1] if io_delta else 0,
            net_rx=net_delta[0] if net_delta else 0,
            net_tx=net_delta[1] if net_delta else 0,
            elapsed_seconds=self.config.hang_warn_minutes * 60,
        )


def _delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return max(0.0, current - previous)


def _delta_pair(
    current: tuple[int, int] | None, previous: tuple[int, int] | None
) -> tuple[int, int] | None:
    """32 位计数器可能回绕，负值时按 0 处理（不把回绕当活动）。"""
    if current is None or previous is None:
        return None
    return (max(0, current[0] - previous[0]), max(0, current[1] - previous[1]))


class FourSignalWatchdog:
    """把 :class:`WatchdogState` 接到真实时钟与采样器上。"""

    def __init__(
        self,
        *,
        state: WatchdogState,
        cpu_seconds: Callable[[], float | None],
        io_bytes: Callable[[], tuple[int, int] | None],
        net_bytes: Callable[[], tuple[int, int] | None],
        on_suspect: Callable[[HangEvidence], None],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self._state = state
        self._cpu = cpu_seconds
        self._io = io_bytes
        self._net = net_bytes
        self._on_suspect = on_suspect
        self._sleep = sleep
        self._output_seen = False
        self._stopped = False

    def note_output(self) -> None:
        """有任意输出（含无换行残留）时调用，第 1 条信号即视为「有活动」。"""
        self._output_seen = True

    def stop(self) -> None:
        """请求停止（进程结束时调用）。"""
        self._stopped = True

    async def run(self) -> None:
        """按 ``hang_sample_seconds`` 周期采样，直到 :meth:`stop`。"""
        while not self._stopped:
            await self._sleep(self._state.config.hang_sample_seconds)
            if self._stopped:
                return
            evidence = self._state.observe(
                output_since_last=self._output_seen,
                cpu_seconds=self._cpu(),
                io_bytes=self._io(),
                net_bytes=self._net(),
            )
            self._output_seen = False
            if evidence is not None:
                self._on_suspect(evidence)
