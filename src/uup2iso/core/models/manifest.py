"""下载清单（manifest）领域模型 —— 字段严格对齐 下载 §3.1。

这里只做**结构承载**与自校验的判定依据；生成、落盘与网络获取见 WP2-T3。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

__all__ = ["Manifest", "ManifestSource", "UupFile"]

SCHEMA_VERSION = "1.0"
"""清单结构版本（下载 §3.1 的 ``schemaVersion``）。"""


@dataclass(frozen=True, slots=True)
class ManifestSource:
    """清单来源信息（下载 §3.1 ``source``）。

    Attributes:
        base_url: 生效的数据源基址，如 ``https://uupdump.net/``。
        fetched_at: 拉取时间（带时区）。
        from_cache: 是否来自过期缓存（下载 §2.3，此时需在 UI 标注「可能不是最新」）。
    """

    base_url: str
    fetched_at: datetime
    from_cache: bool = False


@dataclass(frozen=True, slots=True)
class UupFile:
    """清单中的一个文件（下载 §3.1 ``files[]``）。

    Attributes:
        name: 文件名，清单内唯一（下载 §3.2-2）。
        size: 字节数。
        sha1: 40 位十六进制小写摘要。
        url: 下载地址，必须为 https 且域名在白名单内（下载 §3.2-4）。
        required: 是否为必需文件；必需文件数量须 ≥ 1（下载 §3.2-5）。
        category: 文件分类（可选字段，缺失为 ``None``）。
        sha256: 镜像提供的第二个校验值（可选字段）。
    """

    name: str
    size: int
    sha1: str
    url: str
    required: bool = True
    category: str | None = None
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class Manifest:
    """一次任务要下载的全部文件。

    Attributes:
        build: 完整构建号。
        revision: 修订号。
        language: 语言标签（小写）。
        edition: edition 标识（小写归一化）。
        files: 文件列表，至少一条。
        source: 来源信息。
        schema_version: 结构版本，默认 :data:`SCHEMA_VERSION`。
        parser_version: 解析器版本，随清单落盘便于定位「同构建不同时间解析结果不同」。
        total_bytes: 清单自带的字节总数；须与 :attr:`computed_total_bytes` 相等（下载 §3.2-3）。
        unknown_fields: 站点新增的未知字段名（下载 §2.2，仅记录不报错）。
    """

    build: str
    revision: str
    language: str
    edition: str
    files: tuple[UupFile, ...]
    source: ManifestSource
    schema_version: str = SCHEMA_VERSION
    parser_version: str = "1.0.0"
    total_bytes: int = 0
    unknown_fields: tuple[str, ...] = field(default_factory=tuple)

    @property
    def computed_total_bytes(self) -> int:
        """按 ``files[].size`` 求和得到的实际总字节数。"""
        return sum(item.size for item in self.files)

    @property
    def is_total_consistent(self) -> bool:
        """``totalBytes`` 是否与各文件大小之和一致（下载 §3.2-3）。"""
        return self.total_bytes == self.computed_total_bytes

    @property
    def required_files(self) -> tuple[UupFile, ...]:
        """必需文件子集；下载时缺一即失败。"""
        return tuple(item for item in self.files if item.required)

    @property
    def has_duplicate_names(self) -> bool:
        """文件名是否重复（重复即 ``MANIFEST_INVALID``，下载 §3.2-2）。"""
        names = [item.name for item in self.files]
        return len(names) != len(set(names))
