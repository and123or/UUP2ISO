"""进程事件与运行结果模型（进程 §5.1、§6、§9.2）。

事件是「工具输出」的统一形状：分类结果、通道、编码、截断标记都在这里，
上层（编排 / GUI / 日志）只消费事件，不接触原始字节。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from uup2iso.core.models.enums import ToolKind

__all__ = ["EventKind", "ProcessEvent", "ProcessResult"]


class EventKind(StrEnum):
    """行分类（进程 §5.1）。"""

    PROGRESS = "progress"
    """进度/速度/ETA/重绘行：只更新进度模型，**不写日志**（日志 §5.2）。"""

    RESULT = "result"
    """结果性语句：写日志，可作阶段结论依据。"""

    LINE = "line"
    """其余普通输出（含警告/诊断）：写日志，受采样约束。"""


@dataclass(frozen=True, slots=True)
class ProcessEvent:
    """一行被解码并被分类后的输出。

    Attributes:
        kind: 分类结果。
        channel: ``stdout`` 或 ``stderr``（进程 §4.1）。
        text: 已解码文本（保留原始 ``\\r``，便于识别重绘行）。
        ts: 到达时刻（``time.monotonic()``，仅用于排序与耗时）。
        encoding: 实际使用的编码；回退时与工具默认编码不同（进程 §4.2 要求记 ``encoding``）。
        truncated: 是否因超过 64 KB 被截断。
        partial: 是否为流结束前无换行的残留数据。
        raw_size: 原始字节数（截断时仍为真实大小）。
    """

    kind: EventKind
    channel: str
    text: str
    ts: float
    encoding: str = "utf-8"
    truncated: bool = False
    partial: bool = False
    raw_size: int = 0


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """一次外部工具调用的结果（成功路径）。

    Attributes:
        tool: 工具种类。
        returncode: 工具原始返回码（不是进程退出码）。
        duration_seconds: 总耗时。
        progress_events: 进度事件条数（不落盘，仅统计）。
        line_events: 普通行条数。
        result_events: 结果行条数。
        last_lines: 最后若干条 ``line``/``result`` 文本（错误对象与日志采样用）。
        last_channel: 最后一条非进度行所在通道（错误对象必须带，进程 §9.2）。
        warnings: 运行期告警（编码回退、锁等待、并发被忽略、疑似卡死）。
        hang_suspected: 看门狗是否报过 ``suspected_hang``（绝不自动终止）。
    """

    tool: ToolKind
    returncode: int
    duration_seconds: float
    progress_events: int = 0
    line_events: int = 0
    result_events: int = 0
    last_lines: tuple[str, ...] = field(default_factory=tuple)
    last_channel: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    hang_suspected: bool = False

    @property
    def succeeded(self) -> bool:
        """工具是否返回 0。"""
        return self.returncode == 0
