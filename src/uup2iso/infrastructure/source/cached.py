"""带缓存的数据源装饰器（下载 §2.3）。

``CachedSource`` 包住任意 :class:`UupSource`，只缓存**元数据**（构建列表 / 清单），不缓存文件内容：

| 情况 | 行为 |
| --- | --- |
| 未命中 | 联网取，写入缓存 |
| 命中且新鲜 | 直接返回，**不联网** |
| 已过期 | 联网刷新；成功即覆盖 |
| 已过期且刷新失败 | **降级使用过期缓存**，附 `manifest_stale_used age_minutes=<n>` 与 `from_cache=True` |

「新鲜」不只由 10 分钟 TTL 决定：清单里的下载 URL 只有 ~12 分钟寿命，
剩余不足 :data:`~.cache.URL_SAFETY_MARGIN_SECONDS` 时同样判为过期（否则命中一份「URL 已死」的清单）。
"""

from __future__ import annotations

from typing import Any

from uup2iso.core.models.build import BuildInfo
from uup2iso.core.models.manifest import UupFile
from uup2iso.core.models.source import EditionInfo, SourcePayload
from uup2iso.infrastructure.source.base import SourceError, UupSource
from uup2iso.infrastructure.source.cache import CacheKey, CacheLookup, SourceCache, build_key

__all__ = ["CachedSource"]


class CachedSource:
    """把 `UupSource` 包上磁盘缓存。

    Args:
        inner: 真实数据源。
        cache: 磁盘缓存。
        host: 缓存分目录用的 host（通常是数据源 host）。
        log: 可选日志（写 `cache_hit` / `manifest_stale_used` 等事件）。
    """

    def __init__(
        self,
        inner: UupSource,
        cache: SourceCache,
        *,
        host: str,
        log: Any = None,
    ) -> None:
        self._inner = inner
        self._cache = cache
        self._host = host
        self._log = log
        self.warnings: list[str] = []

    def bind(self, *, log: Any = None) -> None:
        """运行期绑定日志（组合根先建对象、后接上下文）。"""
        if log is not None:
            self._log = log

    # -- UupSource --------------------------------------------------------

    def list_builds(self, *, search: str | None = None) -> tuple[BuildInfo, ...]:
        key = CacheKey(host=self._host, name="buildlist", search=search or "")
        lookup = self._cache.get(key)
        if lookup.fresh:
            builds = _builds_from(lookup.value or {})
            if builds:
                self._log_event("cache_hit", kind="buildlist", age_seconds=int(lookup.age_seconds))
                return builds

        try:
            builds = self._inner.list_builds(search=search)
        except SourceError as exc:
            stale = _builds_from((lookup.value or {}) if lookup.hit else {})
            if stale:
                self._note_stale(lookup, kind="buildlist")
                return stale
            raise exc

        self._cache.put(key, _builds_to(builds), expires_at=None)
        return builds

    def list_languages(self, build_id: str) -> tuple[str, ...]:
        # 语言/edition 不缓存：它们很小、且必须与当前构建一致（避免键爆炸）
        return self._inner.list_languages(build_id)

    def list_editions(self, build_id: str, language: str) -> tuple[EditionInfo, ...]:
        return self._inner.list_editions(build_id, language)

    def get_files(self, build_id: str, *, language: str, edition: str) -> SourcePayload:
        key = build_key(
            host=self._host,
            build=build_id,
            language=language,
            edition=edition,
        )
        lookup = self._cache.get(key)
        if lookup.fresh:
            payload = _payload_from(lookup.value or {}, source_dir="", from_cache=True)
            if payload is not None:
                self._log_event(
                    "cache_hit",
                    kind="manifest",
                    age_seconds=int(lookup.age_seconds),
                    files=len(payload.files),
                )
                return _with_cache_meta(payload, lookup)

        try:
            payload = self._inner.get_files(build_id, language=language, edition=edition)
        except SourceError as exc:
            if lookup.hit:
                cached = _payload_from(lookup.value or {}, source_dir="", from_cache=True)
                if cached is not None:
                    self._note_stale(lookup, kind="manifest", files=len(cached.files))
                    result = _with_cache_meta(cached, lookup)
                    if lookup.url_expired:
                        result = _append_warning(result, "manifest_url_expired")
                    return result
            raise exc

        self._cache.put(
            key,
            _payload_to(payload),
            expires_at=payload.expires_at,
        )
        self._log_event("cache_store", kind="manifest", files=len(payload.files))
        return payload

    # -- 维护 -------------------------------------------------------------

    def invalidate(self, *, all_hosts: bool = False) -> tuple[str, ...]:
        """手动刷新/切换数据源时作废缓存。"""
        removed = self._cache.invalidate(host=None if all_hosts else self._host)
        return tuple(item.name for item in removed)

    # -- 内部 -------------------------------------------------------------

    def _note_stale(self, lookup: CacheLookup, *, kind: str, files: int | None = None) -> None:
        minutes = int(lookup.age_seconds // 60)
        warning = f"manifest_stale_used age_minutes={minutes}"
        self.warnings.append(warning)
        self._log_event("manifest_stale_used", kind=kind, age_minutes=minutes, files=files)

    def _log_event(self, event: str, **fields: object) -> None:
        if self._log is not None:
            self._log.info("infra.source", event, event=event, **fields)


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def _builds_to(builds: tuple[BuildInfo, ...]) -> dict[str, object]:
    return {
        "builds": [
            {
                "build": item.build,
                "revision": item.revision,
                "arch": item.arch.value,
                "title": item.title,
                "id": item.id,
                "releasedAt": item.released_at.isoformat() if item.released_at else None,
            }
            for item in builds
        ]
    }


def _builds_from(value: dict[str, object]) -> tuple[BuildInfo, ...]:
    raw = value.get("builds")
    if not isinstance(raw, list):
        return ()
    builds: list[BuildInfo] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            builds.append(
                BuildInfo(
                    build=str(item["build"]),
                    revision=str(item.get("revision", "")),
                    arch=_arch(item.get("arch")),
                    languages=(),
                    title=str(item.get("title") or "") or None,
                    id=str(item.get("id") or ""),
                    released_at=_time(item.get("releasedAt")),
                )
            )
        except (KeyError, ValueError):
            continue
    return tuple(builds)


def _payload_to(payload: SourcePayload) -> dict[str, object]:
    return {
        "build": payload.build,
        "updateName": payload.update_name,
        "arch": payload.arch,
        "apiVersion": payload.api_version,
        "sourceDir": payload.source_dir,
        "expiresAt": payload.expires_at.isoformat() if payload.expires_at else None,
        "unknownFields": list(payload.unknown_fields),
        "warnings": list(payload.warnings),
        "files": [
            {
                "name": item.name,
                "size": item.size,
                "sha1": item.sha1,
                "url": item.url,
                "required": item.required,
                "sha256": item.sha256,
            }
            for item in payload.files
        ],
    }


def _payload_from(
    value: dict[str, object], *, source_dir: str, from_cache: bool
) -> SourcePayload | None:
    raw = value.get("files")
    if not isinstance(raw, list) or not raw:
        return None
    files: list[UupFile] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            files.append(
                UupFile(
                    name=str(item["name"]),
                    size=int(item["size"]),
                    sha1=str(item["sha1"]).lower(),
                    url=str(item["url"]),
                    required=bool(item.get("required", True)),
                    sha256=str(item["sha256"]) if item.get("sha256") else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            return None
    return SourcePayload(
        build=str(value.get("build") or ""),
        files=tuple(files),
        update_name=str(value.get("updateName") or ""),
        arch=str(value.get("arch") or ""),
        expires_at=_time(value.get("expiresAt")),
        source_dir=source_dir,
        api_version=str(value.get("apiVersion") or ""),
        unknown_fields=_str_tuple(value.get("unknownFields")),
        warnings=_str_tuple(value.get("warnings")),
        from_cache=from_cache,
    )


def _with_cache_meta(payload: SourcePayload, lookup: CacheLookup) -> SourcePayload:
    return SourcePayload(
        build=payload.build,
        files=payload.files,
        update_name=payload.update_name,
        arch=payload.arch,
        expires_at=payload.expires_at,
        source_dir=payload.source_dir,
        api_version=payload.api_version,
        unknown_fields=payload.unknown_fields,
        warnings=payload.warnings,
        from_cache=True,
        cache_age_seconds=lookup.age_seconds,
    )


def _append_warning(payload: SourcePayload, warning: str) -> SourcePayload:
    return SourcePayload(
        build=payload.build,
        files=payload.files,
        update_name=payload.update_name,
        arch=payload.arch,
        expires_at=payload.expires_at,
        source_dir=payload.source_dir,
        api_version=payload.api_version,
        unknown_fields=payload.unknown_fields,
        warnings=(*payload.warnings, warning),
        from_cache=payload.from_cache,
        cache_age_seconds=payload.cache_age_seconds,
    )


def _str_tuple(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _arch(value: object) -> Any:
    from uup2iso.core.models.enums import Architecture

    try:
        return Architecture(str(value))
    except ValueError:
        return Architecture.X64


def _time(value: object) -> Any:
    from datetime import UTC, datetime

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
