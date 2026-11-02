"""抓取阶段（WP2）：清单生成与下载。

* :mod:`.manifest_service` —— 把数据源响应变成 `manifest.json` 并做五项自校验（WP2-T3）；
* 下载执行（aria2c）与续传校验属 WP2-T6 / T7，尚未实现。
"""

from __future__ import annotations

from uup2iso.application.fetch.manifest_service import (
    OFFICIAL_HOST_SUFFIXES,
    PARSER_VERSION,
    ManifestError,
    ManifestRequest,
    build_manifest,
    load_manifest,
    manifest_to_json,
    redact_payload,
    validate_manifest,
    write_manifest,
)

__all__ = [
    "OFFICIAL_HOST_SUFFIXES",
    "PARSER_VERSION",
    "ManifestError",
    "ManifestRequest",
    "build_manifest",
    "load_manifest",
    "manifest_to_json",
    "redact_payload",
    "validate_manifest",
    "write_manifest",
]
