"""`ProcessRunner`：外部进程的唯一入口（进程 §3–§9）。

一次调用做这些事，顺序即契约：

1. **并发闸门**：镜像类共享 ``image``、下载独占 ``download``，固定 1（进程 §8）；
   等待超 60 秒记 ``lock_wait``，配置里想加并发则忽略并记 ``concurrency_ignored``。
2. **启动**：非 shell、stdin=DEVNULL、双路管道、``CREATE_NO_WINDOW |
   CREATE_NEW_PROCESS_GROUP``；创建失败按「缺工具/无权限/启动失败」分类。
3. **双路并发读**：每路各一个任务 + 各自的 :class:`LineSplitter`（兼容 ``\\r`` 重绘）。
4. **解码与分类**：逐行解码（带回退链）→ 分类（progress / line / result）。
5. **取消**：有控制台先 Ctrl-Break，5 秒后 terminate，再 2 秒后 ``taskkill /T /F``；
   无控制台直接从 terminate 开始（进程 §7.1）。
6. **看门狗**：四信号联合，只告警不杀（进程 §7.2）。
7. **收尾**：非 0 且 ``check=True`` 抛 ``ToolExecutionError``，现场信息按 §9.2 组装。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from uup2iso.core.abstractions.runtime import CancelToken
from uup2iso.core.errors import UserCancelledError
from uup2iso.core.models.enums import ToolKind
from uup2iso.infrastructure.process.backend import (
    ByteStream,
    ProcessBackend,
    RealBackend,
    RunSpec,
    SpawnedProcess,
)
from uup2iso.infrastructure.process.classifier import LineClassifier
from uup2iso.infrastructure.process.decoder import LineSplitter, decode_line
from uup2iso.infrastructure.process.errors import (
    PermissionDeniedError,
    ToolMissingError,
    ToolStartError,
    build_tool_failure,
    redact_command,
)
from uup2iso.infrastructure.process.events import EventKind, ProcessEvent, ProcessResult
from uup2iso.infrastructure.process.profiles import profile_for
from uup2iso.infrastructure.process.sampler import NullSampler, ResourceSampler
from uup2iso.infrastructure.process.watchdog import (
    FourSignalWatchdog,
    HangEvidence,
    WatchdogConfig,
    WatchdogState,
)

__all__ = ["ConcurrencyGate", "ProcessRunner", "DEFAULT_GATE"]

LAST_LINES = 20
"""错误对象保留的输出行数（进程 §9.2）。"""

_FALLBACK_WARN_THRESHOLD = 20
"""同进程内连续解码回退超过此值告警一次（进程 §4.2）。"""

_READ_CHUNK = 8192
_POLL_INTERVAL = 0.1


class ConcurrencyGate:
    """按资源名持有 ``asyncio.Lock``；跨阶段共享同一实例（进程 §8）。"""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, resource: str) -> asyncio.Lock:
        return self._locks.setdefault(resource, asyncio.Lock())


DEFAULT_GATE = ConcurrencyGate()
"""进程级默认闸门；组合根可注入测试专用实例。"""


class ProcessRunner:
    """外部工具调用的唯一入口。

    Args:
        backend: 进程后端；默认真进程，测试注入 :class:`FakeBackend`。
        classifier: 行分类器。
        sampler: 资源采样器；``NullSampler`` 表示不启用看门狗。
        gate: 并发闸门。
        watchdog_config: 看门狗参数。
        sleep: 可注入的睡眠实现（测试用零延迟）。
        ctrl_break_grace: Ctrl-Break 后的等待秒数，默认 5（进程 §7.1）。
        terminate_grace: terminate 后的等待秒数，默认 2。
        lock_warn_seconds: 锁等待告警阈值，默认 60。
    """

    def __init__(
        self,
        *,
        backend: ProcessBackend | None = None,
        classifier: LineClassifier | None = None,
        sampler: ResourceSampler | None = None,
        gate: ConcurrencyGate | None = None,
        watchdog_config: WatchdogConfig | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        ctrl_break_grace: float = 5.0,
        terminate_grace: float = 2.0,
        lock_warn_seconds: float = 60.0,
    ) -> None:
        self._backend = backend or RealBackend()
        self._classifier = classifier or LineClassifier()
        self._sampler: ResourceSampler = sampler or NullSampler()
        self._gate = gate or DEFAULT_GATE
        self._watchdog_config = watchdog_config or WatchdogConfig()
        self._sleep = sleep or asyncio.sleep
        self._ctrl_break_grace = ctrl_break_grace
        self._terminate_grace = terminate_grace
        self._lock_warn_seconds = lock_warn_seconds

    async def run(
        self,
        spec: RunSpec,
        *,
        on_event: Callable[[ProcessEvent], None] | None = None,
        cancel: CancelToken | None = None,
        check: bool = True,
        concurrency_requested: int | None = None,
    ) -> ProcessResult:
        """执行一次外部工具调用。

        Args:
            spec: 工具、参数、工作目录等。
            on_event: 逐行回调（``ProcessEvent``）；GUI 侧需自行切回 UI 线程。
            cancel: 取消令牌。
            check: 非 0 返回码是否抛异常，默认 ``True``。
            concurrency_requested: 调用方想用的并发数；**仅供检测误配置**，
                非 1 时忽略并记 ``concurrency_ignored``（进程 §8）。

        Returns:
            运行结果（含统计、末 20 行、告警）。

        Raises:
            ToolMissingError: 可执行文件不存在（退出码 4）。
            PermissionDeniedError: 权限不足/需提权（退出码 4）。
            ToolStartError: 其他创建失败（退出码 4）。
            ToolExecutionError: 返回码非 0 且 ``check=True``（退出码 2）。
            UserCancelledError: 用户取消（退出码 3，不记 ERROR）。
        """
        warnings: list[str] = []
        if concurrency_requested is not None and concurrency_requested != 1:
            warnings.append("concurrency_ignored")

        profile = profile_for(spec.tool)
        lock = self._gate.lock(profile.resource)
        await self._acquire(lock, profile.resource, warnings)
        try:
            process = await self._spawn(spec)
            result, cancelled = await self._supervise(spec, process, on_event, cancel, warnings)
        finally:
            lock.release()

        if cancelled:
            raise UserCancelledError(
                f"{spec.tool.value} 已被取消",
                stage=spec.stage,
            )
        if check and not result.succeeded:
            raise build_tool_failure(spec, result, channel=result.last_channel)
        return result

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------

    async def _acquire(self, lock: asyncio.Lock, resource: str, warnings: list[str]) -> None:
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self._lock_warn_seconds)
        except TimeoutError:
            warnings.append(f"lock_wait resource={resource}")
            await lock.acquire()

    async def _spawn(self, spec: RunSpec) -> SpawnedProcess:
        try:
            return await self._backend.spawn(spec)
        except FileNotFoundError as exc:
            raise ToolMissingError(
                f"找不到可执行文件：{spec.args[0]}",
                stage=spec.stage,
                tool=spec.tool.value,
                command=redact_command(spec.args),
            ) from exc
        except PermissionError as exc:
            raise PermissionDeniedError(
                f"无权执行：{spec.args[0]}（可能需要管理员）",
                stage=spec.stage,
                tool=spec.tool.value,
                command=redact_command(spec.args),
            ) from exc
        except OSError as exc:
            raise ToolStartError(
                f"创建进程失败：{exc}",
                stage=spec.stage,
                tool=spec.tool.value,
                command=redact_command(spec.args),
            ) from exc

    async def _supervise(
        self,
        spec: RunSpec,
        process: SpawnedProcess,
        on_event: Callable[[ProcessEvent], None] | None,
        cancel: CancelToken | None,
        warnings: list[str],
    ) -> tuple[ProcessResult, bool]:
        """读输出、判取消、跑看门狗，直到进程退出。"""
        profile = profile_for(spec.tool)
        started = time.monotonic()
        queue: asyncio.Queue[tuple[str, bytes, bool, bool] | None] = asyncio.Queue()
        last_lines: deque[str] = deque(maxlen=LAST_LINES)
        counts = {EventKind.PROGRESS: 0, EventKind.LINE: 0, EventKind.RESULT: 0}
        hang_flag = {"suspected": False}
        last_channel: str | None = None
        fallback_lines = 0
        fallback_warned = False

        watchdog, watchdog_task = self._start_watchdog(process.pid, spec, hang_flag, warnings)
        readers = [
            asyncio.create_task(
                self._pump(process.stdout, "stdout", queue, watchdog),
            ),
            asyncio.create_task(
                self._pump(process.stderr, "stderr", queue, watchdog),
            ),
        ]
        cancelled = False

        try:
            finished = 0
            while finished < 2:
                if cancel is not None and cancel.is_cancelled() and not cancelled:
                    cancelled = True
                    await self._abort(process, warnings)

                try:
                    item = await asyncio.wait_for(queue.get(), timeout=_POLL_INTERVAL)
                except TimeoutError:
                    continue

                if item is None:
                    finished += 1
                    continue

                channel, payload, splitter_truncated, partial = item
                decoded = decode_line(payload, profile.encoding)
                if decoded.encoding != profile.encoding:
                    fallback_lines += 1
                    if not fallback_warned and fallback_lines > _FALLBACK_WARN_THRESHOLD:
                        fallback_warned = True
                        warnings.append(
                            f"decoding_fallback tool={spec.tool.value} encoding={decoded.encoding}"
                        )

                kind = self._classifier.classify(
                    spec.tool, decoded.text, version_known=spec.version_known
                )
                counts[kind] += 1
                if kind is not EventKind.PROGRESS:
                    last_lines.append(decoded.text)
                    last_channel = channel

                if on_event is not None:
                    on_event(
                        ProcessEvent(
                            kind=kind,
                            channel=channel,
                            text=decoded.text,
                            ts=time.monotonic(),
                            encoding=decoded.encoding,
                            truncated=decoded.truncated or splitter_truncated,
                            partial=partial,
                            raw_size=len(payload),
                        )
                    )

            returncode = await process.wait()
        finally:
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            if watchdog is not None:
                watchdog.stop()
            if watchdog_task is not None:
                watchdog_task.cancel()
                with suppress(asyncio.CancelledError):
                    await watchdog_task

        result = ProcessResult(
            tool=spec.tool,
            returncode=returncode,
            duration_seconds=time.monotonic() - started,
            progress_events=counts[EventKind.PROGRESS],
            line_events=counts[EventKind.LINE],
            result_events=counts[EventKind.RESULT],
            last_lines=tuple(last_lines),
            last_channel=last_channel,
            warnings=tuple(warnings),
            hang_suspected=hang_flag["suspected"],
        )
        if cancel is not None and cancel.is_cancelled():
            cancelled = True
        return result, cancelled

    async def _pump(
        self,
        stream: ByteStream | None,
        channel: str,
        queue: asyncio.Queue[tuple[str, bytes, bool, bool] | None],
        watchdog: FourSignalWatchdog | None,
    ) -> None:
        """单通道读取：切行 → 入队；任何字节到达都算「有输出」（看门狗信号 1）。"""
        try:
            if stream is None:
                return
            splitter = LineSplitter()
            while True:
                chunk = await stream.read(_READ_CHUNK)
                if not chunk:
                    break
                if watchdog is not None:
                    watchdog.note_output()
                for payload, truncated in splitter.feed(chunk):
                    await queue.put((channel, payload, truncated, False))
            for payload, truncated in splitter.flush():
                await queue.put((channel, payload, truncated, True))
        finally:
            await queue.put(None)

    def _start_watchdog(
        self,
        pid: int,
        spec: RunSpec,
        hang_flag: dict[str, bool],
        warnings: list[str],
    ) -> tuple[FourSignalWatchdog | None, asyncio.Task[None] | None]:
        if isinstance(self._sampler, NullSampler):
            return None, None

        profile = profile_for(spec.tool)
        state = WatchdogState(config=self._watchdog_config, has_network=profile.has_network)

        def _on_suspect(evidence: HangEvidence) -> None:
            hang_flag["suspected"] = True
            warnings.append(f"suspected_hang {evidence.as_text()}")

        watchdog = FourSignalWatchdog(
            state=state,
            cpu_seconds=lambda: self._sampler.cpu_seconds(pid),
            io_bytes=lambda: self._sampler.io_bytes(pid),
            net_bytes=lambda: self._sampler.net_bytes(),
            on_suspect=_on_suspect,
            sleep=self._sleep,
        )
        return watchdog, asyncio.create_task(watchdog.run())

    async def _abort(self, process: SpawnedProcess, warnings: list[str]) -> None:
        """按进程 §7.1 的阶梯终止；`console=none` 时跳过 Ctrl-Break。"""
        console = self._backend.has_console()
        mode = "none"
        if console and process.send_ctrl_break():
            mode = "ctrl_break"
            if await self._wait_exit(process, self._ctrl_break_grace):
                warnings.append(f"cancel mode={mode} console=yes")
                return
        process.terminate()
        if await self._wait_exit(process, self._terminate_grace):
            warnings.append(f"cancel mode=terminate console={'yes' if console else 'none'}")
            return
        await self._backend.kill_tree(process.pid)
        await self._wait_exit(process, 1.0)
        warnings.append(f"cancel mode=kill_tree console={'yes' if console else 'none'}")

    async def _wait_exit(self, process: SpawnedProcess, timeout: float) -> bool:
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False


async def run_process(
    args: list[str],
    *,
    tool: ToolKind,
    cwd: Path,
    stage: str | None = None,
    runner: ProcessRunner | None = None,
) -> ProcessResult:
    """便捷封装：起一个工具并等待完成（供工具自检等短任务使用）。"""
    engine = runner or ProcessRunner()
    return await engine.run(RunSpec(tool=tool, args=tuple(args), cwd=cwd, stage=stage))
