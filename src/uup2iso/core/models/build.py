"""构建（build）领域模型 —— 对应 架构 §5 的 `BuildInfo`。

字段分级按 下载 §2.2：`build/revision/arch/languages` 为必需，缺失即
`SOURCE_SCHEMA_INVALID`；`channel/released_at/title` 为可选，缺失只记
`WARN field_missing`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from uup2iso.core.models.enums import Architecture

__all__ = ["BuildInfo"]


@dataclass(frozen=True, slots=True)
class BuildInfo:
    """一个可选的 Windows 构建。

    Attributes:
        build: 完整构建号，形如 ``"26100.6508"``（下载 §2.2 必需字段）。
        revision: 修订号（UUP 修订），形如 ``"6508"``。
        arch: CPU 架构。
        languages: 该构建可用的语言标签（小写，如 ``"zh-cn"``）。
        channel: 通道名（如 Retail/Beta）；可选字段，缺失为 ``None``。
        released_at: 发布时间；可选字段。
        title: 显示名；可选字段。
    """

    build: str
    revision: str
    arch: Architecture
    languages: tuple[str, ...]
    channel: str | None = None
    released_at: datetime | None = None
    title: str | None = None

    @property
    def build_number(self) -> str:
        """主构建号（``26100.6508`` → ``26100``）。

        无人值守规范 §3.1 要求「构建号 → 组件版本」匹配，主构建号是查表键。
        """
        return self.build.split(".", 1)[0]

    @property
    def display_name(self) -> str:
        """UI/日志用的短标识，如 ``Windows 26100.6508 x64``。"""
        label = self.title or f"Windows {self.build}"
        return f"{label} {self.arch.value}"
