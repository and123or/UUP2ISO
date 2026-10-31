"""外部工具与引擎能力模型 —— 对应 引擎 §3.1（工具角色）、§4（能力探测）。

`ToolProvider`（WP0-T7）实现探测后产出这些值对象；业务侧只读不写。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from uup2iso.core.models.enums import CompressionKind, EngineKind, ToolKind

__all__ = ["EngineCapabilities", "ToolInfo", "ToolSource"]


class ToolSource(StrEnum):
    """工具的来源，用于日志解释「为什么用的是这一份」（进程 §2.1 定位顺序）。"""

    PACKAGED = "packaged"
    """程序目录 ``tools\\`` 下的随包副本（首选）。"""

    CONFIGURED = "configured"
    """用户在设置里显式指定的路径。"""

    PATH = "path"
    """从系统 PATH 找到。"""

    AUTO_FETCHED = "auto_fetched"
    """按 ``tools\\tools.lock.json`` 自动获取并校验通过（引擎 §9.2）。"""


@dataclass(frozen=True, slots=True)
class ToolInfo:
    """一个已定位的外部工具。

    Attributes:
        kind: 工具种类。
        path: 可执行文件绝对路径。
        source: 来源，见 :class:`ToolSource`。
        version: 版本字符串；解析失败为 ``None``，此时按「未知版本」进保守模式（引擎 §4）。
        sha256: 文件摘要；随包/自动获取的工具有值，PATH 找到的可能为 ``None``。
    """

    kind: ToolKind
    path: Path
    source: ToolSource
    version: str | None = None
    sha256: str | None = None

    @property
    def version_known(self) -> bool:
        """版本是否可判定。``False`` 触发保守模式（引擎 §5）。"""
        return bool(self.version)


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    """某个映像引擎实测到的能力集合（引擎 §4、§4.1）。

    Attributes:
        engine: 引擎种类。
        version_known: 版本是否可判定；``False`` 一律按保守模式处理。
        compressions: 可写出的压缩类型；不确定的能力**不得**列入。
        can_write_esd: 能否产出 ESD（LZMS）。
        can_verify: 能否做全文件校验（``wimlib verify`` 为硬依赖）。
        can_mount: 能否挂载（仅 DISM 具备，引擎 §3.2 R6）。
        supports_ref_export: 是否支持 ``--ref`` 分片引用导出（保守模式禁用）。
    """

    engine: EngineKind
    version_known: bool = False
    compressions: frozenset[CompressionKind] = frozenset()
    can_write_esd: bool = False
    can_verify: bool = False
    can_mount: bool = False
    supports_ref_export: bool = False

    def supports(self, compression: CompressionKind) -> bool:
        """该引擎能否写出指定压缩类型。"""
        return compression in self.compressions

    @property
    def is_conservative(self) -> bool:
        """是否需要保守模式：版本未知，或关键能力缺失（引擎 §5）。"""
        return not self.version_known or not self.compressions
