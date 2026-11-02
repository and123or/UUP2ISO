"""清单生成与五项自校验（下载 §3.1、§3.2、§3.3）。

清单是**下载的唯一输入**，所以生成后立刻自校验，任何一条不过就 `MANIFEST_INVALID`，
并把原始响应（脱敏后）落到 `debug\\source-response.json` 供排查——**绝不带着坏清单去下载**。

五项校验（顺序即规范顺序）：

1. `files` 非空且每条含 `name/size/sha1/url`；
2. `name` 唯一；
3. `totalBytes` == 各文件 `size` 之和；
4. `sha1` 40 位十六进制；`url` 为 **https** 且 host 在白名单内
   （官方域名 ∪ 当前数据源 host——否则用户配的镜像会被自己的校验判死）；
5. 必需文件 ≥ 1 个。

**实测决策（0.1.26 修正）**：uupdump 给的是 `http://…microsoft.com/…` 直链，而该 CDN
**只接受 http**（https 会 TLS 证书不匹配 / 返回 22）→ 因此**保留原始 scheme**，并记
`url_kept_http count=<n>`；校验层放行白名单域名下的 http（非白名单仍拒绝）。
强烈建议保留 https 优先：源给 https 就用 https，只在源本身给 http 时才用 http。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from uup2iso.core.errors import ErrorCode, Uup2IsoError
from uup2iso.core.models.manifest import SCHEMA_VERSION, Manifest, ManifestSource, UupFile
from uup2iso.core.models.source import SourcePayload

__all__ = [
    "OFFICIAL_HOST_SUFFIXES",
    "PARSER_VERSION",
    "ManifestError",
    "ManifestRequest",
    "build_manifest",
    "manifest_to_json",
    "redact_payload",
    "validate_manifest",
    "write_manifest",
]

PARSER_VERSION = "1.0.0"
"""解析器版本（下载 §3.1），随清单落盘便于定位「同构建不同时间解析不同」。"""

OFFICIAL_HOST_SUFFIXES: tuple[str, ...] = (
    "uupdump.net",
    "microsoft.com",
    "windowsupdate.com",
)
"""内置官方下载域（下载 §3.2-4）；按后缀匹配，`*.microsoft.com` 均放行。"""

_SHA1 = re.compile(r"^[0-9a-f]{40}$")


class ManifestError(Uup2IsoError):
    """清单不合法（`MANIFEST_INVALID`，退出码 4）。"""

    default_code = ErrorCode.MANIFEST_INVALID


@dataclass(frozen=True, slots=True)
class ManifestRequest:
    """生成清单时来自任务配置的选择（用于落盘与一致性校验）。

    Attributes:
        build: 完整构建号（含修订），如 ``26100.9725``。
        language: 语言标签（小写）。
        edition: edition 标识（小写）。
        base_url: 生效的数据源基址（写入 `source.baseUrl`）。
        fetched_at: 拉取时间（带时区）。
        from_cache: 是否来自过期缓存。
        extra_hosts: 额外放行的 host（如备用镜像）。
    """

    build: str
    language: str
    edition: str
    base_url: str
    fetched_at: datetime
    revision: str = ""
    from_cache: bool = False
    extra_hosts: tuple[str, ...] = field(default_factory=tuple)

    @property
    def revision_or_derived(self) -> str:
        if self.revision:
            return self.revision
        return self.build.partition(".")[2]

    def allowed_hosts(self) -> tuple[str, ...]:
        host = urlsplit(self.base_url).hostname or ""
        return tuple(item for item in (host, *self.extra_hosts) if item)


def build_manifest(
    payload: SourcePayload,
    request: ManifestRequest,
    *,
    debug_dir: Path | None = None,
) -> tuple[Manifest, tuple[str, ...]]:
    """把数据源响应变成清单并立即自校验。

    Args:
        payload: `UupSource.get_files` 的结果。
        request: 任务配置里的选择（build/语言/edition/源地址）。
        debug_dir: 校验失败时落 `source-response.json` 的目录。

    Returns:
        ``(manifest, warnings)``。

    Raises:
        ManifestError: 五项校验任一不过；失败前会把脱敏原始响应写入 ``debug_dir``。
    """
    warnings: list[str] = []
    kept_http = 0
    files: list[UupFile] = []

    for item in payload.files:
        url, changed = _keep_scheme(item.url)
        if changed:
            kept_http += 1
        files.append(
            UupFile(
                name=item.name,
                size=item.size,
                sha1=item.sha1.lower(),
                url=url,
                required=item.required,
                category=item.category,
                sha256=item.sha256,
            )
        )
    if kept_http:
        warnings.append(f"url_kept_http count={kept_http}")

    if payload.build and request.build and payload.build != request.build:
        warnings.append(f"manifest_mismatch config={request.build} source={payload.build}")

    manifest = Manifest(
        build=payload.build or request.build,
        revision=request.revision_or_derived,
        language=request.language.lower(),
        edition=request.edition.lower(),
        files=tuple(files),
        source=ManifestSource(
            base_url=request.base_url,
            fetched_at=request.fetched_at,
            from_cache=request.from_cache,
        ),
        schema_version=SCHEMA_VERSION,
        parser_version=PARSER_VERSION,
        total_bytes=sum(item.size for item in files),
        unknown_fields=payload.unknown_fields,
    )

    try:
        validate_manifest(manifest, request=request)
    except ManifestError:
        if debug_dir is not None:
            _dump_debug(debug_dir, payload)
        raise
    return manifest, tuple(warnings)


def validate_manifest(manifest: Manifest, *, request: ManifestRequest | None = None) -> None:
    """五项自校验（下载 §3.2）。任一不过即抛 :class:`ManifestError`。

    Raises:
        ManifestError: 校验失败；消息里带**具体是哪一项、哪个文件**。
    """
    if not manifest.files:
        raise ManifestError("清单没有任何文件（下载 §3.2-1）")

    for item in manifest.files:
        missing = [
            field_name
            for field_name, value in (
                ("name", item.name),
                ("size", item.size),
                ("sha1", item.sha1),
                ("url", item.url),
            )
            if not value
        ]
        if missing:
            raise ManifestError(
                f"文件 {item.name or '<unnamed>'} 缺少字段：{', '.join(missing)}（下载 §3.2-1）"
            )

    if manifest.has_duplicate_names:
        duplicates = _duplicates([item.name for item in manifest.files])
        raise ManifestError(f"文件名重复：{', '.join(sorted(duplicates))}（下载 §3.2-2）")

    if not manifest.is_total_consistent:
        raise ManifestError(
            f"totalBytes={manifest.total_bytes} 与文件大小之和 "
            f"{manifest.computed_total_bytes} 不一致（下载 §3.2-3）"
        )

    extra_hosts = request.allowed_hosts() if request is not None else ()
    for item in manifest.files:
        if not _SHA1.match(item.sha1):
            raise ManifestError(f"文件 {item.name} 的 SHA1 非法：{item.sha1!r}（下载 §3.2-4）")
        parsed = urlsplit(item.url)
        if parsed.scheme not in ("https", "http"):
            raise ManifestError(f"文件 {item.name} 的 URL 协议非法：{parsed.scheme}（下载 §3.2-4）")
        host = (parsed.hostname or "").lower()
        if not _host_allowed(host, extra_hosts):
            raise ManifestError(f"文件 {item.name} 的下载域名不在白名单：{host}（下载 §3.2-4）")

    if not manifest.required_files:
        raise ManifestError("清单里没有任何必需文件（下载 §3.2-5）")


def manifest_to_json(manifest: Manifest) -> dict[str, object]:
    """按下载 §3.1 的结构序列化。"""
    return {
        "schemaVersion": manifest.schema_version,
        "parserVersion": manifest.parser_version,
        "source": {
            "baseUrl": manifest.source.base_url,
            "fetchedAt": manifest.source.fetched_at.isoformat(),
            "fromCache": manifest.source.from_cache,
        },
        "build": manifest.build,
        "revision": manifest.revision,
        "language": manifest.language,
        "edition": manifest.edition,
        "totalBytes": manifest.total_bytes,
        "files": [
            {
                "name": item.name,
                "size": item.size,
                "sha1": item.sha1,
                "url": item.url,
                "required": item.required,
                **({"category": item.category} if item.category else {}),
                **({"sha256": item.sha256} if item.sha256 else {}),
            }
            for item in manifest.files
        ],
        "unknownFields": list(manifest.unknown_fields),
    }


def write_manifest(path: Path, manifest: Manifest) -> Path:
    """原子写清单（`tmp` → `replace`），返回落盘路径。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(manifest_to_json(manifest), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)
    return path


def redact_payload(payload: SourcePayload) -> dict[str, object]:
    """脱敏后的原始响应（去掉 URL 的签名 query，只留 host + path）。"""
    return {
        "build": payload.build,
        "updateName": payload.update_name,
        "arch": payload.arch,
        "apiVersion": payload.api_version,
        "sourceDir": payload.source_dir,
        "expiresAt": payload.expires_at.isoformat() if payload.expires_at else None,
        "warnings": list(payload.warnings),
        "unknownFields": list(payload.unknown_fields),
        "files": [
            {
                "name": item.name,
                "size": item.size,
                "sha1": item.sha1,
                "url": _strip_query(item.url),
                "required": item.required,
            }
            for item in payload.files
        ],
    }


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _keep_scheme(url: str) -> tuple[str, bool]:
    """保留源给的 scheme；返回 ``(url, 是否 http)``。

    实测：微软 CDN 的签名只对 http 有效，强升 https 会 TLS 证书不匹配 →
    这里不做升级，交给下载层用源给的原样地址。
    """
    parsed = urlsplit(url)
    return url, parsed.scheme == "http"


def _host_allowed(host: str, extra_hosts: tuple[str, ...]) -> bool:
    if not host:
        return False
    if host in extra_hosts:
        return True
    return any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_HOST_SUFFIXES)


def _strip_query(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(parsed._replace(query="", fragment=""))


def _duplicates(names: list[str]) -> set[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for name in names:
        if name in seen:
            repeated.add(name)
        seen.add(name)
    return repeated


def _dump_debug(debug_dir: Path, payload: SourcePayload) -> None:
    """校验失败时保留脱敏原始响应（下载 §3.2），用于事后排查。"""
    target = debug_dir / "source-response.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(redact_payload(payload), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
