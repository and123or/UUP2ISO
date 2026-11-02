"""数据源抽象（下载 §2.1）。

只有两个能力，刻意保持窄：**列构建** 与 **取清单**；语言/edition 是取清单的中间步骤。
地址**模板化**：业务代码只认 `base_url` + 路径模板，不硬编码站点路径。

错误分类（下载 §2.4）：
* `SOURCE_UNAVAILABLE` —— 不可达/超时/非 200；
* `SOURCE_RATE_LIMITED` —— 429；
* `SOURCE_SCHEMA_INVALID` —— 响应结构不符（必需字段缺失、JSON 坏掉）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from uup2iso.core.errors import DependencyError, ErrorCode
from uup2iso.core.models.build import BuildInfo
from uup2iso.core.models.source import EditionInfo, SourcePayload

__all__ = ["SourceError", "UupSource"]


class SourceError(DependencyError):
    """数据源错误（退出码 4）。具体码由抛出处给出。"""

    default_code = ErrorCode.SOURCE_UNAVAILABLE


@runtime_checkable
class UupSource(Protocol):
    """数据源接口。"""

    def list_builds(self, *, search: str | None = None) -> tuple[BuildInfo, ...]:
        """列出可用构建（可按关键字过滤）。"""
        ...

    def list_languages(self, build_id: str) -> tuple[str, ...]:
        """列出某构建可用语言标签。"""
        ...

    def list_editions(self, build_id: str, language: str) -> tuple[EditionInfo, ...]:
        """列出某构建+语言下的 edition。"""
        ...

    def get_files(self, build_id: str, *, language: str, edition: str) -> SourcePayload:
        """取文件清单（含大小/SHA1/URL）。"""
        ...
