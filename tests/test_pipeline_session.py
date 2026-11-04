"""P1-2 门禁：编排接会话（G1）、心跳（G2）、Ctrl+C 取消（G3）。

G1/G2 用假会话记录调用序列（不落盘、不依赖真实时间）；
G3 直接测令牌本体，避免在测试进程里真发 SIGINT。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from uup2iso.application.cancel import SignalCancelToken
from uup2iso.application.pipeline import PipelineOrchestrator, plan_pipeline
from uup2iso.application.pipeline.orchestrator import RetryPolicy
from uup2iso.core.models import default_task_config
from uup2iso.core.models.enums import EntryPoint, StageState, TaskState


class FakeSession:
    """记录编排器回调顺序。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.beats = 0

    def begin_stage(self, stage: str) -> None:
        self.calls.append(("begin", stage, ""))

    def end_stage(self, stage: str, status: StageState, *, skip_reason: str | None = None) -> None:
        self.calls.append(("end", stage, f"{status.value}:{skip_reason or ''}"))

    def heartbeat(self) -> None:
        self.beats += 1

    def finish(self, status: TaskState, *, result: str = "") -> None:
        self.calls.append(("finish", status.value, result))


class OkHandler:
    """成功处理器；故意 `await sleep(0)` 让心跳任务有机会被调度。"""

    def __init__(self, stage: object, *, delay: float = 0.0) -> None:
        self.stage = stage
        self._delay = delay

    async def run(self, ctx: object) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)
        return


def make_config(tmp_path: Path) -> object:
    config = default_task_config(mode=EntryPoint.UUP_FOLDER)
    config.input.start_stage = "Convert"
    config.input.uup_folder = {"path": str(tmp_path / "uups")}  # type: ignore[assignment]
    (tmp_path / "uups").mkdir(exist_ok=True)
    (tmp_path / "uups" / "a.esd").write_bytes(b"ESD")
    (tmp_path / "uups" / "AggregatedMetadata.cab").write_bytes(b"CAB")
    config.output = {"dir": str(tmp_path), "name": "x", "type": "wim"}  # type: ignore[assignment]
    return config


def stage_kinds(config: object) -> list[object]:
    return [item.stage for item in plan_pipeline(config).executed]  # type: ignore[arg-type]


# --- G1：阶段级检查点 -------------------------------------------------------


def test_session_records_stage_transitions(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    session = FakeSession()
    handlers = {kind: OkHandler(kind) for kind in stage_kinds(config)}
    orchestrator = PipelineOrchestrator(handlers=handlers, session=session)

    asyncio.run(
        orchestrator.run(config, task_id="t1", work_dir=tmp_path / "work")  # type: ignore[arg-type]
    )

    begins = [call[1] for call in session.calls if call[0] == "begin"]
    ends = [(call[1], call[2]) for call in session.calls if call[0] == "end"]
    assert begins, "每个执行阶段都要 begin"
    assert any(status.startswith("succeeded") for _, status in ends)
    assert all(status.startswith(("succeeded", "skipped")) for _, status in ends), (
        "不应出现 failed/cancelled"
    )
    assert session.calls[-1][0] == "finish"
    assert session.calls[-1][1] == TaskState.SUCCEEDED.value


def test_skipped_stage_is_recorded_with_reason(tmp_path: Path) -> None:
    config = make_config(tmp_path)  # Fetch 在入口 B 应为 skipped
    session = FakeSession()
    handlers = {kind: OkHandler(kind) for kind in stage_kinds(config)}
    orchestrator = PipelineOrchestrator(handlers=handlers, session=session)

    asyncio.run(
        orchestrator.run(config, task_id="t1", work_dir=tmp_path / "work")  # type: ignore[arg-type]
    )

    skipped = [call for call in session.calls if call[0] == "end" and "skipped" in call[2]]
    assert any("Fetch" in call[1] for call in skipped)
    assert any("input_satisfied" in call[2] for call in skipped)


def test_failed_stage_marks_task_failed(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    session = FakeSession()

    class Boom(OkHandler):
        async def run(self, ctx: object) -> None:
            raise RuntimeError("boom")

    handlers = {kind: OkHandler(kind) for kind in stage_kinds(config)}
    first = stage_kinds(config)[0]
    handlers[first] = Boom(first)
    orchestrator = PipelineOrchestrator(
        handlers=handlers, session=session, retry=RetryPolicy(stage_retries=0)
    )

    asyncio.run(
        orchestrator.run(config, task_id="t1", work_dir=tmp_path / "work")  # type: ignore[arg-type]
    )

    assert session.calls[-1][:2] == ("finish", TaskState.FAILED.value)


def test_no_session_is_allowed(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    handlers = {kind: OkHandler(kind) for kind in stage_kinds(config)}
    orchestrator = PipelineOrchestrator(handlers=handlers)

    run = asyncio.run(
        orchestrator.run(config, task_id="t1", work_dir=tmp_path / "work")  # type: ignore[arg-type]
    )
    assert run.succeeded


# --- G2：心跳 ---------------------------------------------------------------


def test_heartbeat_runs_periodically(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    session = FakeSession()
    ticks: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        ticks.append(seconds)
        if len(ticks) >= 3:
            raise asyncio.CancelledError

    handlers = {kind: OkHandler(kind, delay=0.02) for kind in stage_kinds(config)}
    orchestrator = PipelineOrchestrator(
        handlers=handlers,
        session=session,
        heartbeat_interval=0.01,
        sleep=fake_sleep,
    )

    asyncio.run(
        orchestrator.run(config, task_id="t1", work_dir=tmp_path / "work")  # type: ignore[arg-type]
    )

    assert session.beats >= 1, "心跳应至少触发一次"
    assert all(second == pytest.approx(0.01) for second in ticks), "按注入间隔睡眠"


def test_no_heartbeat_without_session(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    handlers = {kind: OkHandler(kind) for kind in stage_kinds(config)}
    orchestrator = PipelineOrchestrator(handlers=handlers, sleep=fake_sleep)

    asyncio.run(
        orchestrator.run(config, task_id="t1", work_dir=tmp_path / "work")  # type: ignore[arg-type]
    )
    assert slept == [], "没有会话就不该有心跳"


# --- G3：取消令牌 -----------------------------------------------------------


def test_cancel_token_flags_cancelled() -> None:
    token = SignalCancelToken()
    assert token.is_cancelled() is False
    token.cancel()
    assert token.is_cancelled() is True


def test_installed_restores_previous_handler() -> None:
    import signal

    before = signal.getsignal(signal.SIGINT)
    token = SignalCancelToken()
    with token.installed():
        assert signal.getsignal(signal.SIGINT) != before
    assert signal.getsignal(signal.SIGINT) is before


def test_first_sigint_requests_cancel_second_raises() -> None:
    token = SignalCancelToken()
    with token.installed():
        token._handle(2, None)  # 第一次：只设标志
        assert token.is_cancelled() is True
        with pytest.raises(KeyboardInterrupt):
            token._handle(2, None)  # 第二次：交还默认行为


def test_cancelled_run_stops_early(tmp_path: Path) -> None:
    """编排器看到取消令牌后立即退出（不再执行后续阶段）。"""
    config = make_config(tmp_path)
    session = FakeSession()
    token = SignalCancelToken()
    token.cancel()

    handlers = {kind: OkHandler(kind) for kind in stage_kinds(config)}
    orchestrator = PipelineOrchestrator(handlers=handlers, session=session)

    run = asyncio.run(
        orchestrator.run(
            config,  # type: ignore[arg-type]
            task_id="t1",
            work_dir=tmp_path / "work",
            cancel=token,
        )
    )

    assert run.exit_code == 3
    assert session.calls[-1][:2] == ("finish", TaskState.CANCELLED.value)


# --- G4：dry-run 文件操作覆盖 ----------------------------------------------


def test_wired_handlers_expose_file_operations(tmp_path: Path) -> None:
    """已接线的阶段都应能给出 `file_operations`（dry-run 才能列"将读写的文件"）。"""
    from uup2iso.application.cli_runner import build_handlers
    from uup2iso.application.pipeline import StageContext
    from uup2iso.core.models.pipeline import StageKind

    config = make_config(tmp_path)
    wim = tmp_path / "work" / "convert" / "ISOFOLDER" / "sources" / "install.wim"
    wim.parent.mkdir(parents=True)
    wim.write_bytes(b"WIM")
    handlers = build_handlers(config)  # type: ignore[arg-type]

    checked = 0
    for kind in (StageKind.PREPARE, StageKind.CONVERT, StageKind.OUTPUT):
        handler = handlers.get(kind)
        assert handler is not None, f"{kind} 未接线"
        plan = plan_pipeline(config).stage(kind)  # type: ignore[arg-type]
        ctx = StageContext(
            task_id="t1",
            config=config,  # type: ignore[arg-type]
            stage=kind,
            plan=plan,
            work_dir=tmp_path / "work",
        )
        assert callable(getattr(handler, "file_operations", None)), f"{kind} 缺 file_operations"
        assert handler.file_operations(ctx), f"{kind} 的 file_operations 为空"
        checked += 1
    assert checked == 3
