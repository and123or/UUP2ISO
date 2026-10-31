"""工具供给契约 `IToolProvider`（引擎 §9.1–§9.3、进程 §2.1）。

实现 `ToolProvider` 在 `infrastructure/process/` 或 `infrastructure/tools/`，归 WP0-T7/T8。

设计要点：**定位**与**获取**分开——`locate` 是纯查询（不写磁盘），
`ensure` 才可能触发下载并校验哈希，调用方据此决定是否提示用户。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uup2iso.core.models.enums import EngineKind, ToolKind
from uup2iso.core.models.tools import EngineCapabilities, ToolInfo

__all__ = ["IToolProvider"]


@runtime_checkable
class IToolProvider(Protocol):
    """外部工具定位、自检与按需获取。

    定位顺序固定（进程 §2.1）：``程序目录\\tools\\`` → 设置里的路径 → ``PATH`` → 报错。
    白名单与 SHA256 以随包 ``tools\\tools.lock.json`` 为唯一信任根（引擎 §9.1），
    不联网取清单、不采用上游发布页哈希。
    """

    def locate(self, kind: ToolKind) -> ToolInfo | None:
        """查找工具，不产生任何副作用。

        Args:
            kind: 工具种类。

        Returns:
            命中返回 :class:`ToolInfo`；未找到返回 ``None``（调用方决定是报错还是降级）。
        """
        ...

    async def ensure(self, kind: ToolKind) -> ToolInfo:
        """确保工具可用；必要时从官方通道获取并校验。

        仅「必需工具」会触发获取；``oscdimg`` 只提示安装 ADK，不自动下载（引擎 §9.2）。
        哈希不符即拒绝落位，并保留原文件供排查。

        Args:
            kind: 工具种类。

        Returns:
            可用的工具信息。

        Raises:
            ToolMissingError: 找不到且无法获取（``TOOL_MISSING``）。
            ToolHashMismatchError: 下载物哈希与清单不符（``TOOL_HASH_MISMATCH``）。
        """
        ...

    def capabilities(self, engine: EngineKind) -> EngineCapabilities:
        """取引擎的能力集合（引擎 §4）。

        版本无法解析时必须返回 ``version_known=False`` 的能力对象，
        使上层进入保守模式，而不是「假定支持」。
        """
        ...

    def is_available(self, kind: ToolKind) -> bool:
        """工具当前是否可用（等价于 ``locate(kind) is not None``）。"""
        ...
