"""缺失必需工具的自动获取（引擎 §9.2）。

流程固定：**按清单 URL 下载 → SHA256 校验 → 解压到暂存 → 原子落位**。
三步都不能省：

* 没有哈希校验就等于随便从网上捡个 exe；
* 直接解压到 ``tools\\`` 会在中途失败时留下半个工具；
* 覆盖已有文件前必须先落到暂存目录再 replace，否则被杀软/占用打断就两头不是。

**只解 zip**：7-Zip 官方只提供 ``.exe``/``.msi``/``.7z``，解 ``.7z`` 需要 7z 自身（引导悖论），
所以 7z 走「随包内置」而不是自动获取（决策见 CHANGELOG 0.1.15）。
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from uup2iso.core.models.enums import ToolKind
from uup2iso.infrastructure.process.errors import ToolMissingError
from uup2iso.infrastructure.tools.lock import LockedTool, ToolLock
from uup2iso.infrastructure.tools.provider import TOOL_EXECUTABLES

__all__ = [
    "DEFAULT_ALLOWED_HOSTS",
    "FetchOutcome",
    "ToolFetcher",
    "extract_zip",
    "sha256_of",
]

DEFAULT_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "wimlib.net",
        "www.wimlib.net",
        "7-zip.org",
        "www.7-zip.org",
    }
)
"""允许的下载域名（引擎 §9.1）。

注意：这只解决「从哪里下」，不解决「信谁」——信任仍来自清单里的哈希。
GitHub 的 release 资产会 302 到 ``release-assets.githubusercontent.com``，故一并列入。
"""

_CHUNK = 1024 * 256


def _preplaced_size(archive: Path, expected_sha256: str) -> int | None:
    """检查是否已有手工放置的压缩包且哈希一致；命中返回字节数。"""
    if not archive.is_file():
        return None
    if sha256_of(archive) != expected_sha256:
        # 哈希不符的中转文件不可信，删掉避免下次误用
        archive.unlink(missing_ok=True)
        return None
    return archive.stat().st_size


def _describe(exc: Exception) -> str:
    """异常文本可能为空（如代理直接断连），补上类型名，避免日志出现空白原因。"""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _content_length(response: httpx.Response) -> int | None:
    raw = response.headers.get("Content-Length")
    return int(raw) if raw and raw.isdigit() else None


def sha256_of(path: Path) -> str:
    """算文件 SHA256（流式，避免把大文件读进内存）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """一次成功获取的结果。"""

    tool: ToolKind
    version: str
    sha256: str
    executable: Path
    """落位后的**可执行文件**（installed 里可能还有 DLL，别拿第一个当 exe）。"""

    installed: tuple[Path, ...]
    bytes_downloaded: int


class _BorrowedClient(AbstractAsyncContextManager[httpx.AsyncClient]):
    """包装注入的 client：退出时不关闭它（生命周期归调用方）。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *_exc: object) -> None:
        return None


class ToolFetcher:
    """按锁定清单获取必需工具。

    Args:
        tools_dir: 程序目录的 ``tools\\``。
        lock: 锁定清单（哈希与 URL 的唯一来源）。
        timeout: 单次请求超时（秒）。
        allowed_hosts: 允许的域名；测试可放宽。
        client: 注入的 httpx 客户端（测试用 MockTransport）。
        progress: ``(已下载字节, 总字节或 None)`` 回调；不写日志。
    """

    def __init__(
        self,
        *,
        tools_dir: Path,
        lock: ToolLock,
        timeout: float = 60.0,
        allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_HOSTS,
        client: httpx.AsyncClient | None = None,
        progress: Callable[[int, int | None], None] | None = None,
    ) -> None:
        self._tools_dir = tools_dir
        self._lock = lock
        self._timeout = timeout
        self._allowed = frozenset(allowed_hosts)
        self._client = client
        self._progress = progress

    async def fetch(self, kind: ToolKind) -> FetchOutcome:
        """下载、校验并落位一个工具。

        Raises:
            ToolMissingError: 清单未登记、域名不允许、哈希不符或解压失败。
        """
        entry = self._lock.entry_for(kind.value)
        if entry is None or not entry.sha256:
            raise ToolMissingError(
                f"{kind.value} 未在锁定清单登记，无法自动获取（不联网兜底、不猜版本）",
                tool=kind.value,
            )
        self._check_url(entry)

        download_dir = self._tools_dir / ".download"
        download_dir.mkdir(parents=True, exist_ok=True)
        archive = download_dir / f"{kind.value}-{entry.version}.zip"

        try:
            # 受限网络/离线时用户可以手工把压缩包放到这里；哈希一致就跳过下载
            downloaded = _preplaced_size(archive, entry.sha256)
            if downloaded is None:
                downloaded = await self._download(entry.url, archive)
            actual = sha256_of(archive)
            if actual != entry.sha256:
                raise ToolMissingError(
                    f"{kind.value} 下载校验失败：期望 {entry.sha256}，实际 {actual}",
                    tool=kind.value,
                )

            staging = download_dir / f"staging-{kind.value}"
            if staging.exists():
                shutil.rmtree(staging)
            exe_source = extract_zip(
                archive, staging, exe_names=TOOL_EXECUTABLES.get(kind, ())
            )
            installed, executable = self._install(kind, exe_source)
        finally:
            archive.unlink(missing_ok=True)

        return FetchOutcome(
            tool=kind,
            version=entry.version,
            sha256=entry.sha256,
            executable=executable,
            installed=installed,
            bytes_downloaded=downloaded,
        )

    # -- 内部 -------------------------------------------------------------

    def _check_url(self, entry: LockedTool) -> None:
        parsed = urlparse(entry.url)
        if parsed.scheme != "https":
            raise ToolMissingError(
                f"{entry.name} 的下载地址不是 HTTPS，拒绝下载：{entry.url}",
                tool=entry.name,
            )
        host = (parsed.hostname or "").lower()
        if host not in self._allowed:
            raise ToolMissingError(
                f"{entry.name} 的下载域名不在白名单：{host}（引擎 §9.1）",
                tool=entry.name,
            )

    async def _download(self, url: str, dest: Path) -> int:
        try:
            async with self._make_client() as client:
                return await self._stream_download(client, url, dest)
        except httpx.RemoteProtocolError:
            # 实测坑：部分 HTTP 代理对「302 到 GitHub 资产 CDN」的流式请求会直接断连
            # （非流式读取正常）。断连会污染连接池，所以第二次必须换新 client。
            # 工具体积都在 10 MB 内，缓冲读取的额外内存可接受。
            pass
        except httpx.HTTPError as exc:
            dest.unlink(missing_ok=True)
            raise ToolMissingError(f"下载失败：{_describe(exc)}", tool=dest.stem) from exc

        try:
            async with self._make_client() as client:
                return await self._buffered_download(client, url, dest)
        except httpx.HTTPError as exc:
            dest.unlink(missing_ok=True)
            raise ToolMissingError(f"下载失败（已回退缓冲模式）：{_describe(exc)}", tool=dest.stem) from exc

    def _make_client(self) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        if self._client is not None:
            # 注入的 client 由调用方管理生命周期，这里包一层“不关闭”的语境
            return _BorrowedClient(self._client)
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=20.0),
            follow_redirects=True,
        )

    async def _stream_download(self, client: httpx.AsyncClient, url: str, dest: Path) -> int:
        written = 0
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            expected = _content_length(response)
            with dest.open("wb") as handle:
                async for chunk in response.aiter_bytes(_CHUNK):
                    handle.write(chunk)
                    written += len(chunk)
                    self._report(written, expected)
        return written

    async def _buffered_download(self, client: httpx.AsyncClient, url: str, dest: Path) -> int:
        response = await client.get(url)
        response.raise_for_status()
        dest.write_bytes(response.content)
        self._report(len(response.content), len(response.content))
        return len(response.content)

    def _report(self, written: int, expected: int | None) -> None:
        if self._progress is not None:
            self._progress(written, expected)

    def _install(self, kind: ToolKind, exe_source: Path) -> tuple[tuple[Path, ...], Path]:
        """把 exe **及其同目录的 DLL** 复制到 ``tools\\<kind>\\``，逐个原子替换。

        wimlib 需要 ``libwim-15.dll`` 才能跑，只拷 exe 会得到一个「存在但跑不起来」的工具。
        """
        target_dir = self._tools_dir / kind.value
        target_dir.mkdir(parents=True, exist_ok=True)

        installed: list[Path] = []
        executable: Path | None = None
        for source in sorted(exe_source.parent.iterdir()):
            if not source.is_file():
                continue
            if source.suffix.lower() not in {".exe", ".dll"}:
                continue
            destination = target_dir / source.name
            tmp = destination.with_name(f"{destination.name}.new")
            shutil.copy2(source, tmp)
            tmp.replace(destination)
            installed.append(destination)
            if source.name.lower() == exe_source.name.lower():
                executable = destination

        if not installed or executable is None:
            raise ToolMissingError(
                f"{kind.value} 解压后未找到可执行文件", tool=kind.value
            )
        return tuple(installed), executable


def extract_zip(archive: Path, target: Path, *, exe_names: Iterable[str]) -> Path:
    """解压 zip 并返回目标 exe 的路径（取层级最浅的那个）。

    Args:
        archive: zip 文件。
        target: 解压目录（会被清空重建）。
        exe_names: 候选可执行文件名。

    Returns:
        解压后 exe 的绝对路径。

    Raises:
        ToolMissingError: 压缩包损坏、含越界路径，或找不到可执行文件。
    """
    wanted = {name.lower() for name in exe_names}
    target.mkdir(parents=True, exist_ok=True)
    found: list[Path] = []

    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                name = member.filename
                # 拒绝越界路径（zip-slip）
                if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                    raise ToolMissingError(
                        f"压缩包含可疑路径，拒绝解压：{name}", tool=archive.stem
                    )
                bundle.extract(member, target)
                extracted = target / name
                if extracted.is_file() and extracted.suffix.lower() == ".exe":
                    if extracted.name.lower() in wanted:
                        found.append(extracted)
    except zipfile.BadZipFile as exc:
        raise ToolMissingError(f"压缩包损坏：{exc}", tool=archive.stem) from exc

    if not found:
        raise ToolMissingError(
            f"压缩包内找不到 {sorted(wanted)}", tool=archive.stem
        )
    # 层级最浅者优先（避免误取 docs/examples 里的同名 exe）
    return min(found, key=lambda item: len(item.relative_to(target).parts))
