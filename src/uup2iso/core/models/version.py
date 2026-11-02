"""Windows 版本族模型（P0-0 版本目录）。

**设计底线**：`26H2 → 26300` 这类映射只存在于**数据**（随包快照或 GitHub 抓取结果），
代码里不出现任何具体版本号。这一层只定义结构与判定，不做猜测。
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["VersionResolution", "WindowsRelease"]


@dataclass(frozen=True, slots=True)
class WindowsRelease:
    """一个版本族与它的主版本号。

    Attributes:
        family: 版本族名，如 `26H2`。
        major: 主版本号，如 `26300`（不含修订号）。
        channel: 通道（Retail/Insider/…），未知为空。
        source: 数据来源（`snapshot` 或 `github:<owner/repo>@<sha>`）。
    """

    family: str
    major: int
    channel: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class VersionResolution:
    """一次版本判定结果（供 `probe.json`、无人值守映射与日志使用）。"""

    family: str = ""
    major: int = 0
    build: str = ""
    """完整构建号，如 `26300.9818`；未知为空。"""
    revision: str = ""
    language: str = ""
    edition: str = ""
    source: str = ""
    """判定依据：`snapshot` / `github:<repo>@<sha>` / `directory-name` / `unknown`。"""
    stale: bool = False
    """数据是否来自过期/离线快照（此时 UI 需提示"可能不是最新"）。"""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def known(self) -> bool:
        return bool(self.family) and self.major > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "major": self.major,
            "build": self.build,
            "revision": self.revision,
            "language": self.language,
            "edition": self.edition,
            "source": self.source,
            "stale": self.stale,
            "warnings": list(self.warnings),
        }
