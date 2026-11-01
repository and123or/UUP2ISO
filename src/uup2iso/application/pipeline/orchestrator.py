"""PipelineOrchestrator（编排 §2、§7、§8、§11、§12）。

只做四件事，其它都交给阶段处理器：

1. **按计划跑**：跳过的阶段记 `stage=skipped reason=…`，执行的阶段按顺序推进；
2. **记账**：`task_started` / `stage_started` / `stage_finished` / `stage_skipped` / `stage_retry` /
   `task_cancelled` / `task_finished`，字段按编排 §11；
3. **重试**：只走白名单（`RetryPolicy`），阶段级兜底 2 次；重试前按需清理挂载残留；
4. **取消**：阶段之间与尝试之间都检查取消令牌，取消**保留中间产物**并把阶段标 `cancelled`。

`dry_run()` 只产出文本/结构，不创建 `work` 目录、不写检查点（编排 §12-11）。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from uup2iso.application.pipeline.handlers import (
    CleanupHook,
    StageContext,
    StageHandler,
    StageOutcome,
    has_dry_run_info,
)
from uup2iso.application.pipeline.planner import plan_pipeline
from uup2iso.application.pipeline.retry import RetryPolicy
from uup2iso.core.errors import InternalError, UserCancelledError, exit_code_for
from uup2iso.core.models.config import TaskConfig
from uup2iso.core.models.pipeline import StageKind, StagePlan
from uup2iso.infrastructure.logging.sink import LogSink
from uup2iso.infrastructure.progress.hub import ProgressHub

__all__ = [
    "DryRunReport",
    "FileOp",
    "DryRunStage",
    "PipelineOrchestrator",
    "PipelineRun",
]

STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class PipelineRun:
    """一次流水线执行的结果。"""

    task_id: str
    outcomes: tuple[StageOutcome, ...] = ()
    cancelled: bool = False
    failed: BaseException | None = None
    duration: float = 0.0

    def outcome(self, stage: StageKind) -> StageOutcome | None:
        return next((item for item in self.outcomes if item.stage is stage), None)

    @property
    def succeeded(self) -> bool:
        return not self.cancelled and self.failed is None

    @property
    def exit_code(self) -> int:
        """进程退出码（编排 §8：取消 = 3；其它按错误码折算，兜底 2）。"""
        if self.cancelled:
            return 3
        if self.failed is not None:
            return int(exit_code_for(self.failed))
        return 0

    @property
    def attempted_stages(self) -> tuple[StageKind, ...]:
        return tuple(item.stage for item in self.outcomes if item.status == STATUS_SUCCEEDED)


@dataclass(frozen=True, slots=True)
class FileOp:
    """`--dry-run` 里的一个文件操作（只描述，不执行）。"""

    action: str
    path: str
    note: str = ""

    def line(self) -> str:
        suffix = f"  # {self.note}" if self.note else ""
        return f"  {self.action:<6} {self.path}{suffix}"


@dataclass(frozen=True, slots=True)
class DryRunStage:
    """`--dry-run` 中一个阶段的说明。"""

    stage: StageKind
    will_run: bool
    skip_reason: str | None = None
    substeps: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    reused: tuple[str, ...] = ()
    file_ops: tuple[FileOp, ...] = ()
    handler_missing: bool = False
    """该阶段要执行、但当前没有处理器（WS2/WS3 尚未落地时的诚实标注）。"""

    def line(self) -> str:
        if not self.will_run:
            return f"[skip] {self.stage.value:<10} reason={self.skip_reason}"
        if self.handler_missing:
            return (
                f"[TODO] {self.stage.value:<10} substeps={','.join(self.substeps) or '-'}"
                "  (无处理器)"
            )
        parts = [f"[run ] {self.stage.value:<10} substeps={','.join(self.substeps) or '-'}"]
        if self.commands:
            parts.append(f"commands={len(self.commands)}")
        if self.reused:
            parts.append(f"reuse={','.join(self.reused)}")
        return "  ".join(parts)


@dataclass(frozen=True, slots=True)
class DryRunReport:
    """`--dry-run` 的完整输出（不产生任何副作用）。"""

    entry: str
    start_stage: StageKind
    stages: tuple[DryRunStage, ...] = ()
    work_dir: Path | None = None

    def lines(self) -> tuple[str, ...]:
        header = [f"entry={self.entry}  startStage={self.start_stage.value}"]
        return tuple(header + [stage.line() for stage in self.stages])

    def file_lines(self) -> tuple[str, ...]:
        """按阶段列出将发生的文件操作（T7：不改动磁盘，只预览）。"""
        lines: list[str] = []
        for stage in self.stages:
            if not stage.file_ops:
                continue
            lines.append(f"[{stage.stage.value}]")
            lines.extend(op.line() for op in stage.file_ops)
        return tuple(lines)

    @property
    def file_operations(self) -> tuple[tuple[str, FileOp], ...]:
        return tuple((stage.stage.value, op) for stage in self.stages for op in stage.file_ops)

    def command_lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        for stage in self.stages:
            lines.extend(f"{stage.stage.value}: {command}" for command in stage.commands)
        return tuple(lines)

    @property
    def executed(self) -> tuple[DryRunStage, ...]:
        return tuple(stage for stage in self.stages if stage.will_run)


class PipelineOrchestrator:
    """阶段驱动器。

    Args:
        handlers: 阶段 → 处理器；缺失的阶段会在运行时直接失败（不是静默跳过）。
        retry: 重试策略。
        cleanup: 重试前的残留清理钩子（挂载类阶段必需）。
        sleep: 可注入的等待（测试用零延迟）。
        clock: 可注入时钟（测试用）。
    """

    def __init__(
        self,
        *,
        handlers: Mapping[StageKind, StageHandler],
        retry: RetryPolicy | None = None,
        cleanup: CleanupHook | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._handlers = dict(handlers)
        self._retry = retry or RetryPolicy()
        self._cleanup = cleanup
        self._sleep = sleep or _async_sleep
        self._clock = clock or time.monotonic

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    async def run(
        self,
        config: TaskConfig,
        *,
        task_id: str,
        work_dir: Path,
        log: LogSink | None = None,
        progress: ProgressHub | None = None,
        cancel: object | None = None,
    ) -> PipelineRun:
        """按计划执行整条流水线。

        Args:
            config: 已通过校验的任务配置。
            task_id: 任务短 id。
            work_dir: 任务工作目录（**不会**在这里创建，由各阶段自行创建所需子目录）。
            log: 运行日志写入端。
            progress: 进度通道。
            cancel: 取消令牌（需实现 ``is_cancelled()``）。

        Returns:
            :class:`PipelineRun`；`exit_code` 可直接交给进程退出。
        """
        plan = plan_pipeline(config)
        started = self._clock()
        outcomes: list[StageOutcome] = []
        cancelled = False
        failed: BaseException | None = None

        self._log(
            log,
            "任务开始",
            event="task_started",
            task=task_id,
            fingerprint=config.fingerprint()[:16],
            stages=",".join(item.stage.value for item in plan.executed),
        )

        for stage_plan in plan.stages:
            if _is_cancelled(cancel):
                cancelled = True
                outcomes.append(StageOutcome(stage=stage_plan.stage, status=STATUS_CANCELLED))
                break

            if stage_plan.skipped:
                outcomes.append(
                    StageOutcome(
                        stage=stage_plan.stage,
                        status=STATUS_SKIPPED,
                        skip_reason=stage_plan.skip_reason,
                    )
                )
                self._log(
                    log,
                    "阶段跳过",
                    stage=stage_plan.stage.value,
                    event="stage_skipped",
                    reason=stage_plan.skip_reason,
                )
                continue

            handler = self._handlers.get(stage_plan.stage)
            if handler is None:
                failed = InternalError(f"缺少阶段处理器：{stage_plan.stage.value}")
                outcomes.append(
                    StageOutcome(stage=stage_plan.stage, status=STATUS_FAILED, error=failed)
                )
                break

            outcome = await self._run_stage(
                config,
                task_id=task_id,
                work_dir=work_dir,
                stage_plan=stage_plan,
                handler=handler,
                log=log,
                progress=progress,
                cancel=cancel,
            )
            outcomes.append(outcome)

            if outcome.status == STATUS_CANCELLED:
                cancelled = True
                break
            if outcome.status == STATUS_FAILED:
                failed = outcome.error
                break

        duration = self._clock() - started
        if cancelled:
            self._log(
                log, "任务取消", event="task_cancelled", task=task_id, elapsed=f"{duration:.1f}s"
            )
        elif failed is not None:
            self._log(
                log,
                "任务失败",
                event="task_finished",
                task=task_id,
                result=STATUS_FAILED,
                duration=f"{duration:.1f}s",
            )
        else:
            self._log(
                log,
                "任务完成",
                event="task_finished",
                task=task_id,
                result=STATUS_SUCCEEDED,
                duration=f"{duration:.1f}s",
            )

        return PipelineRun(
            task_id=task_id,
            outcomes=tuple(outcomes),
            cancelled=cancelled,
            failed=failed,
            duration=duration,
        )

    async def _run_stage(
        self,
        config: TaskConfig,
        *,
        task_id: str,
        work_dir: Path,
        stage_plan: StagePlan,
        handler: StageHandler,
        log: LogSink | None,
        progress: ProgressHub | None,
        cancel: object | None,
    ) -> StageOutcome:
        attempts_allowed = self._retry.stage_retries + 1
        attempt = 1
        started = self._clock()

        while True:
            ctx = StageContext(
                task_id=task_id,
                config=config,
                stage=stage_plan.stage,
                plan=stage_plan,
                work_dir=work_dir,
                attempt=attempt,
                cancel=cancel,  # type: ignore[arg-type]
                log=log,
                progress=progress,
            )
            self._log(
                log,
                "阶段开始",
                stage=stage_plan.stage.value,
                event="stage_started",
                attempt=attempt,
            )
            if progress is not None:
                progress.reset(stage_plan.stage.value)
                progress.report(stage_plan.stage.value, percent=0)

            try:
                await handler.run(ctx)
            except BaseException as exc:  # noqa: BLE001 - 统一记账后再决定去留
                if isinstance(exc, UserCancelledError) or _is_cancelled(cancel):
                    duration = self._clock() - started
                    self._log(
                        log,
                        "阶段取消",
                        stage=stage_plan.stage.value,
                        event="stage_finished",
                        attempt=attempt,
                        result=STATUS_CANCELLED,
                        duration=f"{duration:.1f}s",
                    )
                    return StageOutcome(
                        stage=stage_plan.stage,
                        status=STATUS_CANCELLED,
                        attempts=attempt,
                        duration=duration,
                        error=exc,
                    )

                can_retry = attempt < attempts_allowed and self._retry.accepts(exc)
                if can_retry:
                    cleanup_error = await self._prepare_retry(ctx, log=log)
                    if cleanup_error is not None:
                        duration = self._clock() - started
                        return StageOutcome(
                            stage=stage_plan.stage,
                            status=STATUS_FAILED,
                            attempts=attempt,
                            duration=duration,
                            error=cleanup_error,
                        )
                    self._log(
                        log,
                        "阶段重试",
                        stage=stage_plan.stage.value,
                        event="stage_retry",
                        attempt=attempt,
                        reason=_reason_of(exc),
                    )
                    await self._sleep(self._retry.backoff_for(attempt))
                    attempt += 1
                    continue

                duration = self._clock() - started
                self._log(
                    log,
                    "阶段失败",
                    stage=stage_plan.stage.value,
                    event="stage_finished",
                    attempt=attempt,
                    result=STATUS_FAILED,
                    duration=f"{duration:.1f}s",
                    reason=_reason_of(exc),
                )
                return StageOutcome(
                    stage=stage_plan.stage,
                    status=STATUS_FAILED,
                    attempts=attempt,
                    duration=duration,
                    error=exc,
                )

            duration = self._clock() - started
            if progress is not None:
                progress.report(stage_plan.stage.value, percent=100)
            self._log(
                log,
                "阶段完成",
                stage=stage_plan.stage.value,
                event="stage_finished",
                attempt=attempt,
                result=STATUS_SUCCEEDED,
                duration=f"{duration:.1f}s",
            )
            return StageOutcome(
                stage=stage_plan.stage,
                status=STATUS_SUCCEEDED,
                attempts=attempt,
                duration=duration,
            )

    async def _prepare_retry(
        self, ctx: StageContext, *, log: LogSink | None
    ) -> BaseException | None:
        """重试前的残留清理（挂载类阶段）；返回清理异常表示「不该再重试」。"""
        if not self._retry.cleanup_before_retry or self._cleanup is None:
            return None
        if not _needs_cleanup(ctx.plan):
            return None
        try:
            await self._cleanup(ctx)
        except BaseException as exc:  # noqa: BLE001 - 清理失败即判定不可继续
            self._log(
                log,
                "重试前清理失败",
                stage=ctx.stage.value,
                event="stage_cleanup_failed",
                reason=_reason_of(exc),
            )
            return exc
        return None

    # ------------------------------------------------------------------
    # dry-run
    # ------------------------------------------------------------------

    def dry_run(self, config: TaskConfig, *, work_dir: Path | None = None) -> DryRunReport:
        """列出「将执行/跳过哪些阶段、各阶段要跑什么命令、将复用哪些产物」。

        不创建目录、不写检查点、不触碰磁盘。
        """
        plan = plan_pipeline(config)
        stages: list[DryRunStage] = []

        for stage_plan in plan.stages:
            if stage_plan.skipped:
                stages.append(
                    DryRunStage(
                        stage=stage_plan.stage,
                        will_run=False,
                        skip_reason=stage_plan.skip_reason,
                    )
                )
                continue

            handler = self._handlers.get(stage_plan.stage)
            if handler is None:
                stages.append(
                    DryRunStage(
                        stage=stage_plan.stage,
                        will_run=True,
                        substeps=tuple(sub.name for sub in stage_plan.executed_substeps),
                        handler_missing=True,
                    )
                )
                continue

            ctx = StageContext(
                task_id="dry-run",
                config=config,
                stage=stage_plan.stage,
                plan=stage_plan,
                work_dir=work_dir or Path("work") / "dry-run",
            )
            has_describe, has_reuse = has_dry_run_info(handler)
            commands = tuple(handler.describe(ctx)) if has_describe else ()  # type: ignore[attr-defined]
            reused = tuple(handler.reusable_artifacts(ctx)) if has_reuse else ()  # type: ignore[attr-defined]
            ops_source = getattr(handler, "file_operations", None)
            file_ops = tuple(ops_source(ctx)) if callable(ops_source) else ()
            stages.append(
                DryRunStage(
                    stage=stage_plan.stage,
                    will_run=True,
                    substeps=tuple(sub.name for sub in stage_plan.executed_substeps),
                    commands=commands,
                    reused=reused,
                    file_ops=file_ops,
                )
            )

        return DryRunReport(
            entry=plan.entry,
            start_stage=plan.start_stage,
            stages=tuple(stages),
            work_dir=work_dir,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _log(
        log: LogSink | None, message: str, *, stage: str | None = None, **fields: object
    ) -> None:
        if log is not None:
            log.info("app.pipeline", message, stage=stage, **fields)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def _async_sleep(seconds: float) -> None:
    if seconds <= 0:
        return
    import asyncio

    await asyncio.sleep(seconds)


def _is_cancelled(cancel: object | None) -> bool:
    checker = getattr(cancel, "is_cancelled", None)
    return bool(checker()) if callable(checker) else False


def _reason_of(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    return getattr(code, "value", None) or type(exc).__name__


def _needs_cleanup(stage_plan: StagePlan) -> bool:
    return any(sub.cleanup_before_rerun for sub in stage_plan.executed_substeps)
