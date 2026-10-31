"""系统优化契约 `IOptimizer`（需求 FR5；详设 §4；规则 §5）。

实现 `SystemOptimizer` 在 `infrastructure/optimize/`，归 WP5-T1–T5（M2）。
本轮只冻结**能力边界**与**结果可见性**，不预设具体规则。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from uup2iso.core.abstractions.runtime import CancelToken, ProgressSink
from uup2iso.core.models.rules import RuleReport
from uup2iso.core.options.execution import OptimizeRequest

__all__ = ["IOptimizer"]


@runtime_checkable
class IOptimizer(Protocol):
    """离线定制能力：空间回收、注册表 tweak、CompactOS。

    共同约束：

    * 只操作**离线映像**（离线挂载点或已解包映像树），``online`` 上下文一律跳过并
      单列为「不支持」（规则 §5）；
    * 上下文不满足时**跳过但必须可见**——结果里保留 ``skipped_reason``，
      并同时上报进度事件，不得静默丢弃；
    * 高风险/不可逆项在执行前需二次确认（由 GUI/CLI 层完成，接口只暴露参数）。
    """

    async def reclaim(
        self,
        image_root: Path,
        request: OptimizeRequest,
        *,
        progress: ProgressSink | None = None,
        cancel: CancelToken | None = None,
    ) -> RuleReport:
        """空间回收（FR5.1：WinSxS / 缓存 / 临时 / 日志 / 缩略图 / 传递优化等）。

        Args:
            image_root: 离线映像根（如 ``work\\<task>\\image``）。
            request: 要执行的规则与参数。
            progress: 进度接收端（进度不落盘，日志 §5.2）。
            cancel: 取消令牌。

        Returns:
            逐规则结局；跳过项必须出现在报告里（规则 §5）。
        """
        ...

    async def apply_tweaks(
        self,
        image_root: Path,
        request: OptimizeRequest,
        *,
        progress: ProgressSink | None = None,
        cancel: CancelToken | None = None,
    ) -> RuleReport:
        """离线注册表 tweak（FR5.2：加载 SOFTWARE/SYSTEM/DEFAULT/NTUSER hive）。

        注册表写入顺序为「规则先、集成后」，冲突记 ``reg_override``（集成 §6）。
        """
        ...

    async def compact(
        self,
        image_root: Path,
        request: OptimizeRequest,
        *,
        progress: ProgressSink | None = None,
        cancel: CancelToken | None = None,
    ) -> RuleReport:
        """CompactOS 压缩（FR5.3）。

        实验性能力：未在目标系统实测通过前，GUI 不得显示为可用（规则 §3.4）。
        """
        ...
