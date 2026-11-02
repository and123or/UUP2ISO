"""数据源实现（WP2-T1）。

* :mod:`.base` —— `UupSource` 协议与 `SourceError`；
* :mod:`.uupdump` —— `UupDumpSource`（默认 `https://uupdump.net/`，可换镜像/自建）。

缓存（WP2-T5）与清单生成（WP2-T3）不在本包：这里只负责「把站点响应翻译成领域对象」。
"""

from __future__ import annotations

from uup2iso.infrastructure.source.base import SourceError, UupSource
from uup2iso.infrastructure.source.cache import (
    CACHE_TTL_SECONDS,
    URL_SAFETY_MARGIN_SECONDS,
    CacheKey,
    CacheLookup,
    SourceCache,
    build_key,
)
from uup2iso.infrastructure.source.cached import CachedSource
from uup2iso.infrastructure.source.uupdump import (
    API_BASE_DEFAULT,
    SITE_BASE_DEFAULT,
    UupDumpSource,
)

__all__ = [
    "API_BASE_DEFAULT",
    "CACHE_TTL_SECONDS",
    "URL_SAFETY_MARGIN_SECONDS",
    "CacheKey",
    "CacheLookup",
    "CachedSource",
    "SourceCache",
    "SITE_BASE_DEFAULT",
    "SourceError",
    "UupDumpSource",
    "UupSource",
    "build_key",
]
