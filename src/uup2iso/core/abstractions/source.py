"""数据源契约 `IUupSource`（下载 §2.1；需求 §7.3）。

实现（`UupDumpSource` 等）在 `infrastructure/source/`，归 WP2-T1 起。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from uup2iso.core.models.build import BuildInfo
from uup2iso.core.models.enums import Architecture
from uup2iso.core.models.manifest import Manifest

__all__ = ["BuildQuery", "IUupSource"]


@dataclass(frozen=True, slots=True)
class BuildQuery:
    """构建列表的筛选条件（需求 FR1.1：通道 / 架构 / 语言 / 关键字）。

    全部字段为空表示「不过滤」；实现方负责把空值翻译成「不限」。
    """

    channel: str | None = None
    arch: Architecture | None = None
    language: str | None = None
    keyword: str | None = None
    limit: int = 50


@runtime_checkable
class IUupSource(Protocol):
    """UUP 数据源。

    行为约束（下载 §2.1）：

    * 基址模板化，默认 ``https://uupdump.net/``，不得把路径硬编码在业务代码里；
    * 备用地址按顺序回退，切换记 ``WARN source_failover``；
    * 同一数据源并发请求 ≤ 1，请求间隔 ≥ 300 ms（礼貌约束由实现负责）；
    * 必需字段缺失即 ``SOURCE_SCHEMA_INVALID``，不得用默认值兜底。
    """

    @property
    def base_url(self) -> str:
        """当前生效的数据源基址（用于清单白名单与日志）。"""
        ...

    async def list_builds(self, query: BuildQuery | None = None) -> tuple[BuildInfo, ...]:
        """查询可用的构建列表。

        Args:
            query: 筛选条件；``None`` 等价于空条件（不过滤）。

        Returns:
            构建列表，按发布时间倒序。

        Raises:
            SourceUnavailableError: 全部地址不可达（``SOURCE_UNAVAILABLE``）。
            SourceRateLimitedError: 被限流（``SOURCE_RATE_LIMITED``）。
        """
        ...

    async def get_build(self, build: str, revision: str) -> BuildInfo | None:
        """取单个构建详情。

        Args:
            build: 完整构建号，如 ``"26100.6508"``。
            revision: 修订号，如 ``"6508"``。

        Returns:
            命中返回 :class:`BuildInfo`；不存在或已下架返回 ``None``
            （调用方据此报 ``BUILD_NOT_FOUND`` / ``BUILD_WITHDRAWN``）。
        """
        ...

    async def build_manifest(
        self,
        *,
        build: str,
        revision: str,
        language: str,
        edition: str,
    ) -> Manifest:
        """生成下载清单（下载 §3）。

        实现必须**在返回前**完成 下载 §3.2 的五项自校验；不通过即抛错，
        绝不返回「看起来能用」的清单。

        Args:
            build: 完整构建号。
            revision: 修订号。
            language: 语言标签（小写）。
            edition: edition 标识（小写归一化）。

        Returns:
            已通过自校验的清单。

        Raises:
            ManifestInvalidError: 自校验失败（``MANIFEST_INVALID``）。
            SourceSchemaInvalidError: 必需字段缺失（``SOURCE_SCHEMA_INVALID``）。
        """
        ...
