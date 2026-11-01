"""PipelineOrchestrator 门禁（编排 §7、§8、§11、§12）。

验收重点：白名单重试（2 次后停）、不可重试直停（4）、取消保留产物（3）、
`--dry-run` 列阶段/命令/复用且不建目录。
"""

from __future__ import annotations

from pathlib import Path

from uup2iso.application.pipeline import (
    PipelineOrchestrator,
    RetryPolicy,
    StageContext,
    is_retryable,
)
from uup2iso.core.errors import (
    ConfigError,
    ErrorCode,
    StageExecutionError,
    UserCancelledError,
)
from uup2iso.core.models import default_task_config
from uup2iso.core.models.config import TaskConfig
from uup2iso.core.models.enums import EntryPoint
from uup2iso.core.models.pipeline import StageKind
from uup2iso.infrastructure.logging import LogSink
from uup2iso.infrastructure.progress import ProgressHub

ALL_STAGES = tuple(StageKind)


class Token:
    """可手动触发的取消令牌。"""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise UserCancelledError()


class FakeHandler:
    """可控的阶段处理器：前 ``failures`` 次抛错，之后成功。"""

    def __init__(
        self,
        stage: StageKind,
        *,
        failures: int = 0,
        error: BaseException | None = None,
        commands: tuple[str, ...] = (),
        reused: tuple[str, ...] = (),
        on_run: object | None = None,
    ) -> None:
        self.stage = stage
        self.failures = failures
        self.error = error or StageExecutionError("boom", code=ErrorCode.TOOL_FAILED)
        self.attempts: list[int] = []
        self._commands = commands
        self._reused = reused
        self._on_run = on_run

    async def run(self, ctx: StageContext) -> None:
        self.attempts.append(ctx.attempt)
        if callable(self._on_run):
            self._on_run(ctx)  # type: ignore[operator]
        if self.failures > 0:
            self.failures -= 1
            raise self.error

    def describe(self, ctx: StageContext) -> tuple[str, ...]:
        return self._commands

    def reusable_artifacts(self, ctx: StageContext) -> tuple[str, ...]:
        return self._reused


async def _no_sleep(_seconds: float) -> None:
    return None


def make_config(*, dry_run: bool = False) -> TaskConfig:
    """入口 C + 四个选项阶段全开，让 6 个阶段都要执行。"""
    config = default_task_config(mode=EntryPoint.IMAGE)
    config.input.start_stage = "Slim"
    config.input.image = {  # type: ignore[assignment]
        "path": "D:/win11.iso",
        "type": "iso",
        "editions": [1],
    }
    config.slim = {"ruleIds": ["FR4-SL-A01"]}  # type: ignore[assignment]
    config.optimize = {"ruleIds": ["FR5-OP-T60"]}  # type: ignore[assignment]
    config.integrate = {"updates": {"paths": ["D:/Updates"]}}  # type: ignore[assignment]
    config.unattend = {"enabled": True}  # type: ignore[assignment]
    if dry_run:
        config.execution = {"dryRun": True, "resume": False}  # type: ignore[assignment]
    return config


def all_handlers(
    overrides: dict[StageKind, FakeHandler] | None = None,
) -> dict[StageKind, FakeHandler]:
    handlers = {stage: FakeHandler(stage) for stage in ALL_STAGES}
    if overrides:
        handlers.update(overrides)
    return handlers


def make_sink(tmp_path: Path) -> LogSink:
    return LogSink.open(tmp_path / "logs", entry="cli", level="DEBUG")


EXECUTED = (
    StageKind.PREPARE,
    StageKind.SLIM,
    StageKind.OPTIMIZE,
    StageKind.INTEGRATE,
    StageKind.UNATTEND,
    StageKind.OUTPUT,
)


# --- 正常路径 ---------------------------------------------------------------


def run(coro: object) -> object:
    import asyncio

    return asyncio.run(coro)  # type: ignore[arg-type]


def test_executes_stages_in_order_and_succeeds(tmp_path: Path) -> None:
    handlers = all_handlers()
    orchestrator = PipelineOrchestrator(handlers=handlers, sleep=_no_sleep)

    run_result = run(
        orchestrator.run(make_config(), task_id="7f3a1b2c", work_dir=tmp_path / "work")
    )

    assert run_result.succeeded and run_result.exit_code == 0
    assert run_result.attempted_stages == EXECUTED
    assert all(len(handlers[stage].attempts) == 1 for stage in EXECUTED)


def test_skipped_stages_do_not_call_handlers(tmp_path: Path) -> None:
    handlers = all_handlers()
    orchestrator = PipelineOrchestrator(handlers=handlers, sleep=_no_sleep)

    run_result = run(orchestrator.run(make_config(), task_id="t1", work_dir=tmp_path))

    assert handlers[StageKind.FETCH].attempts == []
    assert handlers[StageKind.CONVERT].attempts == []
    fetched = run_result.outcome(StageKind.FETCH)
    assert fetched is not None and fetched.status == "skipped"
    assert fetched.skip_reason == "input_satisfied"


# --- 重试白名单 -------------------------------------------------------------


def test_retries_whitelisted_failure_then_succeeds(tmp_path: Path) -> None:
    slim = FakeHandler(StageKind.SLIM, failures=2)
    sink = make_sink(tmp_path)
    orchestrator = PipelineOrchestrator(
        handlers=all_handlers({StageKind.SLIM: slim}),
        retry=RetryPolicy(stage_retries=2, backoff_seconds=(0.0,)),
        sleep=_no_sleep,
    )
    try:
        run_result = run(orchestrator.run(make_config(), task_id="t2", work_dir=tmp_path, log=sink))
    finally:
        sink.close()

    assert run_result.succeeded
    assert slim.attempts == [1, 2, 3]
    text = sink.paths.run.read_text(encoding="utf-8")
    assert text.count("event=stage_retry") == 2


def test_retry_exhausted_reports_failure(tmp_path: Path) -> None:
    slim = FakeHandler(StageKind.SLIM, failures=99)
    sink = make_sink(tmp_path)
    orchestrator = PipelineOrchestrator(
        handlers=all_handlers({StageKind.SLIM: slim}),
        retry=RetryPolicy(stage_retries=2, backoff_seconds=(0.0,)),
        sleep=_no_sleep,
    )
    try:
        run_result = run(orchestrator.run(make_config(), task_id="t3", work_dir=tmp_path, log=sink))
    finally:
        sink.close()

    assert not run_result.succeeded
    assert run_result.exit_code == 2, "工具失败属阶段失败"
    assert slim.attempts == [1, 2, 3], "阶段级兜底 2 次 = 共 3 次尝试"
    text = sink.paths.run.read_text(encoding="utf-8")
    assert text.count("event=stage_retry") == 2
    assert "result=failed" in text


def test_non_retryable_error_stops_immediately(tmp_path: Path) -> None:
    prepare = FakeHandler(
        StageKind.PREPARE,
        failures=99,
        error=ConfigError("配置坏了", code=ErrorCode.CONFIG_SEMANTIC_INVALID),
    )
    sink = make_sink(tmp_path)
    orchestrator = PipelineOrchestrator(
        handlers=all_handlers({StageKind.PREPARE: prepare}),
        retry=RetryPolicy(stage_retries=2, backoff_seconds=(0.0,)),
        sleep=_no_sleep,
    )
    try:
        run_result = run(orchestrator.run(make_config(), task_id="t4", work_dir=tmp_path, log=sink))
    finally:
        sink.close()

    assert prepare.attempts == [1], "配置错误不重试"
    assert run_result.exit_code == 4
    assert "event=stage_retry" not in sink.paths.run.read_text(encoding="utf-8")


def test_retry_policy_whitelist_semantics() -> None:
    assert is_retryable(StageExecutionError("x", code=ErrorCode.TOOL_FAILED))
    assert not is_retryable(UserCancelledError())
    assert not is_retryable(ConfigError("x", code=ErrorCode.CONFIG_SCHEMA_INVALID))
    assert not is_retryable(ValueError("未知"))


# --- 重试前清理 -------------------------------------------------------------


def test_cleanup_hook_runs_before_retry_on_mount_stage(tmp_path: Path) -> None:
    slim = FakeHandler(StageKind.SLIM, failures=1)
    cleaned: list[str] = []

    async def cleanup(ctx: StageContext) -> None:
        cleaned.append(ctx.stage.value)

    orchestrator = PipelineOrchestrator(
        handlers=all_handlers({StageKind.SLIM: slim}),
        retry=RetryPolicy(stage_retries=2, backoff_seconds=(0.0,)),
        cleanup=cleanup,
        sleep=_no_sleep,
    )
    run_result = run(orchestrator.run(make_config(), task_id="t5", work_dir=tmp_path))

    assert run_result.succeeded
    assert cleaned == ["Slim"], "挂载类阶段重试前必须清理残留"


def test_cleanup_failure_aborts_retry(tmp_path: Path) -> None:
    slim = FakeHandler(StageKind.SLIM, failures=5)

    async def cleanup(_ctx: StageContext) -> None:
        raise RuntimeError("卸载失败，残留清不掉")

    orchestrator = PipelineOrchestrator(
        handlers=all_handlers({StageKind.SLIM: slim}),
        retry=RetryPolicy(stage_retries=2, backoff_seconds=(0.0,)),
        cleanup=cleanup,
        sleep=_no_sleep,
    )
    run_result = run(orchestrator.run(make_config(), task_id="t6", work_dir=tmp_path))

    assert not run_result.succeeded
    assert slim.attempts == [1], "清理失败后不再重试"
    assert run_result.exit_code == 2


def test_cleanup_not_called_for_non_mount_stage(tmp_path: Path) -> None:
    output = FakeHandler(StageKind.OUTPUT, failures=1)
    cleaned: list[str] = []

    async def cleanup(ctx: StageContext) -> None:
        cleaned.append(ctx.stage.value)

    orchestrator = PipelineOrchestrator(
        handlers=all_handlers({StageKind.OUTPUT: output}),
        retry=RetryPolicy(stage_retries=2, backoff_seconds=(0.0,)),
        cleanup=cleanup,
        sleep=_no_sleep,
    )
    run_result = run(orchestrator.run(make_config(), task_id="t7", work_dir=tmp_path))

    assert run_result.succeeded
    assert cleaned == [], "Output 没有挂载子步骤，不该触发清理"


# --- 取消 -------------------------------------------------------------------


def test_cancel_before_start_runs_nothing(tmp_path: Path) -> None:
    handlers = all_handlers()
    token = Token()
    token.cancel()
    orchestrator = PipelineOrchestrator(handlers=handlers, sleep=_no_sleep)

    run_result = run(orchestrator.run(make_config(), task_id="t8", work_dir=tmp_path, cancel=token))

    assert run_result.cancelled
    assert run_result.exit_code == 3
    assert all(not handler.attempts for handler in handlers.values())


def test_cancel_mid_run_keeps_artifacts_and_stops(tmp_path: Path) -> None:
    work = tmp_path / "work"

    def _cancel_after_work(ctx: StageContext) -> None:
        (ctx.work_dir).mkdir(parents=True, exist_ok=True)
        (ctx.work_dir / "install.wim").write_bytes(b"partial")
        token.cancel()

    token = Token()
    prepare = FakeHandler(StageKind.PREPARE, on_run=_cancel_after_work)
    orchestrator = PipelineOrchestrator(
        handlers=all_handlers({StageKind.PREPARE: prepare}), sleep=_no_sleep
    )

    run_result = run(orchestrator.run(make_config(), task_id="t9", work_dir=work, cancel=token))

    assert run_result.cancelled and run_result.exit_code == 3
    assert (work / "install.wim").is_file(), "取消必须保留中间产物（编排 §8）"
    # 取消发生在 Prepare 结束时，因此计划里的**下一个阶段**被标 cancelled
    cancelled = [item for item in run_result.outcomes if item.status == "cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].stage is StageKind.FETCH
    prepare_outcome = run_result.outcome(StageKind.PREPARE)
    assert prepare_outcome is not None and prepare_outcome.status == "succeeded"


def test_handler_raising_cancel_is_treated_as_cancelled(tmp_path: Path) -> None:
    """处理器内部发现取消（工具被终止）时，按取消处理而不是失败。"""
    prepare = FakeHandler(StageKind.PREPARE, failures=1, error=UserCancelledError())
    orchestrator = PipelineOrchestrator(
        handlers=all_handlers({StageKind.PREPARE: prepare}), sleep=_no_sleep
    )

    run_result = run(orchestrator.run(make_config(), task_id="t10", work_dir=tmp_path))

    assert run_result.cancelled and run_result.exit_code == 3
    assert prepare.attempts == [1], "取消不重试"


# --- 缺失处理器 -------------------------------------------------------------


def test_missing_handler_fails_instead_of_silent_skip(tmp_path: Path) -> None:
    handlers = all_handlers()
    handlers.pop(StageKind.SLIM)
    orchestrator = PipelineOrchestrator(handlers=handlers, sleep=_no_sleep)

    run_result = run(orchestrator.run(make_config(), task_id="t11", work_dir=tmp_path))

    assert not run_result.succeeded
    assert run_result.exit_code == 2
    slim = run_result.outcome(StageKind.SLIM)
    assert slim is not None and slim.status == "failed"
    assert "缺少阶段处理器" in str(slim.error)
    assert run_result.outcome(StageKind.OPTIMIZE) is None, "失败后不再往下跑"


# --- 进度与事件 -------------------------------------------------------------


def test_progress_reports_stage_boundaries(tmp_path: Path) -> None:
    updates: list[tuple[str, float | None]] = []
    hub = ProgressHub(min_interval=0.0)
    hub.subscribe(lambda update: updates.append((update.stage, update.percent)))
    orchestrator = PipelineOrchestrator(handlers=all_handlers(), sleep=_no_sleep)

    run(orchestrator.run(make_config(), task_id="t12", work_dir=tmp_path, progress=hub))

    assert ("Prepare", 0) in updates
    assert ("Prepare", 100) in updates
    assert ("Slim", 100) in updates
    assert all(stage not in {"Fetch", "Convert"} for stage, _ in updates), "跳过的阶段不进进度"


def test_log_events_follow_spec(tmp_path: Path) -> None:
    sink = make_sink(tmp_path)
    orchestrator = PipelineOrchestrator(handlers=all_handlers(), sleep=_no_sleep)
    try:
        run(orchestrator.run(make_config(), task_id="7f3a1b2c", work_dir=tmp_path, log=sink))
    finally:
        sink.close()

    text = sink.paths.run.read_text(encoding="utf-8")
    assert "event=task_started" in text
    assert "task=7f3a1b2c" in text
    assert "fingerprint=" in text
    assert "event=stage_skipped" in text and "reason=input_satisfied" in text
    assert "event=stage_started" in text
    assert "event=stage_finished" in text and "result=succeeded" in text
    assert "event=task_finished" in text


# --- dry-run ---------------------------------------------------------------


def test_dry_run_lists_stages_commands_and_reuse(tmp_path: Path) -> None:
    handlers = all_handlers(
        {
            StageKind.SLIM: FakeHandler(
                StageKind.SLIM,
                commands=("dism /Image:... /Remove-Package",),
                reused=("install.wim",),
            ),
            StageKind.OUTPUT: FakeHandler(StageKind.OUTPUT, commands=("oscdimg -m -o -u2 ...",)),
        }
    )
    orchestrator = PipelineOrchestrator(handlers=handlers, sleep=_no_sleep)
    work = tmp_path / "work"

    report = orchestrator.dry_run(make_config(), work_dir=work)

    lines = report.lines()
    assert lines[0].startswith("entry=image")
    assert any("reason=input_satisfied" in line for line in lines)
    assert any(line.startswith("[run ] Slim") and "reuse=install.wim" in line for line in lines)
    assert any("oscdimg" in command for command in report.command_lines())
    assert report.executed, "dry-run 应列出将执行的阶段"


def test_dry_run_creates_nothing(tmp_path: Path) -> None:
    orchestrator = PipelineOrchestrator(handlers=all_handlers(), sleep=_no_sleep)
    work = tmp_path / "work"

    orchestrator.dry_run(make_config(dry_run=True), work_dir=work)

    assert not work.exists(), "dry-run 不得创建 work 目录（编排 §12-11）"


def test_dry_run_marks_missing_handler_as_todo(tmp_path: Path) -> None:
    orchestrator = PipelineOrchestrator(handlers={}, sleep=_no_sleep)
    report = orchestrator.dry_run(make_config(), work_dir=tmp_path / "work")

    todo = [line for line in report.lines() if line.startswith("[TODO]")]
    assert any("Prepare" in line for line in todo)
