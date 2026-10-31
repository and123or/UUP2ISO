"""映像引擎契约 `IImageEngine`（引擎 §3.2 路由表；需求 §7.3）。

两个实现：``WimlibImageEngine`` / ``DismImageEngine``，由 ``ImageEngineRouter``
按**操作**选主/备（引擎 §3.2）。接口按「能力」划分而不是按「工具」划分，
使路由层可以在不改业务代码的前提下替换实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from uup2iso.core.abstractions.runtime import CancelToken, ProgressSink
from uup2iso.core.models.enums import EngineKind
from uup2iso.core.models.image import ImageInfo
from uup2iso.core.models.verification import VerifyReport
from uup2iso.core.options.execution import ExportRequest, MountRequest

__all__ = ["IImageEngine"]


@runtime_checkable
class IImageEngine(Protocol):
    """映像读写与挂载能力。

    能力边界（引擎 §3.2，实现方不得越界假装支持）：

    * 读信息 R1、导出 R2、生成 ESD R3、校验 R5 —— wimlib 主、DISM 备；
    * 挂载/卸载 R6、加删包 R7 —— **仅 DISM**，wimlib 无挂载语义；
    * 缺能力时必须显式报错，不得静默降级或改写产物。
    """

    @property
    def kind(self) -> EngineKind:
        """引擎种类，用于日志与路由决策。"""
        ...

    async def get_info(self, image: Path) -> ImageInfo:
        """读取映像信息（R1：索引 / edition / 压缩类型）。

        Args:
            image: 映像路径（WIM/ESD，或已解包的 install.*）。

        Returns:
            探测结果；``editions`` 必须完整（输入 §4）。

        Raises:
            ImageUnreadableError: 文件损坏或非 Windows 映像（``INPUT_UNSUPPORTED``）。
        """
        ...

    async def export(
        self,
        request: ExportRequest,
        *,
        progress: ProgressSink | None = None,
        cancel: CancelToken | None = None,
    ) -> Path:
        """导出/转换映像到目标压缩类型（R2/R3）。

        保守模式下调用方必须先行拦下 ``LZMS``（引擎 §5），实现侧不负责猜测意图。

        Returns:
            目标文件路径（等于 ``request.target``）。

        Raises:
            CapabilityMissingError: 该引擎写不出所请求的压缩类型。
        """
        ...

    async def mount(
        self,
        request: MountRequest,
        *,
        progress: ProgressSink | None = None,
        cancel: CancelToken | None = None,
    ) -> Path:
        """挂载映像（R6，仅 DISM）。

        Returns:
            挂载点路径（等于 ``request.mount_dir``）。

        Raises:
            CapabilityMissingError: 引擎不支持挂载。
            AdminRequiredError: 非管理员（DISM 挂载需提权，进程 §2.4）。
        """
        ...

    async def unmount(self, mount_dir: Path, *, commit: bool = True) -> None:
        """卸载并提交或丢弃改动（R6）。

        Args:
            mount_dir: 挂载点。
            commit: ``True`` 提交改动；``False`` 等价 ``/Discard``（阶段级回滚，规则 §5）。

        Raises:
            MountStateError: 挂载点未挂载或状态异常（残留由调用方清理）。
        """
        ...

    async def verify(
        self,
        image: Path,
        *,
        progress: ProgressSink | None = None,
        cancel: CancelToken | None = None,
    ) -> VerifyReport:
        """全文件校验（R5：``wimlib verify``，属硬依赖，无备用实现）。"""
        ...
