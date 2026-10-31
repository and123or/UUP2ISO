"""ISO 封装契约 `IIsoBuilder`（引擎 §3.2 R10；详设 §2.6 CV-28；输出 §5）。

实现 `OscdimgIsoBuilder` 在 `infrastructure/iso/`，归 WP3-T8。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from uup2iso.core.abstractions.runtime import CancelToken, ProgressSink
from uup2iso.core.options.execution import IsoBuildRequest

__all__ = ["IIsoBuilder"]


@runtime_checkable
class IIsoBuilder(Protocol):
    """把媒体骨架封装为可引导 ISO。

    约束：

    * 仅走 ``oscdimg``；缺失时只提示安装 ADK，不做替代实现（引擎 §9.2）；
    * 输出必须先写暂存区，校验通过后再原子落位（输出 §2–§3），
      **最终文件名上永不出现未校验内容**；
    * 引导文件按 ``boot_mode`` 组合，保守模式下不刷新引导文件（引擎 §5）。
    """

    async def build(
        self,
        request: IsoBuildRequest,
        *,
        progress: ProgressSink | None = None,
        cancel: CancelToken | None = None,
    ) -> Path:
        """封装 ISO。

        Args:
            request: 媒体根、输出路径、卷标与引导模式。
            progress: 进度接收端。
            cancel: 取消令牌。

        Returns:
            暂存区内的 ISO 路径；**尚未**落位到最终名（落位由输出层做）。

        Raises:
            ToolMissingError: ``oscdimg`` 不可用（提示安装 ADK）。
            IsoBuildError: 封装失败（引导文件缺失、路径过长等）。
        """
        ...
