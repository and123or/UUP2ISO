"""`run` 命令的业务编排（WP1-T5）。

CLI 只做三件事：解析参数、调这里、把结果变成退出码。所有判断都在本模块，
这样 GUI/CLI 共用同一套「配置 → 计划 → 续跑 → 执行」逻辑（配置 §11）。

流程（顺序即契约）：

1. **加载配置**（V0/V1/V2）——失败即 `CONFIG_*`，退出码 4；
2. **抢单实例 + 建日志**（日志 §2.2）——冲突退 5，且不建日志；
3. **算阶段计划**（T1）与**续跑判定**（T3）——配置/输入变了就拒绝续跑；
4. `--dry-run` 打印阶段/命令/复用清单后退出 0（不写检查点、不建 work 目录）；
5. 否则检查处理器覆盖度，不足则明确报 `not_implemented`（而不是跑到一半崩）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from uup2iso.application.pipeline import (
    PipelineOrchestrator,
    PipelineRun,
    PrepareStageHandler,
    RetryPolicy,
    plan_pipeline,
)
from uup2iso.application.pipeline.handlers import StageHandler
from uup2iso.application.startup import (
    ALREADY_RUNNING_PAYLOAD,
    StartupConflictError,
    StartupReport,
    acquire_instance,
    bootstrap,
    release_instance,
)
from uup2iso.application.task_session import TaskSession
from uup2iso.core.errors import ErrorCode, ExitCode, Uup2IsoError
from uup2iso.core.models.checkpoint import ResumeDecision
from uup2iso.core.models.pipeline import StageKind
from uup2iso.infrastructure.storage.checkpoint_store import CheckpointStore
from uup2iso.infrastructure.storage.config_loader import load_task_config

__all__ = ["RunOptions", "RunOutcome", "build_handlers", "execute_run"]

_NOT_IMPLEMENTED = "not_implemented"
"""处理器未接线的错误码（不是规范码，但必须让用户一眼看懂「为什么跑不了」）。"""


@dataclass(frozen=True, slots=True)
class RunOptions:
    """`run` 的全部输入（**只有配置文件与运行开关**，配置 §11）。"""

    config_path: Path
    dry_run: bool = False
    resume: bool = False
    debug: bool = False


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """一次 `run` 的结果。

    Attributes:
        exit_code: 进程退出码（与 `ExitCode` 一致）。
        payload: 机器可读结果（T6 的 JSON 输出直接用）。
        lines: 人读输出（逐行打印）。
    """

    exit_code: int
    payload: dict[str, object] = field(default_factory=dict)
    lines: tuple[str, ...] = ()


def build_handlers() -> dict[StageKind, StageHandler]:
    """当前已实现（或至少已接线）的阶段处理器。

    WS2/WS3 会往这里加 `Fetch`/`Convert`/`Slim`/… ；`Prepare` 的底层动作也还没接
    （`PrepareOps` 属引擎层），所以现在只在 `--dry-run` 下可用。
    """
    return {StageKind.PREPARE: PrepareStageHandler()}


def execute_run(
    options: RunOptions,
    *,
    handlers: Mapping[StageKind, StageHandler] | None = None,
    log_dir: Path | None = None,
    work_dir: Path | None = None,
    mutex_name: str | None = None,
    session: StartupReport | None = None,
) -> RunOutcome:
    """执行 `run` 命令。

    Args:
        options: 命令行选项。
        handlers: 阶段处理器；``None`` 用 :func:`build_handlers`。
        log_dir / work_dir / mutex_name / session: 测试注入用（生产走默认）。

    Returns:
        :class:`RunOutcome`；`exit_code` 可直接作为进程退出码。
    """
    # 1) 配置
    try:
        loaded = load_task_config(options.config_path)
    except Uup2IsoError as exc:
        return _error(exc.code.value, exc.exit_code, exc.message)

    config = loaded.config
    task_id = config.meta.id or "00000000"

    # 2) 单实例 + 日志
    report = session
    owned_session = report is None
    if report is None:
        guard = acquire_instance(mutex_name) if mutex_name else acquire_instance()
        if guard is None:
            return RunOutcome(
                exit_code=int(ExitCode.ALREADY_RUNNING),
                payload=dict(ALREADY_RUNNING_PAYLOAD),
                lines=("程序已在运行（同一时刻只允许一个实例）",),
            )
        try:
            report = bootstrap(entry="cli", log_dir=log_dir, debug=options.debug, session=guard)
        except StartupConflictError:
            release_instance(guard)
            return RunOutcome(
                exit_code=int(ExitCode.ALREADY_RUNNING),
                payload=dict(ALREADY_RUNNING_PAYLOAD),
            )
        except Uup2IsoError as exc:
            release_instance(guard)
            return _error(exc.code.value, exc.exit_code, exc.message)

    try:
        return _run_with_session(
            options,
            config=config,
            task_id=task_id,
            report=report,
            handlers=handlers or build_handlers(),
            work_dir=work_dir,
        )
    finally:
        if owned_session:
            report.close()


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _run_with_session(
    options: RunOptions,
    *,
    config: object,
    task_id: str,
    report: StartupReport,
    handlers: Mapping[StageKind, StageHandler],
    work_dir: Path | None,
) -> RunOutcome:
    sink = report.sink
    resolved_work = work_dir or _default_work_dir(task_id)
    plan = plan_pipeline(config)  # type: ignore[arg-type]

    lines = [f"任务 {task_id}：入口 {plan.entry}，起点 {plan.start_stage.value}"]
    lines.extend(plan.summary_lines())

    # 3) 续跑判定
    resume_payload: dict[str, object] = {}
    if options.resume:
        session = TaskSession(
            task_id=task_id,
            work_dir=resolved_work,
            config_fingerprint=config.fingerprint(),  # type: ignore[attr-defined]
            store=CheckpointStore(resolved_work),
        )
        resume_plan = session.plan_resume()
        resume_payload = {
            "decision": resume_plan.decision.value,
            "reuseStages": list(resume_plan.reuse_stages),
            "redoStages": list(resume_plan.redo_stages),
            "reuseArtifacts": list(resume_plan.reuse_artifacts),
        }
        lines.append(f"续跑判定：{resume_plan.decision.value}（{resume_plan.message}）")
        if resume_plan.decision is ResumeDecision.REJECT_CONFIG_CHANGED:
            return RunOutcome(
                exit_code=int(ExitCode.INVALID_ARGUMENT),
                payload={
                    "error": ErrorCode.CONFIG_CHANGED.value,
                    "code": int(ExitCode.INVALID_ARGUMENT),
                    "message": resume_plan.message,
                    **resume_payload,
                },
                lines=tuple(lines),
            )
        if resume_plan.decision is ResumeDecision.ALREADY_SUCCEEDED:
            # 已完成：不是错误，直接 0 退出并提示（编排 §9）
            return RunOutcome(
                exit_code=int(ExitCode.SUCCESS),
                payload={
                    "alreadySucceeded": True,
                    "message": resume_plan.message,
                    **resume_payload,
                },
                lines=tuple(lines),
            )
        if resume_plan.reuse_stages:
            lines.append(
                f"将复用 {len(resume_plan.reuse_stages)} 个阶段 / "
                f"{len(resume_plan.reuse_artifacts)} 个产物"
            )

    # 4) dry-run
    orchestrator = PipelineOrchestrator(handlers=handlers, retry=RetryPolicy())
    if options.dry_run:
        report_dry = orchestrator.dry_run(config, work_dir=resolved_work)  # type: ignore[arg-type]
        lines = [f"任务 {task_id}：入口 {report_dry.entry}，起点 {report_dry.start_stage.value}"]
        lines.extend(report_dry.lines()[1:])
        commands = report_dry.command_lines()
        if commands:
            lines.append("将执行的命令：")
            lines.extend(f"  {command}" for command in commands)
        sink.info("app.cli", "dry-run 完成", event="dry_run", task=task_id)
        file_lines = report_dry.file_lines()
        if file_lines:
            lines.append("将读写的文件：")
            lines.extend(file_lines)
        return RunOutcome(
            exit_code=int(ExitCode.SUCCESS),
            payload={
                "task": task_id,
                "dryRun": True,
                "logLevel": report.level,
                "stages": [
                    {
                        "stage": stage.stage.value,
                        "action": _stage_action(stage),
                        "reason": stage.skip_reason,
                        "substeps": list(stage.substeps),
                        "commands": list(stage.commands),
                        "reused": list(stage.reused),
                    }
                    for stage in report_dry.stages
                ],
                "commands": list(commands),
                "files": [
                    {"stage": stage_name, "action": op.action, "path": op.path}
                    for stage_name, op in report_dry.file_operations
                ],
                **resume_payload,
            },
            lines=tuple(lines),
        )

    # 5) 处理器覆盖度
    missing = [
        stage.value for stage in (item.stage for item in plan.executed) if stage not in handlers
    ]
    if missing:
        message = (
            "以下阶段的处理器尚未接线，无法执行：" + "、".join(missing) + "（当前仅支持 --dry-run）"
        )
        sink.warn("app.cli", message, event="not_implemented", task=task_id)
        return RunOutcome(
            exit_code=int(ExitCode.INVALID_ARGUMENT),
            payload={
                "error": _NOT_IMPLEMENTED,
                "code": int(ExitCode.INVALID_ARGUMENT),
                "message": message,
                "missingStages": missing,
                **resume_payload,
            },
            lines=tuple(lines) + (message,),
        )

    run: PipelineRun = _as_sync(
        orchestrator.run(
            config,  # type: ignore[arg-type]
            task_id=task_id,
            work_dir=resolved_work,
            log=sink,
            cancel=None,
        )
    )
    lines.append(f"结果：{'成功' if run.succeeded else '失败'}（{run.exit_code}）")
    return RunOutcome(
        exit_code=run.exit_code,
        payload={
            "task": task_id,
            "result": "success" if run.succeeded else "failed",
            "stages": [
                {"stage": outcome.stage.value, "status": outcome.status} for outcome in run.outcomes
            ],
            **resume_payload,
        },
        lines=tuple(lines),
    )


def _stage_action(stage: object) -> str:
    """把 dry-run 阶段的三种状态映射成稳定字符串（T6 的 JSON 契约）。"""
    if getattr(stage, "handler_missing", False):
        return "todo"
    return "run" if getattr(stage, "will_run", False) else "skip"


def _error(code: str, exit_code: int, message: str) -> RunOutcome:
    return RunOutcome(
        exit_code=exit_code,
        payload={"error": code, "code": exit_code, "message": message},
        lines=(message,),
    )


def _default_work_dir(task_id: str) -> Path:
    from uup2iso.application.startup import default_log_dir

    return default_log_dir().parent / "work" / task_id


def _as_sync(coro: object) -> PipelineRun:
    import asyncio

    return asyncio.run(coro)  # type: ignore[arg-type]
