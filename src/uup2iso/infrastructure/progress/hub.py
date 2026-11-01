"""进度通道（进程 §6）：单调、节流、**绝不落盘**。

为什么单独一个模块：进度和日志的规则正好相反——

* 日志：只记头尾，进度一律不写（日志 §5.2）；
* 进度：高频刷新、可回退噪声要吞掉（单调不减）、对 UI 每秒最多 5 次。

把它做成独立的 hub，日志侧就永远没有「顺手把进度写进去」的机会。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["ProgressHub", "ProgressUpdate"]

_DEFAULT_INTERVAL = 0.2


@dataclass(frozen=True, slots=True)
class ProgressUpdate:
    """一次对外上报。"""

    stage: str
    substep: str | None
    percent: float | None
    message: str | None
    ts: float


class ProgressHub:
    """``ProgressSink`` 的实现：单调 + 节流 + 多订阅者。

    Args:
        min_interval: 同一进度键的最小上报间隔（默认 0.2s ≈ 5 次/秒）。
        clock: 可注入时钟（测试用）。
    """

    def __init__(
        self,
        *,
        min_interval: float = _DEFAULT_INTERVAL,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._interval = min_interval
        self._clock = clock or time.monotonic
        self._listeners: list[Callable[[ProgressUpdate], None]] = []
        self._last_percent: dict[tuple[str, str | None], float] = {}
        self._last_emit: dict[tuple[str, str | None], float] = {}
        self._snapshot: dict[tuple[str, str | None], ProgressUpdate] = {}

    # -- ProgressSink -----------------------------------------------------

    def report(
        self,
        stage: str,
        *,
        substep: str | None = None,
        percent: float | None = None,
        message: str | None = None,
    ) -> bool:
        """上报一次进度；返回是否真的对外发了一次。

        * ``percent`` 比上次小 → 当作工具重绘丢弃（单调不减）；
        * 距上次上报不足 ``min_interval`` → 节流丢弃；
        * ``percent is None`` 的「不确定态」不参与单调判断，但同样节流。
        """
        key = (stage, substep)
        now = self._clock()

        if percent is not None:
            previous = self._last_percent.get(key)
            if previous is not None and percent < previous:
                return False
            self._last_percent[key] = percent

        last = self._last_emit.get(key)
        if last is not None and now - last < self._interval:
            return False

        self._last_emit[key] = now
        update = ProgressUpdate(
            stage=stage,
            substep=substep,
            percent=percent,
            message=message,
            ts=now,
        )
        self._snapshot[key] = update
        for listener in tuple(self._listeners):
            listener(update)
        return True

    # -- 订阅与快照 -------------------------------------------------------

    def subscribe(self, listener: Callable[[ProgressUpdate], None]) -> Callable[[], None]:
        """注册监听器，返回取消函数。"""
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsubscribe

    def snapshot(self) -> tuple[ProgressUpdate, ...]:
        """当前各进度键的最后一次上报（GUI 重绘用）。"""
        return tuple(self._snapshot.values())

    def reset(self, stage: str | None = None) -> None:
        """阶段切换时清空单调基线（避免上一个阶段的 100% 压住新阶段）。"""
        if stage is None:
            self._last_percent.clear()
            self._last_emit.clear()
            return
        for key in [key for key in self._last_percent if key[0] == stage]:
            self._last_percent.pop(key, None)
            self._last_emit.pop(key, None)

    # 让静态检查知道这里永远不会碰日志
    def attach_log(self, _sink: Any) -> None:
        """故意不提供：进度**不得**写入日志（日志 §5.2）。调用即报错。"""
        raise RuntimeError("进度不得写入日志（日志 §5.2）；请把进度送到 UI 而不是 LogSink")
