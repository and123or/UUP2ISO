"""版本目录（P0-0）：把 `26H2 → 26300` 从**数据**里解析出来，而不是写进代码。

* :class:`VersionCatalog` —— 读随包快照 / 从 GitHub 抓取 / 24 小时缓存 / 降级告警；
* 代码中**不得出现具体主版本号**（`tests/test_version_catalog.py` 有 grep 断言守着）。
"""

from __future__ import annotations

from uup2iso.infrastructure.version.catalog import (
    CACHE_TTL_SECONDS,
    GitHubSource,
    VersionCatalog,
    parse_releases,
)

__all__ = ["CACHE_TTL_SECONDS", "GitHubSource", "VersionCatalog", "parse_releases"]
