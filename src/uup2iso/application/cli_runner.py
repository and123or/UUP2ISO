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

from uup2iso.application.cancel import SignalCancelToken
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
from uup2iso.core.models.enums import EntryPoint, TaskState
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


def build_handlers(config: object | None = None) -> dict[StageKind, StageHandler]:
    """当前已接线（或已实现）的阶段处理器。

    * `Prepare` —— 处理器在，但底层 `PrepareOps` 仍缺（引擎层 WS3）；
    * `Fetch` —— 仅入口 A 需要；会现场构造数据源与 aria2c 下载器；
    * 其余阶段待 WS3。
    """
    handlers: dict[StageKind, StageHandler] = {StageKind.PREPARE: PrepareStageHandler()}
    if (
        config is not None
        and getattr(getattr(config, "input", None), "mode", None) == EntryPoint.ONLINE
    ):
        handlers[StageKind.FETCH] = _make_fetch_handler(config)
    if config is not None and getattr(getattr(config, "input", None), "mode", None) in (
        EntryPoint.ONLINE,
        EntryPoint.UUP_FOLDER,
    ):
        handlers[StageKind.CONVERT] = _make_convert_handler()
    if config is not None:
        handlers[StageKind.OUTPUT] = _make_output_handler()
    return handlers


def _make_output_handler() -> StageHandler:
    """Output：暂存 → 校验 → 原子提交 → 伴生（需要 wimlib 做 L2/L3）。"""
    from uup2iso.application.pipeline.handlers import OutputStageHandler
    from uup2iso.infrastructure.process.runner import ProcessRunner

    root = Path(__file__).resolve().parents[3]
    return OutputStageHandler(runner=ProcessRunner(), tools_dir=root / "tools")


def _make_convert_handler() -> StageHandler:
    """Convert：编排转换器（不自建聚合），需要 tools 目录与进程执行器。"""
    from uup2iso.application.pipeline.handlers import ConvertStageHandler
    from uup2iso.infrastructure.process.runner import ProcessRunner

    root = Path(__file__).resolve().parents[3]
    return ConvertStageHandler(tools_dir=root / "tools", runner=ProcessRunner())


def _make_fetch_handler(config: object) -> StageHandler:
    """入口 A 的 Fetch：数据源 + aria2c 下载器（组合根职责）。"""
    from urllib.parse import urlsplit

    from uup2iso.application.pipeline.handlers import FetchStageHandler
    from uup2iso.infrastructure.download import Aria2Downloader
    from uup2iso.infrastructure.process.runner import ProcessRunner
    from uup2iso.infrastructure.source import CachedSource, SourceCache, UupDumpSource
    from uup2iso.infrastructure.tools.lock import load_lock
    from uup2iso.infrastructure.tools.provider import ToolProvider

    root = Path(__file__).resolve().parents[3]
    source_cfg = getattr(config, "source", None)
    base_url = str(getattr(source_cfg, "base_url", "https://uupdump.net/"))
    lock = load_lock(root / "tools" / "tools.lock.json")

    def _downloader(work_dir: Path) -> Aria2Downloader:
        runner = ProcessRunner()
        provider = ToolProvider(
            tools_dir=root / "tools",
            lock=lock.lock,
            runner=runner,
            search_path=False,
            cache_path=root / "config" / "tool_caps.json",
        )
        return Aria2Downloader(provider=provider, runner=runner, work_dir=work_dir)

    cache = SourceCache(root / "cache" / "source")
    host = urlsplit(base_url).hostname or "uupdump.net"
    source = CachedSource(UupDumpSource(base_url=base_url), cache, host=host)
    return FetchStageHandler(source=source, downloader_factory=_downloader)


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
            handlers=handlers if handlers is not None else build_handlers(config),
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
    session = TaskSession(
        task_id=task_id,
        work_dir=resolved_work,
        config_fingerprint=config.fingerprint(),  # type: ignore[attr-defined]
        store=CheckpointStore(resolved_work),
    )
    resume_payload: dict[str, object] = {}
    if options.resume:
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

    # G1：编排器持有会话（阶段级检查点 + 心跳）；G3：Ctrl+C → 取消令牌
    orchestrator = PipelineOrchestrator(handlers=handlers, retry=RetryPolicy(), session=session)
    token = SignalCancelToken()
    if options.resume:
        session.load()
        session.begin_resume()  # 沿用原指纹与已完成阶段
    else:
        session.start()  # 新任务：重置检查点

    with token.installed():
        try:
            run: PipelineRun = _as_sync(
                orchestrator.run(
                    config,  # type: ignore[arg-type]
                    task_id=task_id,
                    work_dir=resolved_work,
                    log=sink,
                    cancel=token,
                )
            )
        except KeyboardInterrupt:
            session.finish(TaskState.CANCELLED)
            sink.warn("app.cli", "用户取消", event="task_cancelled", task=task_id)
            message = "已取消（保留中间产物，可用 --resume 继续）"
            return RunOutcome(
                exit_code=int(ExitCode.CANCELLED),
                payload={"task": task_id, "result": "cancelled", "code": int(ExitCode.CANCELLED)},
                lines=tuple(lines) + (message,),
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
