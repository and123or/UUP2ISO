"""uupdump 数据源实现（下载 §2.1、§2.2）。

**站点细节不写进业务代码**：这里把 `https://uupdump.net/`（用户可见的站点）
自动映射到 `https://api.uupdump.net/`（真实 API），并允许整体替换为镜像/自建。

礼貌约束（下载 §2.1）：同一源并发 ≤ 1、请求间隔 ≥ 300 ms、带产品标识 UA。
失败切换：当前地址**连续失败 3 次**才切下一个，切换记 `WARN source_failover`。

字段分级（下载 §2.2）：文件名/大小/SHA1/URL 缺失 → `SOURCE_SCHEMA_INVALID`；
可选字段缺失（sha256/expire 等）→ 收进 ``warnings``；站点新增字段 → ``unknown_fields``。
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from uup2iso import __version__
from uup2iso.core.errors import ErrorCode
from uup2iso.core.models.build import BuildInfo
from uup2iso.core.models.enums import Architecture
from uup2iso.core.models.manifest import UupFile
from uup2iso.core.models.source import EditionInfo, SourcePayload
from uup2iso.infrastructure.source.base import SourceError

__all__ = [
    "API_BASE_DEFAULT",
    "DEFAULT_MIN_INTERVAL",
    "SITE_BASE_DEFAULT",
    "UupDumpSource",
]

SITE_BASE_DEFAULT = "https://uupdump.net/"
"""用户可见的站点（配置 `source.baseUrl` 的默认值）。"""

API_BASE_DEFAULT = "https://api.uupdump.net/"
"""真实 API 基址；由 :func:`_api_base` 从站点地址推导。"""

DEFAULT_MIN_INTERVAL = 0.3
"""请求间隔下限（秒），下载 §2.1 的礼貌约束。"""

_FAILOVER_THRESHOLD = 3
"""连续失败多少次切换到备用地址（下载 §2.1）。"""

_ARCH_MAP: dict[str, Architecture] = {
    "amd64": Architecture.X64,
    "x64": Architecture.X64,
    "arm64": Architecture.ARM64,
    "x86": Architecture.X86,
}

_ALLOWED_TOP_FIELDS: frozenset[str] = frozenset(
    {
        "apiVersion",
        "updateName",
        "arch",
        "build",
        "sku",
        "hasUpdates",
        "appxPresent",
        "files",
        "updateInfo",
    }
)
_REQUIRED_FILE_FIELDS: tuple[str, ...] = ("size", "sha1", "url")


class UupDumpSource:
    """uupdump 数据源。

    Args:
        base_url: 站点基址（默认 :data:`SITE_BASE_DEFAULT`）；可换成镜像或自建。
        fallbacks: 备用地址列表，按顺序在主地址失败后启用。
        client: 注入的 httpx 客户端（测试用 ``MockTransport``）。
        min_interval: 请求间隔下限（秒）。
        timeout: 单次请求超时（秒）。
        clock: 可注入时钟（测试免等待）。
        sleep: 可注入等待函数（测试免等待）。
    """

    def __init__(
        self,
        *,
        base_url: str = SITE_BASE_DEFAULT,
        fallbacks: tuple[str, ...] | list[str] = (),
        client: httpx.Client | None = None,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        timeout: float = 30.0,
        clock: Any = None,
        sleep: Any = None,
    ) -> None:
        self._bases: list[str] = [_api_base(base_url), *(_api_base(item) for item in fallbacks)]
        self._base_index = 0
        self._failures = 0
        self._client = client
        self._owns_client = client is None
        self._min_interval = min_interval
        self._timeout = timeout
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._last_request: float | None = None
        self._lock = threading.Lock()
        self.warnings: list[str] = []
        """累计告警（fatal 以外的问题），由调用方写日志。"""

    # -- 生命周期 ---------------------------------------------------------

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> UupDumpSource:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- UupSource --------------------------------------------------------

    def list_builds(self, *, search: str | None = None) -> tuple[BuildInfo, ...]:
        """列出构建（可按关键字过滤）。"""
        params: dict[str, str] = {"sortByDate": "1"}
        if search:
            params["search"] = search
        response = self._get_json("listid.php", params)
        builds = response.get("builds")
        if not isinstance(builds, dict):
            raise SourceError("listid 响应缺少 builds 对象", code=ErrorCode.SOURCE_SCHEMA_INVALID)

        result: list[BuildInfo] = []
        for key, entry in builds.items():
            build = _parse_build(entry)
            if build is None:
                self.warnings.append(f"build_skipped reason=field_missing id={key}")
                continue
            result.append(build)

        if not result:
            raise SourceError("listid 响应里没有可用构建", code=ErrorCode.SOURCE_SCHEMA_INVALID)
        return tuple(result)

    def list_languages(self, build_id: str) -> tuple[str, ...]:
        response = self._get_json("listlangs.php", {"id": build_id})
        langs = response.get("langList")
        if not isinstance(langs, list) or not langs:
            # 实测：服务端累积更新（"Update for Microsoft server operating system"）没有语言列表。
            # 这不是站点坏了，而是该构建本就不是完整 OS 构建；返回空 + 告警，由调用方跳过。
            self.warnings.append(f"no_languages build={build_id}")
            return ()
        return tuple(str(item).lower() for item in langs)

    def list_editions(self, build_id: str, language: str) -> tuple[EditionInfo, ...]:
        response = self._get_json("listeditions.php", {"id": build_id, "lang": language})
        codes = response.get("editionList")
        if not isinstance(codes, list) or not codes:
            self.warnings.append(f"no_editions build={build_id} lang={language}")
            return ()
        fancy = response.get("editionFancyNames")
        names = fancy if isinstance(fancy, dict) else {}
        return tuple(
            EditionInfo(code=str(code), display_name=str(names.get(code, code))) for code in codes
        )

    def get_files(self, build_id: str, *, language: str, edition: str) -> SourcePayload:
        """取文件清单。"""
        response = self._get_json("get.php", {"id": build_id, "lang": language, "edition": edition})
        files_raw = response.get("files")
        if not isinstance(files_raw, dict) or not files_raw:
            raise SourceError(
                f"构建 {build_id} 的文件清单为空", code=ErrorCode.SOURCE_SCHEMA_INVALID
            )

        warnings: list[str] = []
        files: list[UupFile] = []
        expires: list[datetime] = []

        for raw_name, raw in files_raw.items():
            name, normalized = _normalize_name(str(raw_name), build_id)
            if normalized:
                warnings.append(f"file_name_normalized raw={raw_name}")
            files.append(_parse_file(name, raw, warnings))

        for raw in files_raw.values():
            stamp = _safe_int(raw.get("expire")) if isinstance(raw, dict) else 0
            if stamp:
                expires.append(datetime.fromtimestamp(stamp, tz=UTC))

        unknown = tuple(sorted(str(key) for key in response if key not in _ALLOWED_TOP_FIELDS))
        payload = SourcePayload(
            build=str(response.get("build") or ""),
            files=tuple(files),
            update_name=str(response.get("updateName") or ""),
            arch=str(response.get("arch") or ""),
            expires_at=min(expires) if expires else None,
            source_dir=self._bases[self._base_index],
            api_version=str(response.get("apiVersion") or ""),
            unknown_fields=unknown,
            warnings=tuple(warnings),
        )
        if not payload.build:
            raise SourceError("get 响应缺少构建号", code=ErrorCode.SOURCE_SCHEMA_INVALID)
        if payload.url_expired:
            warnings.append("manifest_url_expired")
        self.warnings.extend(warnings)
        return payload

    # -- HTTP -------------------------------------------------------------

    def _get_json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        url = self._bases[self._base_index].rstrip("/") + "/" + endpoint
        with self._lock:
            self._respect_interval()
            try:
                payload = self._request(url, params)
            except SourceError as exc:
                self._failures += 1
                if self._failures >= _FAILOVER_THRESHOLD and self._can_failover():
                    self._switch_base(exc)
                    url = self._bases[self._base_index].rstrip("/") + "/" + endpoint
                    self._respect_interval()
                    payload = self._request(url, params)
                    self._failures = 0
                else:
                    raise
            else:
                self._failures = 0
            finally:
                self._last_request = self._clock()

        response = payload.get("response")
        if not isinstance(response, dict):
            raise SourceError(
                "响应缺少 response 对象（站点结构可能变了）",
                code=ErrorCode.SOURCE_SCHEMA_INVALID,
            )
        return response

    def _request(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        client = self._ensure_client()
        try:
            response = client.get(url, params=params, headers=_headers())
        except httpx.TimeoutException as exc:
            raise SourceError(
                f"数据源超时：{url}（{exc}）", code=ErrorCode.SOURCE_UNAVAILABLE
            ) from exc
        except httpx.HTTPError as exc:
            raise SourceError(
                f"数据源不可达：{url}（{exc}）", code=ErrorCode.SOURCE_UNAVAILABLE
            ) from exc

        if response.status_code == 429:
            raise SourceError("数据源限流（429），请稍后重试", code=ErrorCode.SOURCE_RATE_LIMITED)
        if response.status_code != 200:
            raise SourceError(
                f"数据源返回 {response.status_code}：{url}", code=ErrorCode.SOURCE_UNAVAILABLE
            )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise SourceError(
                f"数据源返回非法 JSON：{url}", code=ErrorCode.SOURCE_SCHEMA_INVALID
            ) from exc
        if not isinstance(payload, dict):
            raise SourceError(
                "数据源返回的 JSON 顶层不是对象", code=ErrorCode.SOURCE_SCHEMA_INVALID
            )
        return payload

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout, follow_redirects=True)
            self._owns_client = True
        return self._client

    def _respect_interval(self) -> None:
        if self._min_interval <= 0 or self._last_request is None:
            return  # 首次请求不需要等待
        elapsed = self._clock() - self._last_request
        if elapsed < self._min_interval:
            self._sleep(self._min_interval - elapsed)

    def _can_failover(self) -> bool:
        return self._base_index + 1 < len(self._bases)

    def _switch_base(self, exc: SourceError) -> None:
        old = self._bases[self._base_index]
        self._base_index += 1
        new = self._bases[self._base_index]
        self.warnings.append(f"source_failover from={old} to={new} reason={exc.code.value}")


# ---------------------------------------------------------------------------
# 解析辅助
# ---------------------------------------------------------------------------


def _headers() -> dict[str, str]:
    return {"User-Agent": f"UUP2ISO/{__version__} (+https://github.com/and123or/UUP2ISO)"}


def _api_base(base_url: str) -> str:
    """把站点地址映射成 API 地址（`uupdump.net` → `api.uupdump.net`）。"""
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if host == "uupdump.net":
        host = "api.uupdump.net"
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunparse(parsed._replace(netloc=netloc, path="", query="", fragment="")) + "/"


def _parse_build(entry: object) -> BuildInfo | None:
    if not isinstance(entry, dict):
        return None
    build_full = str(entry.get("build") or "")
    arch = str(entry.get("arch") or "")
    if not build_full or not arch:
        return None
    architecture = _ARCH_MAP.get(arch.lower())
    if architecture is None:
        return None

    head, _, tail = build_full.partition(".")
    created = _safe_int(entry.get("created"))
    return BuildInfo(
        build=build_full,
        revision=tail,
        arch=architecture,
        languages=(),
        channel=None,
        released_at=datetime.fromtimestamp(created, tz=UTC) if created else None,
        title=str(entry.get("title") or "") or None,
        id=str(entry.get("uuid") or ""),
    )


def _parse_file(name: str, raw: object, warnings: list[str]) -> UupFile:
    if not isinstance(raw, dict):
        raise SourceError(f"文件 {name} 的条目不是对象", code=ErrorCode.SOURCE_SCHEMA_INVALID)

    missing = [field for field in _REQUIRED_FILE_FIELDS if not raw.get(field)]
    if missing:
        raise SourceError(
            f"文件 {name} 缺少必需字段：{', '.join(missing)}",
            code=ErrorCode.SOURCE_SCHEMA_INVALID,
        )

    size = _safe_int(raw.get("size"))
    if size <= 0:
        raise SourceError(
            f"文件 {name} 的大小非法：{raw.get('size')!r}", code=ErrorCode.SOURCE_SCHEMA_INVALID
        )

    sha1 = str(raw.get("sha1") or "").lower()
    if len(sha1) != 40:
        raise SourceError(
            f"文件 {name} 的 SHA1 格式非法：{sha1!r}", code=ErrorCode.SOURCE_SCHEMA_INVALID
        )

    url = str(raw.get("url") or "")
    if not url.lower().startswith("https://"):
        # 站点会给出 http 直链；这里只记录，是否拒绝由清单校验（T3）决定
        warnings.append(f"insecure_url name={name}")

    sha256 = str(raw.get("sha256") or "") or None
    if not sha256:
        warnings.append(f"field_missing field=sha256 name={name}")

    return UupFile(
        name=name,
        size=size,
        sha1=sha1,
        url=url,
        required=True,
        sha256=sha256,
    )


_UUID_PREFIX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\."
)


def _normalize_name(raw_name: str, build_id: str) -> tuple[str, bool]:
    """去掉站点给个别文件名加的 `<uuid>.` 前缀（实测：AggregatedMetadata.cab）。

    实测坑：前缀**不一定等于当前 build 的 uuid**，所以按 UUID 形态匹配，而不是按 build_id 比对。
    """
    _ = build_id  # 保留参数以便将来按构建区分；当前按 UUID 形态判断更稳
    match = _UUID_PREFIX.match(raw_name)
    if match:
        return raw_name[match.end() :], True
    return raw_name, False


def _safe_int(value: object) -> int:
    if value is None:
        return 0
    text = str(value).replace(",", "").strip()
    return int(text) if text.isdigit() else 0
