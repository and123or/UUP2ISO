"""数据源缓存（下载 §2.3）：10 分钟 TTL + 过期容错。

位置与键严格按规范：``{程序目录}\\cache\\source\\<host>\\<key>.json``，
键由 ``host + build + revision + language + edition`` 算出（列表请求用 ``buildlist`` + 关键字）。

两条比规范更严的地方（实测驱动，已在 下载 §2.3 登记）：

1. **按 URL 寿命提前失效**：实测清单里的下载 URL 只有 ~12 分钟寿命，
   光看 10 分钟 TTL 会命中「清单还在、URL 已死」的缓存 → 所以剩余寿命低于
   :data:`URL_SAFETY_MARGIN_SECONDS` 即视为过期，强制刷新；
2. **过期容错只降级、不沉默**：刷新失败可以返回过期缓存，但必须带上
   ``manifest_stale_used age_minutes=<n>`` 与 ``from_cache=True``，让 UI/日志能提示「可能不是最新」。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from uup2iso.core.models.source import CacheState

__all__ = [
    "CACHE_TTL_SECONDS",
    "URL_SAFETY_MARGIN_SECONDS",
    "CacheKey",
    "CacheLookup",
    "SourceCache",
    "build_key",
]

CACHE_TTL_SECONDS = 600
"""缓存有效期（下载 §2.3 的 10 分钟）。"""

URL_SAFETY_MARGIN_SECONDS = 120
"""URL 剩余寿命低于该值即视为过期（实测 URL 只有 ~12 分钟寿命）。"""

_SAFE_HOST = re.compile(r"[^0-9a-zA-Z._-]")


@dataclass(frozen=True, slots=True)
class CacheKey:
    """缓存键的语义组成。"""

    host: str
    name: str
    """``manifest`` 或 ``buildlist``。"""
    build: str = ""
    revision: str = ""
    language: str = ""
    edition: str = ""
    search: str = ""

    def digest(self) -> str:
        raw = "|".join(
            (
                self.host,
                self.name,
                self.build,
                self.revision,
                self.language,
                self.edition,
                self.search,
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class CacheLookup:
    """一次缓存查询的结果。"""

    value: dict[str, object] | None = None
    state: CacheState = CacheState.MISS
    age_seconds: float = 0.0
    url_expired: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def hit(self) -> bool:
        return self.value is not None

    @property
    def fresh(self) -> bool:
        return self.state is CacheState.FRESH

    @property
    def from_cache(self) -> bool:
        """返回值是否来自磁盘缓存（含过期缓存）。"""
        return self.state in (CacheState.FRESH, CacheState.STALE)


class SourceCache:
    """磁盘缓存（只存元数据，不存文件内容）。

    Args:
        root: ``{程序目录}\\cache\\source``。
        ttl_seconds: 有效期，默认 10 分钟。
        clock: 可注入时钟（测试用）。
    """

    def __init__(
        self,
        root: Path,
        *,
        ttl_seconds: int = CACHE_TTL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._root = root
        self._ttl = ttl_seconds
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

    # -- 路径 -------------------------------------------------------------

    def path_for(self, key: CacheKey) -> Path:
        host = _SAFE_HOST.sub("_", key.host) or "unknown"
        return self._root / host / f"{key.name}-{key.digest()}.json"

    # -- 读写 -------------------------------------------------------------

    def get(self, key: CacheKey) -> CacheLookup:
        """读取缓存并判定新鲜度（不联网）。"""
        path = self.path_for(key)
        if not path.is_file():
            return CacheLookup()

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return CacheLookup(state=CacheState.MISS, warnings=(f"cache_corrupt file={path.name}",))

        cached_at = _parse_time(payload.get("cachedAt"))
        if cached_at is None:
            return CacheLookup(
                state=CacheState.MISS, warnings=(f"cache_stale_meta file={path.name}",)
            )

        now = self._now()
        age = max(0.0, (now - cached_at).total_seconds())
        expires_at = _parse_time(payload.get("expiresAt"))
        url_expired = expires_at is not None and expires_at <= now
        near_expiry = (
            expires_at is not None
            and (expires_at - now).total_seconds() <= URL_SAFETY_MARGIN_SECONDS
        )

        value = payload.get("value")
        if not isinstance(value, dict):
            return CacheLookup(state=CacheState.MISS, warnings=(f"cache_corrupt file={path.name}",))

        fresh = age <= self._ttl and not near_expiry
        return CacheLookup(
            value=value,
            state=CacheState.FRESH if fresh else CacheState.STALE,
            age_seconds=age,
            url_expired=url_expired,
        )

    def put(self, key: CacheKey, value: dict[str, object], *, expires_at: datetime | None) -> Path:
        """写入缓存（原子写）。"""
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": {
                "host": key.host,
                "name": key.name,
                "build": key.build,
                "revision": key.revision,
                "language": key.language,
                "edition": key.edition,
                "search": key.search,
            },
            "cachedAt": self._now().isoformat(),
            "expiresAt": expires_at.isoformat() if expires_at else None,
            "value": value,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        tmp.replace(path)
        return path

    def invalidate(self, *, host: str | None = None) -> tuple[Path, ...]:
        """作废缓存：``host`` 为空时清空全部（手动刷新/切换数据源用）。"""
        if host:
            target = self._root / (_SAFE_HOST.sub("_", host) or "unknown")
            if not target.is_dir():
                return ()
            removed = tuple(sorted(target.glob("*.json")))
            for item in removed:
                item.unlink(missing_ok=True)
            return removed

        if not self._root.is_dir():
            return ()
        removed = tuple(sorted(self._root.rglob("*.json")))
        for item in removed:
            item.unlink(missing_ok=True)
        return removed

    # -- 内部 -------------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock()


def build_key(
    *,
    host: str,
    build: str = "",
    revision: str = "",
    language: str = "",
    edition: str = "",
    search: str = "",
) -> CacheKey:
    """构造清单键（下载 §2.3：host + build + revision + language + edition）。"""
    return CacheKey(
        host=host,
        name="manifest",
        build=build,
        revision=revision,
        language=language.lower(),
        edition=edition.lower(),
    )


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
