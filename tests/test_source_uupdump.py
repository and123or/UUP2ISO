"""uupdump 数据源门禁（下载 §2.1、§2.2、§2.4）。

夹具来自真实响应（2026-11-02 抓取），断网也能跑；真实联调用 `scripts/smoke_source.py`。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from uup2iso.core.errors import ErrorCode
from uup2iso.core.models.enums import Architecture
from uup2iso.infrastructure.source import SourceError, UupDumpSource
from uup2iso.infrastructure.source.uupdump import _api_base

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "source"
BUILD_ID = "4ef76f77-dd31-49a8-9bb5-6de3a2115eb3"


def fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_source(
    handler: object,
    *,
    bases: tuple[str, ...] = (),
    clock: object | None = None,
    sleep: object | None = None,
) -> UupDumpSource:
    client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return UupDumpSource(
        client=client,
        fallbacks=bases,
        min_interval=0,
        clock=clock,
        sleep=sleep,
    )


def ok_handler(name_map: dict[str, str] | None = None) -> object:
    mapping = name_map or {
        "listid.php": "listid.json",
        "listlangs.php": "langs.json",
        "listeditions.php": "eds.json",
        "get.php": "get.json",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=fixture(mapping[endpoint]))

    return handler


# --- 构建列表 ---------------------------------------------------------------


def test_list_builds_parses_fixture() -> None:
    source = make_source(ok_handler())
    builds = source.list_builds(search="26100")

    assert len(builds) == 6
    first = builds[0]
    assert first.build.count(".") == 1
    assert first.revision == first.build.split(".")[1]
    assert first.arch in (Architecture.X64, Architecture.ARM64)
    assert first.id, "必须带数据源内部 id（换取详情用）"
    assert first.languages == (), "语言要按需单独查，不在列表里猜"


def test_list_builds_sends_search_and_header() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["search"] = request.url.params.get("search")
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json=fixture("listid.json"))

    make_source(handler).list_builds(search="24H2")

    assert seen["search"] == "24H2"
    assert str(seen["ua"]).startswith("UUP2ISO/"), "请求头要带产品标识（下载 §2.1）"


def test_arch_is_mapped_to_domain_enum() -> None:
    builds = make_source(ok_handler()).list_builds()
    archs = {item.arch for item in builds}
    assert Architecture.X64 in archs
    assert all(item.arch in tuple(Architecture) for item in builds)


def test_bad_build_entries_are_skipped_with_warning() -> None:
    payload = fixture("listid.json")
    payload["response"]["builds"]["broken"] = {"title": "no fields"}  # type: ignore[index]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    source = make_source(handler)
    builds = source.list_builds()
    assert len(builds) == 6, "坏条目应跳过而不是整份失败"
    assert any("build_skipped" in warning for warning in source.warnings)


def test_all_bad_builds_raise_schema_error() -> None:
    payload = {"response": {"builds": {"x": {"title": "only title"}}}}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(SourceError) as caught:
        make_source(handler).list_builds()
    assert caught.value.code is ErrorCode.SOURCE_SCHEMA_INVALID


# --- 语言与 edition ---------------------------------------------------------


def test_list_languages_lowercases() -> None:
    langs = make_source(ok_handler()).list_languages(BUILD_ID)
    assert "zh-cn" in langs
    assert all(item == item.lower() for item in langs)
    assert len(langs) > 10


def test_empty_language_list_is_warning_not_error() -> None:
    """实测：服务端累积更新没有语言列表，不是站点坏了。"""
    payload = {"response": {"langList": []}}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    source = make_source(handler)
    assert source.list_languages(BUILD_ID) == ()
    assert any("no_languages" in warning for warning in source.warnings)


def test_list_editions_with_display_names() -> None:
    editions = make_source(ok_handler()).list_editions(BUILD_ID, "zh-cn")

    codes = [item.code for item in editions]
    assert "PROFESSIONAL" in codes
    professional = next(item for item in editions if item.code == "PROFESSIONAL")
    assert professional.display_name == "Windows Pro"


def test_empty_editions_is_warning_not_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": {"editionList": []}})

    source = make_source(handler)
    assert source.list_editions(BUILD_ID, "zh-cn") == ()
    assert any("no_editions" in warning for warning in source.warnings)


# --- 文件清单 ---------------------------------------------------------------


def test_get_files_parses_and_normalizes_names() -> None:
    payload = make_source(ok_handler()).get_files(
        BUILD_ID, language="zh-cn", edition="professional"
    )

    names = [item.name for item in payload.files]
    assert "AggregatedMetadata.cab" in names, "uuid 前缀必须去掉"
    assert not any("-4bff-" in name for name in names)
    assert payload.build
    assert payload.total_bytes > 0
    assert payload.expires_at is not None, "站点给了 expire，必须解析出来"
    assert all(len(item.sha1) == 40 for item in payload.files)
    assert any("file_name_normalized" in warning for warning in payload.warnings)


def test_get_files_flags_http_urls() -> None:
    """实测：uupdump 给的是 http 直链；先记录，是否升级由清单校验（T3）决定。"""
    payload = make_source(ok_handler()).get_files(
        BUILD_ID, language="zh-cn", edition="professional"
    )
    assert any("insecure_url" in warning for warning in payload.warnings)


@pytest.mark.parametrize("missing", ["size", "sha1", "url"])
def test_missing_required_file_field_is_fatal(missing: str) -> None:
    payload = fixture("get.json")
    first_key = next(iter(payload["response"]["files"]))  # type: ignore[index]
    payload["response"]["files"][first_key].pop(missing)  # type: ignore[index]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(SourceError) as caught:
        make_source(handler).get_files(BUILD_ID, language="zh-cn", edition="professional")
    assert caught.value.code is ErrorCode.SOURCE_SCHEMA_INVALID
    assert missing in caught.value.message


def test_bad_sha1_is_rejected() -> None:
    payload = fixture("get.json")
    first_key = next(iter(payload["response"]["files"]))  # type: ignore[index]
    payload["response"]["files"][first_key]["sha1"] = "abc"  # type: ignore[index]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(SourceError) as caught:
        make_source(handler).get_files(BUILD_ID, language="zh-cn", edition="professional")
    assert "SHA1" in caught.value.message


def test_unknown_fields_are_collected() -> None:
    payload = fixture("get.json")
    payload["response"]["brandNewField"] = {"x": 1}  # type: ignore[index]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    result = make_source(handler).get_files(BUILD_ID, language="zh-cn", edition="professional")
    assert "brandNewField" in result.unknown_fields


# --- HTTP 错误与回退 --------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [(500, ErrorCode.SOURCE_UNAVAILABLE), (429, ErrorCode.SOURCE_RATE_LIMITED)],
)
def test_http_errors_are_classified(status: int, code: ErrorCode) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(SourceError) as caught:
        make_source(handler).list_builds()
    assert caught.value.code is code


def test_broken_json_is_schema_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(SourceError) as caught:
        make_source(handler).list_builds()
    assert caught.value.code is ErrorCode.SOURCE_SCHEMA_INVALID


def test_failover_after_three_failures() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        calls.append(host)
        if host == "api.uupdump.net":
            return httpx.Response(500)
        return httpx.Response(200, json=fixture("listid.json"))

    source = make_source(handler, bases=("https://mirror.example.com/",))
    with pytest.raises(SourceError):
        source.list_builds()  # 第 1 次失败
    with pytest.raises(SourceError):
        source.list_builds()  # 第 2 次失败
    builds = source.list_builds()  # 第 3 次失败 → 切换备用并重试成功

    assert builds, "备用地址应给出结果"
    assert calls.count("api.uupdump.net") == 3, "连续失败 3 次才切换（下载 §2.1）"
    assert any("source_failover" in warning for warning in source.warnings)


def test_no_failover_when_no_backup() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(SourceError):
        make_source(handler).list_builds()


def test_politeness_interval_is_respected() -> None:
    """请求间隔 ≥ 300 ms（下载 §2.1）：第二次调用前必须 sleep。"""
    slept: list[float] = []
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    source = UupDumpSource(
        client=httpx.Client(transport=httpx.MockTransport(ok_handler())),
        min_interval=0.3,
        clock=clock,
        sleep=sleep,
    )
    source.list_builds()
    now[0] += 0.05  # 只过了 50ms 就发第二次
    source.list_builds()

    assert slept == [pytest.approx(0.25, abs=0.01)], "首次不等，第二次补足到 300ms"


def test_api_base_mapping() -> None:
    assert _api_base("https://uupdump.net/") == "https://api.uupdump.net/"
    assert _api_base("https://api.uupdump.net") == "https://api.uupdump.net/"
    assert _api_base("http://10.0.0.5:8080/") == "http://10.0.0.5:8080/"
