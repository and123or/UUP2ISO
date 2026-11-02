"""数据源领域模型（下载 §2.1、§2.2）。

只承载「从数据源拿到的东西」，**不含缓存与清单生成**：
* 缓存（10 分钟 TTL + 过期容错）属 WP2-T5；
* 清单（`manifest.json` + 五项自校验）属 WP2-T3。

字段分级（下载 §2.2）：必需字段缺失 → `SOURCE_SCHEMA_INVALID`；可选缺失 → `WARN`；
未知字段收集到 :attr:`SourcePayload.unknown_fields`，便于后续适配。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from uup2iso.core.models.manifest import UupFile

__all__ = ["CacheState", "EditionInfo", "SourcePayload"]


class CacheState(StrEnum):
    """一次缓存查询的结果（下载 §2.3）。"""

    MISS = "miss"
    """没有缓存。"""

    FRESH = "fresh"
    """命中且仍在有效期内。"""

    STALE = "stale"
    """命中但已过期（仅在刷新失败时降级使用）。"""


@dataclass(frozen=True, slots=True)
class EditionInfo:
    """一个可选的 edition（数据源码 + 显示名）。

    Attributes:
        code: 站点用的代码，如 ``PROFESSIONAL``。
        display_name: 显示名，如 ``Windows Pro``；站点没给时退化为 code。
    """

    code: str
    display_name: str = ""


@dataclass(frozen=True, slots=True)
class SourcePayload:
    """一次 `get_files` 的完整结果。

    Attributes:
        build: 构建号（含修订），如 ``26100.994``。
        update_name: 更新名（站点给的显示串）。
        arch: 原始架构串（如 ``amd64``）；归一化留给调用方。
        files: 文件清单（名/大小/SHA1/URL/必需性）。
        expires_at: 清单里最早的 URL 过期时间；``None`` 表示站点未提供。
        source_dir: 生效的数据源基址。
        api_version: 站点的 API 版本串（便于排查「同一构建不同时间解析不同」）。
        unknown_fields: 站点新增但本解析器未识别的字段名。
        warnings: 解析过程中的告警（可选字段缺失、文件名归一化等）。
    """

    build: str
    files: tuple[UupFile, ...] = field(default_factory=tuple)
    update_name: str = ""
    arch: str = ""
    expires_at: datetime | None = None
    source_dir: str = ""
    api_version: str = ""
    unknown_fields: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    from_cache: bool = False
    """是否来自磁盘缓存（含过期缓存）；清单里的 ``source.fromCache`` 用它。"""

    cache_age_seconds: float | None = None
    """缓存的年龄（秒）；未命中为 ``None``。"""

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)

    @property
    def url_expired(self) -> bool:
        """清单里的 URL 是否已过期（过期后必须重新拉清单，不能拿旧 URL 下）。"""
        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)
