"""aria2c 下载器（下载 §4、§5）。

分工明确：**网络层重试交给 aria2c**（`--max-tries=3 --retry-wait=2`），
**校验层由这里负责**（大小 → SHA1 流式），两者不叠加放大。

三条底线：
1. 只经 `ProcessRunner` 调 aria2c（进程 §3），输入列表由程序生成、不走 shell；
2. 校验顺序固定「存在 → 大小 → SHA1」，先快速失败再算哈希；
3. **绝不忽略哈希**：重下 2 次仍不符就 `DOWNLOAD_CORRUPT` 并列出清单，宁可失败也不放行损坏文件。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from uup2iso.core.errors import ErrorCode, StageExecutionError
from uup2iso.core.models.download import DownloadedFile, DownloadReport
from uup2iso.core.models.enums import DownloadFileState, ToolKind
from uup2iso.core.models.manifest import Manifest, UupFile
from uup2iso.infrastructure.process.backend import RunSpec
from uup2iso.infrastructure.process.runner import ProcessRunner
from uup2iso.infrastructure.tools.provider import ToolProvider

__all__ = [
    "Aria2Downloader",
    "Aria2Options",
    "build_input_file",
    "sha1_of",
    "verify_file",
]

_CHUNK = 1024 * 1024
_PERCENT = re.compile(r"\((\d{1,3})%\)")
_MAX_REDOWNLOADS = 2
"""哈希不符最多重下 2 次（合计 3 次机会，下载 §5.2）。"""


@dataclass(frozen=True, slots=True)
class Aria2Options:
    """aria2c 参数契约（下载 §4.2）。"""

    split: int = 8
    max_concurrent: int = 1
    min_split_size: str = "20M"
    max_tries: int = 3
    retry_wait: int = 2
    max_file_not_found: int = 1
    summary_interval: int = 1
    console_log_level: str = "warn"
    proxy: str = ""
    max_overall_limit: str = ""
    """限速（如 ``2M``）；空表示不限。"""
    extra_args: tuple[str, ...] = field(default_factory=tuple)

    def to_args(self, input_file: Path) -> tuple[str, ...]:
        """生成 aria2c 参数（exe 之外的完整列表）。"""
        args: list[str] = [
            f"--input-file={input_file}",
            "--continue=true",
            f"--max-concurrent-downloads={self.max_concurrent}",
            f"--split={max(1, min(16, self.split))}",
            f"--min-split-size={self.min_split_size}",
            f"--max-tries={self.max_tries}",
            f"--retry-wait={self.retry_wait}",
            f"--max-file-not-found={self.max_file_not_found}",
            f"--summary-interval={self.summary_interval}",
            f"--console-log-level={self.console_log_level}",
            "--check-certificate=true",
        ]
        if self.proxy:
            args.append(f"--all-proxy={self.proxy}")
        if self.max_overall_limit:
            args.append(f"--max-overall-download-limit={self.max_overall_limit}")
        args.extend(self.extra_args)
        return tuple(args)


class Aria2Downloader:
    """按清单下载并校验。

    Args:
        provider: 工具供给（定位 aria2c）。
        runner: 进程执行器（唯一外部进程入口）。
        work_dir: 任务工作目录；输入列表与状态文件落在其 ``download\\`` 下。
        options: aria2c 参数。
        log: 运行日志写入端（``LogSink``）。
        progress: 进度通道（不落盘）。
        sleep: 可注入等待（测试免等）。
    """

    def __init__(
        self,
        *,
        provider: ToolProvider,
        runner: ProcessRunner,
        work_dir: Path,
        options: Aria2Options | None = None,
        log: Any = None,
        progress: Any = None,
        sleep: Any = None,
    ) -> None:
        self._provider = provider
        self._runner = runner
        self._work_dir = work_dir
        self._options = options or Aria2Options()
        self._log = log
        self._progress = progress
        self._sleep = sleep or time.sleep

    def bind(self, *, log: Any = None, progress: Any = None) -> None:
        """运行期绑定日志与进度（组合根先建对象、后接上下文）。"""
        if log is not None:
            self._log = log
        if progress is not None:
            self._progress = progress

    @property
    def state_path(self) -> Path:
        return self._work_dir / "download" / "download.state.json"

    @property
    def input_path(self) -> Path:
        return self._work_dir / "download" / "aria2-input.txt"

    async def download(
        self,
        manifest: Manifest,
        *,
        dest_dir: Path,
        names: tuple[str, ...] | None = None,
    ) -> DownloadReport:
        """下载并校验清单中的文件。

        Args:
            manifest: 已通过五项自校验的清单。
            dest_dir: 落盘目录（通常是 ``work\\<task>\\uup\\``）。
            names: 只下指定文件（小范围验证用）；``None`` 表示全部。

        Returns:
            :class:`DownloadReport`。

        Raises:
            StageExecutionError: 重下 2 次仍不符（``DOWNLOAD_CORRUPT``）。
        """
        selected = [item for item in manifest.files if names is None or item.name in set(names)]
        if not selected:
            raise StageExecutionError("清单里没有要下载的文件", code=ErrorCode.MANIFEST_INVALID)

        dest_dir.mkdir(parents=True, exist_ok=True)
        self.input_path.parent.mkdir(parents=True, exist_ok=True)

        states: dict[str, DownloadedFile] = {}
        retry_rounds = 0
        started = time.monotonic()

        for round_index in range(_MAX_REDOWNLOADS + 1):
            pending = [item for item in selected if item.name not in states]
            if not pending:
                break

            build_input_file(self.input_path, pending, dest_dir.resolve())
            await self._run_aria2(pending, dest_dir)

            corrupt: list[str] = []
            for item in pending:
                outcome = verify_file(dest_dir / item.name, item.size, item.sha1)
                if outcome.state is DownloadFileState.VERIFIED:
                    states[item.name] = outcome
                    continue
                if outcome.error == "size_mismatch" or outcome.error == "sha1_mismatch":
                    corrupt.append(item.name)
                self._log_event(
                    "download_verify_failed" if item.name in corrupt else "download_failed",
                    name=item.name,
                    expected=item.sha1,
                    actual=outcome.actual_sha1 or "-",
                )
                states[item.name] = outcome

            if not corrupt or round_index >= _MAX_REDOWNLOADS:
                break

            for name in corrupt:
                _remove_artifacts(dest_dir / name)
                states.pop(name, None)
            retry_rounds += 1
            self._log_event("download_redownload", round=retry_rounds, files=",".join(corrupt))
            await self._sleep_async(2.0)

        report = DownloadReport(
            files=tuple(states[item.name] for item in selected if item.name in states),
            retry_rounds=retry_rounds,
            elapsed_seconds=time.monotonic() - started,
        )
        self._write_state(manifest, report)

        if not report.is_complete:
            details = "；".join(
                f"{fail.name}（期望 {fail.expected}，实际 {fail.actual or '-'}）"
                for fail in report.failures
            )
            raise StageExecutionError(
                f"下载校验失败（已重下 {retry_rounds} 次）：{details}",
                code=ErrorCode.DOWNLOAD_CORRUPT,
            )
        return report

    # -- 内部 -------------------------------------------------------------

    async def _run_aria2(self, files: list[UupFile], dest_dir: Path) -> None:
        info = await self._provider.ensure(ToolKind.ARIA2C)
        total_bytes = sum(item.size for item in files)
        # 关键：cwd 是 dest_dir，输入列表必须给绝对路径，否则 aria2c 找不到（下载 §4.1）
        args = (str(info.path), *self._options.to_args(self.input_path.resolve()))
        spec = RunSpec(
            tool=ToolKind.ARIA2C,
            args=args,
            cwd=dest_dir,
            stage="Fetch",
            version_known=info.version != "unknown",
        )

        seen: set[str] = set()

        def _on_event(event: object) -> None:
            kind = getattr(getattr(event, "kind", None), "value", "")
            text = getattr(event, "text", "")
            if kind == "progress":
                self._report_progress(text, total_bytes)
                return
            if kind in ("result", "line") and isinstance(text, str):
                key = text[:60]
                if key in seen:
                    return
                seen.add(key)
                self._log_line(text)

        result = await self._runner.run(spec, on_event=_on_event, check=False)
        self._log_event("aria2_finished", exit=result.returncode, files=len(files))

    def _report_progress(self, text: str, total_bytes: int) -> None:
        if self._progress is None:
            return
        match = _PERCENT.search(text)
        if not match:
            return
        percent = float(match.group(1))
        self._progress.report("Fetch", substep="download", percent=percent, message=None)

    def _log_event(self, event: str, **fields: object) -> None:
        if self._log is not None:
            self._log.info("infra.download", event, event=event, **fields)

    def _log_line(self, text: str) -> None:
        if self._log is not None:
            self._log.info("infra.download", text, event="aria2")

    async def _sleep_async(self, seconds: float) -> None:
        if seconds <= 0:
            return
        import asyncio

        await asyncio.to_thread(self._sleep, seconds)

    def _write_state(self, manifest: Manifest, report: DownloadReport) -> None:
        payload = {
            "taskId": "",
            "build": manifest.build,
            "updatedAt": datetime.now().astimezone().isoformat(),
            "files": [
                {
                    "name": item.name,
                    "state": item.state.value,
                    "retries": report.retry_rounds,
                    "verifiedAt": datetime.now().astimezone().isoformat() if item.is_ok else None,
                }
                for item in report.files
            ],
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        tmp.replace(self.state_path)


# ---------------------------------------------------------------------------
# 输入列表与校验
# ---------------------------------------------------------------------------


def build_input_file(
    path: Path, files: list[UupFile] | tuple[UupFile, ...], dest_dir: Path
) -> Path:
    """生成 aria2c 输入列表（下载 §4.1）：`url` / `  dir=` / `  out=`。"""
    absolute_dest = dest_dir.resolve()
    lines: list[str] = []
    for item in files:
        lines.append(item.url)
        lines.append(f"  dir={absolute_dest}")
        # `#` 开头会被 aria2c 当注释，文件名不会这样，但显式兜底
        lines.append(f"  out={item.name.lstrip('#')}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def sha1_of(path: Path) -> str:
    """流式 SHA1（1 MB 分块，不整文件读入内存）。"""
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, expected_size: int, expected_sha1: str) -> DownloadedFile:
    """按「存在 → 大小 → SHA1」顺序校验（下载 §5.1）。"""
    name = path.name
    if not path.is_file():
        return DownloadedFile(
            name=name,
            state=DownloadFileState.PENDING,
            expected_sha1=expected_sha1,
            error="missing",
        )

    size = path.stat().st_size
    if size != expected_size:
        return DownloadedFile(
            name=name,
            state=DownloadFileState.FAILED,
            size_bytes=size,
            expected_sha1=expected_sha1,
            error="size_mismatch",
        )

    actual = sha1_of(path)
    if actual.lower() != expected_sha1.lower():
        return DownloadedFile(
            name=name,
            state=DownloadFileState.FAILED,
            size_bytes=size,
            expected_sha1=expected_sha1,
            actual_sha1=actual,
            error="sha1_mismatch",
        )

    return DownloadedFile(
        name=name,
        state=DownloadFileState.VERIFIED,
        size_bytes=size,
        expected_sha1=expected_sha1,
        actual_sha1=actual,
    )


def _remove_artifacts(path: Path) -> None:
    """删除坏文件与其 `.aria2` 控制文件（下载 §5.2）。"""
    path.unlink(missing_ok=True)
    path.with_name(path.name + ".aria2").unlink(missing_ok=True)
