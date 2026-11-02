"""版本目录门禁（P0-0）。

两条硬约束：
1. **代码里不出现具体主版本号**（最后一条测试用 grep 守着）；
2. GitHub 取不到时能降级到随包快照，并明确标记 `stale`。
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from uup2iso.application.input import (
    build_from_directory,
    language_from_names,
    resolve_input_version,
)
from uup2iso.infrastructure.version import (
    CACHE_TTL_SECONDS,
    GitHubSource,
    VersionCatalog,
    parse_releases,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def write_snapshot(tmp_path: Path, releases: list[dict[str, object]]) -> Path:
    path = tmp_path / "windows_releases.json"
    path.write_text(
        json.dumps({"schemaVersion": "1.0", "releases": releases}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def make_catalog(
    tmp_path: Path,
    *,
    releases: list[dict[str, object]] | None = None,
    sources: tuple[GitHubSource, ...] = (),
    handler: object | None = None,
    clock: Clock | None = None,
) -> VersionCatalog:
    snapshot = write_snapshot(
        tmp_path,
        releases
        if releases is not None
        else [{"family": "25H2", "major": 26200}, {"family": "26H2", "major": 26300}],
    )
    client = httpx.Client(transport=httpx.MockTransport(handler)) if handler else None
    return VersionCatalog(
        snapshot_path=snapshot,
        cache_dir=tmp_path / "cache",
        sources=sources,
        client=client,
        clock=clock or Clock(),
    )


# --- 文本解析 ---------------------------------------------------------------


def test_parse_pairs_on_same_line() -> None:
    text = "Windows 11, version 26H2 (OS build 26300.9818)\n"
    releases = parse_releases(text)

    assert [(item.family, item.major) for item in releases] == [("26H2", 26300)]


def test_parse_ignores_family_without_build_on_line() -> None:
    text = "Available: 26H2\n\nSomewhere else 26300\n"
    assert parse_releases(text) == ()


def test_parse_majority_wins_on_conflict() -> None:
    text = "\n".join(
        [
            "26H2 build 26300",
            "26H2 build 26300",
            "26H2 build 26400",
        ]
    )
    releases = parse_releases(text)
    assert [(item.family, item.major) for item in releases] == [("26H2", 26300)]


def test_parse_ignores_noise_numbers() -> None:
    text = "26H2 released in 2026 with 12345 files and 99999 blocks\n"
    assert parse_releases(text) == ()


def test_parse_multiple_families() -> None:
    text = "24H2 build 26100\n25H2 build 26200\n26H2 build 26300\n"
    releases = {item.family: item.major for item in parse_releases(text)}

    assert releases == {"24H2": 26100, "25H2": 26200, "26H2": 26300}


# --- 快照与查询 -------------------------------------------------------------


def test_snapshot_resolution(tmp_path: Path) -> None:
    catalog = make_catalog(tmp_path)
    resolution = catalog.resolve("26H2")

    assert resolution.major == 26300
    assert resolution.family == "26H2"
    assert resolution.stale is True, "只用快照时要标 stale"
    assert catalog.family_of(26300) == "26H2"


def test_unknown_family_is_reported_not_guessed(tmp_path: Path) -> None:
    catalog = make_catalog(tmp_path)
    resolution = catalog.resolve("27H2")

    assert resolution.known is False
    assert any("version_family_unknown" in item for item in catalog.warnings)


def test_resolve_build_splits_revision(tmp_path: Path) -> None:
    catalog = make_catalog(tmp_path)
    resolution = catalog.resolve_build("26300.9818")

    assert (resolution.major, resolution.revision) == (26300, "9818")
    assert resolution.family == "26H2"


def test_unlisted_major_is_flagged(tmp_path: Path) -> None:
    catalog = make_catalog(tmp_path)
    resolution = catalog.resolve_build("27000.1")

    assert resolution.major == 27000
    assert resolution.family == ""
    assert any("version_major_unlisted" in item for item in catalog.warnings)


def test_missing_snapshot_is_harmless(tmp_path: Path) -> None:
    catalog = VersionCatalog(snapshot_path=tmp_path / "nope.json", clock=Clock())
    assert catalog.resolve("26H2").known is False
    assert any("version_snapshot_missing" in item for item in catalog.warnings)


def test_corrupt_snapshot_is_harmless(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")
    catalog = VersionCatalog(snapshot_path=path, clock=Clock())

    assert catalog.resolve("26H2").known is False
    assert any("version_snapshot_corrupt" in item for item in catalog.warnings)


# --- GitHub 源 --------------------------------------------------------------


def github_handler(text: str, *, status: int = 200, sha: str = "abc123def456") -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/contents/" in str(request.url)
        if status != 200:
            return httpx.Response(status, json={"message": "Not Found"})
        return httpx.Response(
            200,
            json={
                "name": "releases.md",
                "encoding": "base64",
                "sha": sha,
                "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            },
        )

    return handler


def test_github_source_parses_and_records_origin(tmp_path: Path) -> None:
    handler = github_handler("Windows 11 26H2 (build 26300.9818)\n")
    catalog = make_catalog(
        tmp_path,
        sources=(GitHubSource(repo="acme/windows-versions", path="releases.md"),),
        handler=handler,
    )

    releases = catalog.load()

    assert {item.family: item.major for item in releases}["26H2"] == 26300
    assert catalog.stale is False
    assert any(
        "github:acme/windows-versions/releases.md@abc123def456" in item for item in catalog.warnings
    )


def test_github_cache_avoids_second_request(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return github_handler("26H2 build 26300\n")(request)

    clock = Clock()
    catalog = make_catalog(
        tmp_path,
        sources=(GitHubSource(repo="acme/versions", path="releases.md"),),
        handler=handler,
        clock=clock,
    )
    catalog.load()
    catalog.load()

    assert len(calls) == 1, "缓存有效期内不应重复请求"
    clock.advance(CACHE_TTL_SECONDS + 1)
    catalog.load()
    assert len(calls) == 2, "缓存过期后应重新请求"


def test_github_failure_falls_back_to_snapshot(tmp_path: Path) -> None:
    catalog = make_catalog(
        tmp_path,
        sources=(GitHubSource(repo="acme/gone", path="releases.md"),),
        handler=github_handler("", status=404),
    )

    resolution = catalog.resolve("26H2")

    assert resolution.major == 26300, "取不到 GitHub 也要能用快照"
    assert resolution.stale is True
    assert any("version_source_unavailable" in item for item in catalog.warnings)


def test_github_unparsable_falls_back(tmp_path: Path) -> None:
    catalog = make_catalog(
        tmp_path,
        sources=(GitHubSource(repo="acme/odd", path="readme.md"),),
        handler=github_handler("nothing useful here\n"),
    )

    assert catalog.resolve("26H2").major == 26300
    assert any("version_source_unparsed" in item for item in catalog.warnings)


# --- 路径推断 ---------------------------------------------------------------


def test_build_from_directory_name() -> None:
    assert build_from_directory("UUPs26300.9818") == "26300.9818"
    assert build_from_directory("UUPs 26100.1742 x64") == "26100.1742"
    assert build_from_directory("no-version-here") == ""


def test_language_from_names() -> None:
    names = ["Microsoft-Windows-Foundation-Package.ESD", "professional_zh-cn.esd"]
    assert language_from_names(names) == "zh-cn"
    assert language_from_names(["nothing.esd"]) == ""


def test_resolve_input_version_end_to_end(tmp_path: Path) -> None:
    folder = tmp_path / "UUPs26300.9818"
    folder.mkdir()
    (folder / "professional_zh-cn.esd").write_bytes(b"ESD")
    catalog = make_catalog(tmp_path)

    resolution = resolve_input_version(
        path=folder, catalog=catalog, names=[item.name for item in folder.iterdir()]
    )

    assert (resolution.family, resolution.major, resolution.build) == ("26H2", 26300, "26300.9818")
    assert resolution.revision == "9818"
    assert resolution.language == "zh-cn"
    assert resolution.source.startswith("directory-name")


def test_resolve_input_version_without_build_warns(tmp_path: Path) -> None:
    folder = tmp_path / "some-folder"
    folder.mkdir()
    catalog = make_catalog(tmp_path)

    resolution = resolve_input_version(path=folder, catalog=catalog)

    assert resolution.known is False
    assert any("build_unresolved" in item for item in resolution.warnings)
    assert any("language_unresolved" in item for item in resolution.warnings)


# --- 硬约束：代码里不许出现版本号 -------------------------------------------


def _hard_coded_numbers(path: Path) -> set[int]:
    """收集代码里**真正参与运算**的数字字面量（docstring 与注释不算）。"""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))

    numbers: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and id(node) not in docstrings
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
        ):
            numbers.add(node.value)
    return numbers


@pytest.mark.parametrize("major", [22631, 26100, 26200, 26300])
def test_no_version_constants_in_code(major: int) -> None:
    """主版本号只能出现在 `data/*.json` 数据文件里，不能写进 Python 代码。"""
    root = Path(__file__).resolve().parents[1] / "src" / "uup2iso"
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if major in _hard_coded_numbers(path)
    ]

    assert offenders == [], f"{major} 出现在代码字面量：{offenders}"
