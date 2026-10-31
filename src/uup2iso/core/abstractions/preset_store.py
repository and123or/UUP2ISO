"""预设存取契约 `IPresetStore`（配置 §4；需求 §7.3）。

实现 `PresetStore` 在 `infrastructure/storage/`，归 WP0-T6。

关键语义（配置 §4.1–§4.2）：

* 预设**快照内联**到 `TaskConfig`（`presetId` + `presetVersion`），运行时**不再回读** `presets\\`；
* 未知 `ruleId` 一律拒绝导入，不做「尽力而为」的部分加载；
* 四类预设各自独立目录，互不影响。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

from uup2iso.core.models.enums import PresetKind

__all__ = ["IPresetStore"]


@runtime_checkable
class IPresetStore(Protocol):
    """精简 / 优化 / 集成 / 无人值守预设的持久化。

    返回的 ``Mapping`` 是**配置片段**的原始形态：``slim``/``optimize`` 为
    ``{origin, ruleIds[], params{}}``，``integrate``/``unattend`` 为对应配置段深拷贝
    （配置 §4.1）。接口只搬运，不解释语义，语义解析归 WP0-T6。
    """

    async def list(self, kind: PresetKind) -> tuple[str, ...]:
        """列出某类预设的名称（按名称升序）。

        Args:
            kind: 预设类别。

        Returns:
            预设名列表；目录不存在时返回空元组（不抛错）。
        """
        ...

    async def load(self, kind: PresetKind, name: str) -> Mapping[str, object]:
        """读取一个预设的内容。

        Args:
            kind: 预设类别。
            name: 预设名。

        Returns:
            预设内容（配置片段）。

        Raises:
            PresetNotFoundError: 不存在。
            PresetInvalidError: 结构不合法（含未知 ``ruleId``，配置 §4.2）。
        """
        ...

    async def save(self, kind: PresetKind, name: str, data: Mapping[str, object]) -> Path:
        """保存预设（已存在则覆盖）。

        Returns:
            实际写入的文件路径。
        """
        ...

    async def export_(self, kind: PresetKind, name: str, target: Path) -> Path:
        """导出预设到指定文件（用于跨机携带）。

        Returns:
            导出后的文件路径。
        """
        ...

    async def import_(self, kind: PresetKind, source: Path) -> str:
        """从文件导入预设。

        Args:
            kind: 预设类别。
            source: 源文件。

        Returns:
            导入后的预设名（同名时按实现策略改名或覆盖）。

        Raises:
            PresetInvalidError: 结构不合法或含未知 ``ruleId``——**整体拒绝，不部分导入**。
        """
        ...
