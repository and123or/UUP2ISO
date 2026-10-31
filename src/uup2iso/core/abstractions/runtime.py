"""执行期横切契约：取消令牌与进度上报（编排 §8、日志 §5.2）。

两者都只定义 Protocol，实现分别归 WP0-T5（ProcessRunner 的取消传播）与
WP0-T10（LogSink / 进度汇聚）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["CancelToken", "ProgressSink"]


@runtime_checkable
class CancelToken(Protocol):
    """取消令牌（编排 §8）。

    取消顺序（进程 §7.1）：Ctrl-Break 整组 → 5s → terminate → 2s → 强杀进程树；
    业务代码只负责**在安全点检查并退出**，不直接杀进程。
    取消后保留中间产物（退出码 3），清理是显式动作。
    """

    def is_cancelled(self) -> bool:
        """是否已请求取消。

        Returns:
            已请求取消返回 ``True``；调用方应在每个子步骤边界检查。
        """
        ...

    def raise_if_cancelled(self) -> None:
        """已取消则抛出取消异常，供无法返回值的深层调用使用。

        Raises:
            BaseException: 实现方定义的取消异常（不记为 ERROR，退出码 3）。
        """
        ...


@runtime_checkable
class ProgressSink(Protocol):
    """进度接收端（日志 §5.2：**进度只显示，绝不落盘**）。

    实现方负责把进度送到 GUI 进度条 / CLI 终端；日志侧只在
    ``event=start`` / ``event=finish`` 记录起止与结果，不写百分比。
    """

    def report(
        self,
        stage: str,
        *,
        substep: str | None = None,
        percent: float | None = None,
        message: str | None = None,
    ) -> None:
        """上报一次进度。

        Args:
            stage: 8 阶段名之一（``Prepare``/``Fetch``/.../``Output``，编排 §2）。
            substep: 阶段内子步骤名（如 ``probe``、``mount``），无则 ``None``。
            percent: 0–100 的完成度；不确定时为 ``None``（进度条走不确定态）。
            message: 面向用户的一行说明，不参与日志落盘。
        """
        ...
