"""数据源缓存门禁（下载 §2.3）。

覆盖：10 分钟 TTL、**按 URL 寿命提前失效**、过期容错（刷新失败降级 + 告警）、
键的唯一性、作废、以及装饰器层「命中不联网」。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from uup2iso.core.models.enums import Architecture
from uup2iso.core.models.manifest import UupFile
from uup2iso.core.models.source import CacheState, SourcePayload
from uup2iso.infrastructure.source import (
    CACHE_TTL_SECONDS,
    URL_SAFETY_MARGIN_SECONDS,
    CachedSource,
    SourceCache,
    build_key,
)
from uup2iso.infrastructure.source.base import SourceError

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
# 用固定时钟构造 payload：不受被测代码内部计算影响
FAKE_PAYLOAD = SourcePayload(
    build="26100.9725",
    files=(UupFile(name="a.cab", size=10, sha1="a" * 40, url="http://x.microsoft.com/a.cab"),),
    arch="amd64",
    expires_at=NOW + timedelta(minutes=30),
)


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_cache(tmp_path: Path, clock: Clock, ttl: int = CACHE_TTL_SECONDS) -> SourceCache:
    return SourceCache(tmp_path / "cache", ttl_seconds=ttl, clock=clock)


def key(**overrides: object) -> object:
    base: dict[str, object] = {
        "host": "uupdump.net",
        "build": "26100.9725",
        "revision": "9725",
        "language": "zh-CN",
        "edition": "Professional",
    }
    base.update(overrides)
    return build_key(**base)  # type: ignore[arg-type]


# --- 基本读写 ---------------------------------------------------------------


def test_miss_on_empty(tmp_path: Path) -> None:
    assert make_cache(tmp_path, Clock()).get(key()).state is CacheState.MISS


def test_fresh_hit_within_ttl(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    cache.put(key(), {"files": [{"name": "a.cab"}]}, expires_at=NOW + timedelta(minutes=12))

    clock.advance(60)
    lookup = cache.get(key())

    assert lookup.state is CacheState.FRESH
    assert lookup.value == {"files": [{"name": "a.cab"}]}
    assert not lookup.url_expired


def test_stale_after_ttl(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    cache.put(key(), {"files": []}, expires_at=None)

    clock.advance(CACHE_TTL_SECONDS + 1)
    assert cache.get(key()).state is CacheState.STALE


def test_near_expiry_url_forces_stale(tmp_path: Path) -> None:
    """实测：清单 URL 只有 ~12 分钟寿命 → 剩余不足安全边界即判过期。"""
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    cache.put(
        key(), {"files": []}, expires_at=NOW + timedelta(seconds=URL_SAFETY_MARGIN_SECONDS - 10)
    )

    clock.advance(5)
    assert cache.get(key()).state is CacheState.STALE


def test_expired_url_is_flagged(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    cache.put(key(), {"files": []}, expires_at=NOW - timedelta(seconds=1))

    lookup = cache.get(key())
    assert lookup.state is CacheState.STALE
    assert lookup.url_expired is True


def test_key_differs_by_edition_and_language(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, Clock())
    first = cache.path_for(key())
    second = cache.path_for(key(edition="core"))
    third = cache.path_for(key(language="en-us"))

    assert first != second != third != first
    assert first.parent.name == "uupdump.net"


def test_cache_file_is_json_with_meta(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, Clock())
    path = cache.put(key(), {"files": []}, expires_at=NOW + timedelta(minutes=10))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["key"]["build"] == "26100.9725"
    assert payload["key"]["edition"] == "professional"
    assert payload["cachedAt"]
    assert payload["expiresAt"]


def test_corrupt_cache_is_treated_as_miss(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, Clock())
    path = cache.path_for(key())
    path.parent.mkdir(parents=True)
    path.write_text("{ broken", encoding="utf-8")

    lookup = cache.get(key())
    assert lookup.state is CacheState.MISS
    assert any("cache_corrupt" in warning for warning in lookup.warnings)


def test_invalidate_by_host_and_all(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, Clock())
    cache.put(key(), {"files": []}, expires_at=None)
    cache.put(key(host="mirror.example.com"), {"files": []}, expires_at=None)

    removed = cache.invalidate(host="uupdump.net")
    assert len(removed) == 1
    assert cache.get(key()).state is CacheState.MISS
    assert cache.get(key(host="mirror.example.com")).hit

    cache.invalidate()
    assert cache.get(key(host="mirror.example.com")).state is CacheState.MISS


# --- 装饰器层 ---------------------------------------------------------------


class FakeInner:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def list_builds(self, *, search: str | None = None) -> tuple[object, ...]:
        from types import SimpleNamespace

        self.calls += 1
        if self.fail:
            raise SourceError("boom", code="SOURCE_UNAVAILABLE")  # type: ignore[arg-type]
        return (
            SimpleNamespace(
                build="26100.9725",
                revision="9725",
                arch=Architecture.X64,
                title="Windows 11",
                id="uuid-1",
                released_at=NOW,
                languages=(),
            ),
        )

    def list_languages(self, _id: str) -> tuple[str, ...]:
        return ("zh-cn",)

    def list_editions(self, _id: str, _lang: str) -> tuple[object, ...]:
        return ()

    def get_files(self, _id: str, *, language: str, edition: str) -> SourcePayload:
        self.calls += 1
        if self.fail:
            raise SourceError("boom", code="SOURCE_UNAVAILABLE")  # type: ignore[arg-type]
        return FAKE_PAYLOAD


def test_cached_source_hit_does_not_call_inner(tmp_path: Path) -> None:
    clock = Clock()
    inner = FakeInner()
    source = CachedSource(inner, make_cache(tmp_path, clock), host="uupdump.net")

    first = source.get_files("uuid-1", language="zh-cn", edition="professional")
    assert inner.calls == 1
    assert first.from_cache is False

    clock.advance(30)
    second = source.get_files("uuid-1", language="zh-cn", edition="professional")
    assert inner.calls == 1, "TTL 内不该再联网"
    assert second.from_cache is True
    assert len(second.files) == 1


def test_cached_source_refreshes_when_stale(tmp_path: Path) -> None:
    clock = Clock()
    inner = FakeInner()
    source = CachedSource(inner, make_cache(tmp_path, clock), host="uupdump.net")
    source.get_files("uuid-1", language="zh-cn", edition="professional")

    clock.advance(CACHE_TTL_SECONDS + 60)
    source.get_files("uuid-1", language="zh-cn", edition="professional")

    assert inner.calls == 2


def test_stale_fallback_when_refresh_fails(tmp_path: Path) -> None:
    """过期容错：刷新失败可以用过期缓存，但必须带告警（下载 §2.3）。"""
    clock = Clock()
    inner = FakeInner()
    source = CachedSource(inner, make_cache(tmp_path, clock), host="uupdump.net")
    source.get_files("uuid-1", language="zh-cn", edition="professional")

    inner.fail = True
    clock.advance(CACHE_TTL_SECONDS + 120)
    payload = source.get_files("uuid-1", language="zh-cn", edition="professional")

    assert payload.from_cache is True
    assert payload.files
    assert "manifest_stale_used" in " ".join(source.warnings)
    assert any("manifest_stale_used" in item for item in source.warnings)


def test_refresh_failure_without_cache_raises(tmp_path: Path) -> None:
    source = CachedSource(FakeInner(fail=True), make_cache(tmp_path, Clock()), host="uupdump.net")
    try:
        source.get_files("uuid-1", language="zh-cn", edition="professional")
    except SourceError:
        return
    raise AssertionError("没有缓存时刷新失败必须抛错")


def test_build_list_is_cached(tmp_path: Path) -> None:
    clock = Clock()
    inner = FakeInner()
    source = CachedSource(inner, make_cache(tmp_path, clock), host="uupdump.net")

    first = source.list_builds(search="26100")
    second = source.list_builds(search="26100")

    assert inner.calls == 1
    assert first[0].build == "26100.9725"
    assert second[0].build == "26100.9725"


def test_languages_and_editions_are_not_cached(tmp_path: Path) -> None:
    inner = FakeInner()
    source = CachedSource(inner, make_cache(tmp_path, Clock()), host="uupdump.net")
    source.list_languages("uuid-1")
    source.list_languages("uuid-1")
    assert inner.calls == 0, "语言/edition 直接透传，不进缓存"
