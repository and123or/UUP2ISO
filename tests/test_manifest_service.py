"""清单生成与五项自校验门禁（下载 §3.1、§3.2、§3.3）。

夹具用 WP2-T1 抓到的真实响应（97 个文件里取 6 个），保证「真实形状」下还能解析。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from uup2iso.application.fetch import (
    ManifestError,
    ManifestRequest,
    build_manifest,
    manifest_to_json,
    redact_payload,
    validate_manifest,
    write_manifest,
)
from uup2iso.core.models.manifest import Manifest, ManifestSource, UupFile
from uup2iso.core.models.source import SourcePayload

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
URL = "http://tlu.dl.delivery.mp.microsoft.com/filestreamingservice/files/abc?P1=1&P2=404"
HTTPS_URL = "https://tlu.dl.delivery.mp.microsoft.com/filestreamingservice/files/abc"


def make_request(**overrides: object) -> ManifestRequest:
    base: dict[str, object] = {
        "build": "26100.9725",
        "language": "zh-cn",
        "edition": "professional",
        "base_url": "https://uupdump.net/",
        "fetched_at": NOW,
    }
    base.update(overrides)
    return ManifestRequest(**base)  # type: ignore[arg-type]


def make_file(name: str = "DesktopDeployment.cab", **overrides: object) -> UupFile:
    base: dict[str, object] = {
        "name": name,
        "size": 1024,
        "sha1": "a" * 40,
        "url": HTTPS_URL,
        "required": True,
    }
    base.update(overrides)
    return UupFile(**base)  # type: ignore[arg-type]


def make_manifest(**overrides: object) -> Manifest:
    files = overrides.pop("files", (make_file(),))
    base: dict[str, object] = {
        "build": "26100.9725",
        "revision": "9725",
        "language": "zh-cn",
        "edition": "professional",
        "files": files,
        "source": ManifestSource(base_url="https://uupdump.net/", fetched_at=NOW),
        "total_bytes": sum(item.size for item in files),  # type: ignore[union-attr]
    }
    base.update(overrides)
    return Manifest(**base)  # type: ignore[arg-type]


# --- 生成 -------------------------------------------------------------------


def test_build_keeps_original_http_scheme() -> None:
    payload = SourcePayload(
        build="26100.9725",
        files=(
            make_file("AggregatedMetadata.cab", url=URL, size=2830538),
            make_file("DesktopDeployment.cab", url=URL, size=6798426),
        ),
        arch="amd64",
        api_version="1.0",
        unknown_fields=("brandNew",),
    )

    manifest, warnings = build_manifest(payload, make_request())

    # 实测：该 CDN 只认 http，强升 https 会 TLS 失败 → 保留原 scheme 并告警
    assert all(item.url.startswith("http://") for item in manifest.files)
    assert any("url_kept_http count=2" in item for item in warnings)
    assert manifest.total_bytes == 2830538 + 6798426
    assert manifest.build == "26100.9725"
    assert manifest.revision == "9725"
    assert manifest.unknown_fields == ("brandNew",)


def test_build_flags_config_mismatch() -> None:
    payload = SourcePayload(build="26100.994", files=(make_file(),))

    _manifest, warnings = build_manifest(payload, make_request(build="26100.9725"))

    assert any("manifest_mismatch" in item for item in warnings)


def test_https_urls_are_not_flagged_as_http() -> None:
    payload = SourcePayload(build="26100.9725", files=(make_file(),))

    _manifest, warnings = build_manifest(payload, make_request())

    assert not any("url_kept_http" in item for item in warnings)


# --- 五项自校验 -------------------------------------------------------------


def test_validation_passes_for_valid_manifest() -> None:
    validate_manifest(make_manifest(), request=make_request())


def test_empty_files_rejected() -> None:
    with pytest.raises(ManifestError) as caught:
        validate_manifest(make_manifest(files=()))
    assert "没有任何文件" in caught.value.message


def test_missing_field_rejected() -> None:
    manifest = make_manifest(files=(make_file(name=""),))
    with pytest.raises(ManifestError) as caught:
        validate_manifest(manifest)
    assert "缺少字段" in caught.value.message


def test_duplicate_names_rejected() -> None:
    files = (make_file("same.cab"), make_file("same.cab"))
    with pytest.raises(ManifestError) as caught:
        validate_manifest(make_manifest(files=files))
    assert "文件名重复" in caught.value.message
    assert "same.cab" in caught.value.message


def test_total_bytes_mismatch_rejected() -> None:
    manifest = make_manifest(total_bytes=999)
    with pytest.raises(ManifestError) as caught:
        validate_manifest(manifest)
    assert "不一致" in caught.value.message


def test_bad_sha1_rejected() -> None:
    manifest = make_manifest(files=(make_file(sha1="xyz"),))
    with pytest.raises(ManifestError) as caught:
        validate_manifest(manifest)
    assert "SHA1 非法" in caught.value.message


def test_http_url_allowed_for_whitelisted_host() -> None:
    """白名单域名的 http 放行（微软 CDN 实测只认 http）。"""
    manifest = make_manifest(files=(make_file(url=URL),))
    validate_manifest(manifest, request=make_request())


def test_http_url_rejected_for_untrusted_host() -> None:
    manifest = make_manifest(files=(make_file(url="http://evil.example.com/a.cab"),))
    with pytest.raises(ManifestError) as caught:
        validate_manifest(manifest, request=make_request())
    assert "白名单" in caught.value.message


def test_bad_scheme_rejected() -> None:
    manifest = make_manifest(files=(make_file(url="ftp://tlu.dl.delivery.mp.microsoft.com/a.cab"),))
    with pytest.raises(ManifestError) as caught:
        validate_manifest(manifest, request=make_request())
    assert "协议非法" in caught.value.message


def test_untrusted_host_rejected() -> None:
    manifest = make_manifest(files=(make_file(url="https://evil.example.com/a.cab"),))
    with pytest.raises(ManifestError) as caught:
        validate_manifest(manifest, request=make_request())
    assert "白名单" in caught.value.message


def test_mirror_host_is_allowed_when_configured() -> None:
    """用户配的镜像站必须放行，否则会被自己的校验判死（下载 §3.2-4）。"""
    manifest = make_manifest(
        files=(make_file(url="https://mirror.example.com/uup/a.cab"),),
        source=ManifestSource(base_url="https://mirror.example.com/", fetched_at=NOW),
    )
    validate_manifest(manifest, request=make_request(base_url="https://mirror.example.com/"))


def test_extra_hosts_are_allowed() -> None:
    manifest = make_manifest(files=(make_file(url="https://cdn.example.org/a.cab"),))
    validate_manifest(manifest, request=make_request(extra_hosts=("cdn.example.org",)))


def test_official_suffixes_allowed() -> None:
    for url in (
        "https://api.uupdump.net/a.cab",
        "https://tlu.dl.delivery.mp.microsoft.com/a.cab",
        "https://download.windowsupdate.com/a.cab",
    ):
        validate_manifest(make_manifest(files=(make_file(url=url),)), request=make_request())


def test_no_required_files_rejected() -> None:
    manifest = make_manifest(files=(make_file(required=False),))
    with pytest.raises(ManifestError) as caught:
        validate_manifest(manifest)
    assert "必需文件" in caught.value.message


# --- 落盘与脱敏 -------------------------------------------------------------


def test_manifest_json_matches_spec_shape() -> None:
    manifest, _warnings = build_manifest(
        SourcePayload(build="26100.9725", files=(make_file(),), unknown_fields=("x",)),
        make_request(),
    )
    payload = manifest_to_json(manifest)

    assert payload["schemaVersion"] == "1.0"
    assert payload["parserVersion"]
    assert payload["source"] == {
        "baseUrl": "https://uupdump.net/",
        "fetchedAt": NOW.isoformat(),
        "fromCache": False,
    }
    assert payload["build"] == "26100.9725"
    assert payload["revision"] == "9725"
    assert payload["totalBytes"] == payload["files"][0]["size"]  # type: ignore[index]
    assert payload["unknownFields"] == ["x"]
    assert set(payload["files"][0]) >= {"name", "size", "sha1", "url", "required"}  # type: ignore[index]


def test_write_manifest_is_atomic(tmp_path: Path) -> None:
    manifest = make_manifest()
    path = write_manifest(tmp_path / "work" / "t1" / "manifest.json", manifest)

    assert path.is_file()
    assert not list(path.parent.glob("*.tmp"))
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["files"][0]["name"] == "DesktopDeployment.cab"


def test_debug_dump_on_validation_failure(tmp_path: Path) -> None:
    """校验失败要留下脱敏原始响应，且不能留签名 query（下载 §3.2）。"""
    payload = SourcePayload(
        build="26100.9725",
        files=(make_file("bad.cab", url="https://evil.example.com/a.cab?token=secret"),),
    )
    debug = tmp_path / "debug"

    with pytest.raises(ManifestError):
        build_manifest(payload, make_request(), debug_dir=debug)

    dump = debug / "source-response.json"
    assert dump.is_file()
    text = dump.read_text(encoding="utf-8")
    assert "token=secret" not in text
    assert "evil.example.com/a.cab" in text


def test_redact_strips_query_and_keeps_host() -> None:
    payload = SourcePayload(build="26100.9725", files=(make_file(url=URL),))
    redacted = redact_payload(payload)
    files = redacted["files"]
    assert isinstance(files, list)
    assert files[0]["url"] == HTTPS_URL.replace("https", "http")
    assert "P1=" not in files[0]["url"]
