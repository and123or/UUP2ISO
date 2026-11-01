"""工具自动获取门禁（引擎 §9.2）。

覆盖：域名白名单、哈希校验、zip 解压（含 zip-slip）、原子落位、手工放置压缩包。
全部用 `httpx.MockTransport` 回放，不联网。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from uup2iso.core.models.enums import ToolKind
from uup2iso.infrastructure.process.errors import ToolMissingError
from uup2iso.infrastructure.tools.fetcher import (
    FetchOutcome,
    ToolFetcher,
    extract_zip,
    sha256_of,
)
from uup2iso.infrastructure.tools.lock import LockedTool, ToolLock


def make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def package_bytes(*, exe: str = "aria2c.exe", extra: dict[str, bytes] | None = None) -> bytes:
    entries = {f"pkg/{exe}": b"fake-exe", "pkg/README.txt": b"readme"}
    if extra:
        entries.update({f"pkg/{name}": data for name, data in extra.items()})
    return make_zip(entries)


def lock_for(payload: bytes, *, version: str = "1.37.0", sha: str | None = None) -> ToolLock:
    return ToolLock(
        tools=[
            LockedTool(
                name="aria2c",
                url="https://github.com/aria2/aria2/releases/download/release-1.37.0/x.zip",
                sha256=sha if sha is not None else hashlib.sha256(payload).hexdigest(),
                version=version,
            )
        ]
    )


def fetcher_with(
    tmp_path: Path,
    lock: ToolLock,
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs: object,
) -> ToolFetcher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    return ToolFetcher(tools_dir=tmp_path / "tools", lock=lock, client=client, **kwargs)  # type: ignore[arg-type]


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


# --- 基础工具 ---------------------------------------------------------------


def test_sha256_of_file(tmp_path: Path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"hello")
    assert sha256_of(path) == hashlib.sha256(b"hello").hexdigest()


def test_extract_zip_finds_exe_even_in_subdir(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    archive.write_bytes(package_bytes(extra={"libwim-15.dll": b"dll"}))

    exe = extract_zip(archive, tmp_path / "out", exe_names=("aria2c.exe",))
    assert exe.name == "aria2c.exe"
    assert (exe.parent / "libwim-15.dll").is_file()


def test_extract_zip_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    archive.write_bytes(make_zip({"../escape.exe": b"x"}))
    with pytest.raises(ToolMissingError) as caught:
        extract_zip(archive, tmp_path / "out", exe_names=("aria2c.exe",))
    assert "可疑路径" in caught.value.message


def test_extract_zip_without_target_exe_fails(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    archive.write_bytes(make_zip({"pkg/other.exe": b"x"}))
    with pytest.raises(ToolMissingError):
        extract_zip(archive, tmp_path / "out", exe_names=("aria2c.exe",))


# --- 域名白名单 -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a.zip",
        "https://evil.example.com/a.zip",
        "https://github.com.evil.io/a.zip",
    ],
)
def test_untrusted_url_rejected(tmp_path: Path, url: str) -> None:
    lock = ToolLock(tools=[LockedTool(name="aria2c", url=url, sha256="0" * 64, version="1")])
    fetcher = fetcher_with(tmp_path, lock, lambda request: httpx.Response(200, content=b""))

    with pytest.raises(ToolMissingError) as caught:
        run(fetcher.fetch(ToolKind.ARIA2C))
    assert "白名单" in caught.value.message or "HTTPS" in caught.value.message


def test_unregistered_tool_rejected(tmp_path: Path) -> None:
    fetcher = fetcher_with(tmp_path, ToolLock(), lambda request: httpx.Response(200))
    with pytest.raises(ToolMissingError) as caught:
        run(fetcher.fetch(ToolKind.ARIA2C))
    assert "未在锁定清单登记" in caught.value.message


# --- 下载与落位 -------------------------------------------------------------


def test_fetch_installs_exe_and_dll(tmp_path: Path) -> None:
    payload = package_bytes(extra={"libwim-15.dll": b"dll"})
    lock = lock_for(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    outcome = run(fetcher_with(tmp_path, lock, handler).fetch(ToolKind.ARIA2C))
    assert isinstance(outcome, FetchOutcome)
    assert outcome.executable.name == "aria2c.exe"
    assert outcome.executable.is_file()
    assert (outcome.executable.parent / "libwim-15.dll").is_file()
    assert outcome.version == "1.37.0"
    # 临时文件/中转压缩包不应残留
    download_dir = tmp_path / "tools" / ".download"
    assert not list(download_dir.glob("*.zip"))


def test_hash_mismatch_aborts_install(tmp_path: Path) -> None:
    payload = package_bytes()
    lock = lock_for(payload, sha="f" * 64)

    outcome_dir = tmp_path / "tools" / "aria2c"
    with pytest.raises(ToolMissingError) as caught:
        run(
            fetcher_with(tmp_path, lock, lambda r: httpx.Response(200, content=payload)).fetch(
                ToolKind.ARIA2C
            )
        )
    assert "校验失败" in caught.value.message
    assert not (outcome_dir / "aria2c.exe").exists(), "校验失败不能落位"


def test_preplaced_archive_skips_download(tmp_path: Path) -> None:
    """受限网络下，用户手工把压缩包放进 tools\\.download\\ 即可离线安装。"""
    payload = package_bytes()
    lock = lock_for(payload)
    download_dir = tmp_path / "tools" / ".download"
    download_dir.mkdir(parents=True)
    (download_dir / "aria2c-1.37.0.zip").write_bytes(payload)

    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        raise AssertionError("已有合法压缩包时不应再联网")

    outcome = run(fetcher_with(tmp_path, lock, handler).fetch(ToolKind.ARIA2C))
    assert outcome.executable.is_file()


def test_stale_preplaced_archive_is_discarded(tmp_path: Path) -> None:
    payload = package_bytes()
    lock = lock_for(payload)
    download_dir = tmp_path / "tools" / ".download"
    download_dir.mkdir(parents=True)
    bogus = download_dir / "aria2c-1.37.0.zip"
    bogus.write_bytes(b"not the right bytes")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    outcome = run(fetcher_with(tmp_path, lock, handler).fetch(ToolKind.ARIA2C))
    assert outcome.executable.is_file(), "哈希不符的中转包应被丢弃后重新下载"
