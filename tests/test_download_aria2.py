"""aria2c 下载器门禁（下载 §4、§5）。

用假 runner 模拟「aria2c 把文件写出来」，覆盖：参数契约、输入列表、大小/哈希校验、
哈希不符重下（≤2 次）、状态文件、`names` 过滤。真实下载用 `scripts/smoke_download.py`。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from uup2iso.core.errors import ErrorCode, StageExecutionError
from uup2iso.core.models.enums import DownloadFileState
from uup2iso.core.models.manifest import Manifest, ManifestSource, UupFile
from uup2iso.infrastructure.download import (
    Aria2Downloader,
    Aria2Options,
    build_input_file,
    sha1_of,
    verify_file,
)
from uup2iso.infrastructure.download.aria2 import _MAX_REDOWNLOADS

CONTENT = b"UUP-PAYLOAD" * 100
GOOD_SHA1 = hashlib.sha1(CONTENT).hexdigest()


def make_file(name: str = "a.cab", *, size: int | None = None, sha1: str | None = None) -> UupFile:
    return UupFile(
        name=name,
        size=len(CONTENT) if size is None else size,
        sha1=GOOD_SHA1 if sha1 is None else sha1,
        url=f"http://tlu.dl.delivery.mp.microsoft.com/files/{name}",
    )


def make_manifest(files: tuple[UupFile, ...] = ()) -> Manifest:
    items = files or (make_file(),)
    return Manifest(
        build="26100.9725",
        revision="9725",
        language="zh-cn",
        edition="professional",
        files=items,
        source=ManifestSource(
            base_url="https://uupdump.net/", fetched_at=__import__("datetime").datetime.now()
        ),
        total_bytes=sum(item.size for item in items),
    )


class FakeProvider:
    async def ensure(self, _kind: object) -> object:
        return SimpleNamespace(path=Path("aria2c.exe"), version="1.37.0")


class FakeRunner:
    """模拟 aria2c：按输入列表把内容写到 dir/out。

    ``rounds`` 控制每轮写什么：``"good"`` 写正确内容，``"bad"`` 写坏内容。
    """

    def __init__(self, rounds: list[str] | None = None) -> None:
        self.rounds = rounds or ["good"]
        self.calls: list[tuple[str, ...]] = []

    async def run(self, spec: object, *, on_event: object = None, check: bool = True) -> object:
        args = tuple(spec.args)
        self.calls.append(args)
        input_file = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--input-file="))
        mode = self.rounds[min(len(self.calls) - 1, len(self.rounds) - 1)]
        target_dir = None
        name = None
        for line in Path(input_file).read_text(encoding="utf-8").splitlines():
            if line.startswith("  dir="):
                target_dir = Path(line.split("=", 1)[1])
            elif line.startswith("  out="):
                name = line.split("=", 1)[1]
                if target_dir and name:
                    payload = CONTENT if mode == "good" else b"corrupted"
                    (target_dir / name).write_bytes(payload)
        if on_event is not None:
            on_event(
                SimpleNamespace(
                    kind=SimpleNamespace(value="progress"), text="[#1 1MB/2MB(50%) CN:1]"
                )
            )
        return SimpleNamespace(returncode=0)


def make_downloader(tmp_path: Path, runner: FakeRunner, **kwargs: object) -> Aria2Downloader:
    return Aria2Downloader(
        provider=FakeProvider(),  # type: ignore[arg-type]
        runner=runner,  # type: ignore[arg-type]
        work_dir=tmp_path / "task",
        sleep=lambda _s: None,
        **kwargs,  # type: ignore[arg-type]
    )


def test_input_file_format_is_absolute(tmp_path: Path) -> None:
    dest = tmp_path / "uup"
    path = build_input_file(tmp_path / "in.txt", [make_file()], dest)
    lines = path.read_text(encoding="utf-8").splitlines()

    assert lines[0].startswith("http://tlu.dl.delivery.mp.microsoft.com/")
    assert lines[1] == f"  dir={dest.resolve()}", "必须是绝对路径（aria2c 的 cwd 不是这里）"
    assert lines[2] == "  out=a.cab"


def test_options_match_parameter_contract(tmp_path: Path) -> None:
    args = Aria2Options().to_args(tmp_path / "in.txt")
    joined = " ".join(args)

    assert f"--input-file={tmp_path / 'in.txt'}" in joined
    assert "--continue=true" in joined
    assert "--max-concurrent-downloads=1" in joined
    assert "--split=8" in joined
    assert "--min-split-size=20M" in joined
    assert "--max-tries=3" in joined
    assert "--retry-wait=2" in joined
    assert "--max-file-not-found=1" in joined
    assert "--summary-interval=1" in joined
    assert "--console-log-level=warn" in joined
    assert "--check-certificate=true" in joined
    assert "--all-proxy" not in joined, "未配置代理时不能带空代理参数"


def test_options_proxy_and_limit_are_optional(tmp_path: Path) -> None:
    args = Aria2Options(proxy="http://127.0.0.1:7890", max_overall_limit="2M").to_args(
        tmp_path / "in.txt"
    )
    assert "--all-proxy=http://127.0.0.1:7890" in args
    assert "--max-overall-download-limit=2M" in args


def test_split_is_clamped(tmp_path: Path) -> None:
    assert "--split=16" in Aria2Options(split=99).to_args(tmp_path / "a")
    assert "--split=1" in Aria2Options(split=0).to_args(tmp_path / "a")


# --- 校验 -------------------------------------------------------------------


def test_verify_missing_file() -> None:
    result = verify_file(Path("nope.cab"), 10, GOOD_SHA1)
    assert result.state is DownloadFileState.PENDING
    assert result.error == "missing"


def test_verify_size_mismatch_skips_hash(tmp_path: Path) -> None:
    path = tmp_path / "a.cab"
    path.write_bytes(CONTENT + b"x")
    result = verify_file(path, len(CONTENT), GOOD_SHA1)
    assert result.state is DownloadFileState.FAILED
    assert result.error == "size_mismatch"
    assert result.actual_sha1 is None, "大小不符就不必算哈希（快速失败）"


def test_verify_sha1_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "a.cab"
    path.write_bytes(b"other content")
    result = verify_file(path, len(b"other content"), GOOD_SHA1)
    assert result.state is DownloadFileState.FAILED
    assert result.error == "sha1_mismatch"
    assert result.actual_sha1 and result.actual_sha1 != GOOD_SHA1


def test_verify_ok(tmp_path: Path) -> None:
    path = tmp_path / "a.cab"
    path.write_bytes(CONTENT)
    result = verify_file(path, len(CONTENT), GOOD_SHA1)
    assert result.state is DownloadFileState.VERIFIED
    assert result.is_ok


def test_sha1_of_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"abc")
    assert sha1_of(path) == hashlib.sha1(b"abc").hexdigest()


# --- 下载流程 ---------------------------------------------------------------


def test_download_success_writes_state(tmp_path: Path) -> None:
    runner = FakeRunner(["good"])
    downloader = make_downloader(tmp_path, runner)
    dest = tmp_path / "uup"

    report = asyncio.run(downloader.download(make_manifest(), dest_dir=dest))

    assert report.is_complete and len(report.files) == 1
    assert report.retry_rounds == 0
    assert (dest / "a.cab").stat().st_size == len(CONTENT)
    state = json.loads(downloader.state_path.read_text(encoding="utf-8"))
    assert state["files"][0]["state"] == "verified"
    assert state["files"][0]["verifiedAt"]


def test_download_retries_once_on_corruption(tmp_path: Path) -> None:
    runner = FakeRunner(["bad", "good"])
    downloader = make_downloader(tmp_path, runner)

    report = asyncio.run(downloader.download(make_manifest(), dest_dir=tmp_path / "uup"))

    assert report.is_complete
    assert report.retry_rounds == 1
    assert len(runner.calls) == 2, "坏文件应重下一轮"


def test_download_gives_up_after_two_redownloads(tmp_path: Path) -> None:
    runner = FakeRunner(["bad"])
    downloader = make_downloader(tmp_path, runner)

    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(downloader.download(make_manifest(), dest_dir=tmp_path / "uup"))

    assert caught.value.code is ErrorCode.DOWNLOAD_CORRUPT
    assert "a.cab" in caught.value.message
    assert len(runner.calls) == _MAX_REDOWNLOADS + 1, "合计 3 次机会"
    state = json.loads(downloader.state_path.read_text(encoding="utf-8"))
    assert state["files"][0]["state"] == "failed"


def test_download_only_selected_names(tmp_path: Path) -> None:
    manifest = make_manifest((make_file("a.cab"), make_file("b.cab")))
    runner = FakeRunner(["good"])
    downloader = make_downloader(tmp_path, runner)

    report = asyncio.run(downloader.download(manifest, dest_dir=tmp_path / "uup", names=("b.cab",)))

    assert [item.name for item in report.files] == ["b.cab"]
    assert not (tmp_path / "uup" / "a.cab").exists()


def test_unknown_name_raises(tmp_path: Path) -> None:
    downloader = make_downloader(tmp_path, FakeRunner())
    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(
            downloader.download(make_manifest(), dest_dir=tmp_path / "uup", names=("nope.cab",))
        )
    assert caught.value.code is ErrorCode.MANIFEST_INVALID


def test_progress_is_forwarded(tmp_path: Path) -> None:
    seen: list[float] = []

    class Hub:
        def report(self, stage: str, *, substep: str, percent: float, message: object) -> None:
            seen.append(percent)

    downloader = make_downloader(tmp_path, FakeRunner(["good"]), progress=Hub())
    asyncio.run(downloader.download(make_manifest(), dest_dir=tmp_path / "uup"))

    assert seen == [50.0], "进度只进通道，不落日志"
