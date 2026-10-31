"""ProcessRunner 门禁（进程 §3–§9、§11）。

全部用夹具回放（``FakeBackend``），不依赖真实镜像/网络/外部工具。
异步用例统一用 ``asyncio.run`` 驱动——项目依赖里没有 pytest-asyncio，
为一个测试引入插件不值得。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from uup2iso.core.errors import ExitCode
from uup2iso.core.errors.base import UserCancelledError
from uup2iso.core.errors.codes import ErrorCode
from uup2iso.core.models.enums import ToolKind
from uup2iso.infrastructure.process.backend import (
    FakeBackend,
    RunSpec,
    ScriptedStep,
    SpawnedProcess,
    load_script,
)
from uup2iso.infrastructure.process.classifier import LineClassifier
from uup2iso.infrastructure.process.errors import (
    PermissionDeniedError,
    ToolExecutionError,
    ToolMissingError,
    redact_command,
)
from uup2iso.infrastructure.process.events import EventKind, ProcessEvent
from uup2iso.infrastructure.process.runner import ConcurrencyGate, ProcessRunner

FIXTURES = Path(__file__).parent / "fixtures" / "process"
CWD = Path(__file__).parent


async def _no_sleep(_seconds: float) -> None:
    """取消流程里的 5s/2s 等待在测试中置零。"""


def make_runner(backend: FakeBackend, **kwargs: object) -> ProcessRunner:
    return ProcessRunner(
        backend=backend,
        classifier=LineClassifier(),
        sleep=_no_sleep,
        **kwargs,
    )


def run_sync(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


class _Token:
    """可预置/延后触发的取消令牌。"""

    def __init__(self, *, cancel_after: int = 0) -> None:
        self._calls = 0
        self._after = cancel_after
        self._cancelled = False

    def is_cancelled(self) -> bool:
        self._calls += 1
        if self._calls > self._after:
            self._cancelled = True
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise RuntimeError("cancelled")


def spec_for(tool: ToolKind = ToolKind.DISM, stage: str = "Integrate") -> RunSpec:
    return RunSpec(tool=tool, args=("dism.exe", "/Add-Package"), cwd=CWD, stage=stage)


# --- 正常路径 ---------------------------------------------------------------


def test_fixture_replay_success() -> None:
    """DISM 夹具：进度行不写日志、结果行可作结论依据。"""
    script, exit_code = load_script(FIXTURES / "dism" / "add_package_ok.jsonl")
    runner = make_runner(FakeBackend(script, exit_code=exit_code))

    result = run_sync(runner.run(spec_for()))

    assert result.succeeded
    assert result.returncode == 0
    assert result.progress_events >= 2
    assert result.result_events >= 1
    assert "The operation completed successfully." in result.last_lines
    assert result.last_channel == "stdout"
    assert result.duration_seconds >= 0


def test_events_are_streamed_with_channel_and_kind() -> None:
    script, exit_code = load_script(FIXTURES / "wimlib" / "export_ok.jsonl")
    seen: list[ProcessEvent] = []
    runner = make_runner(FakeBackend(script, exit_code=exit_code))

    run_sync(
        runner.run(
            RunSpec(tool=ToolKind.WIMLIB, args=("wimlib-imagex", "export"), cwd=CWD),
            on_event=seen.append,
        )
    )

    assert [event.kind for event in seen][:1] == [EventKind.PROGRESS]
    assert any(event.kind is EventKind.RESULT for event in seen)
    assert all(event.channel in {"stdout", "stderr"} for event in seen)
    # wimlib 默认 utf-8，正常内容不应触发回退
    assert all(event.encoding == "utf-8" for event in seen)


def test_aria2c_fixture_classification() -> None:
    script, exit_code = load_script(FIXTURES / "aria2c" / "download_ok.jsonl")
    runner = make_runner(FakeBackend(script, exit_code=exit_code))
    result = run_sync(runner.run(spec_for(ToolKind.ARIA2C, stage="Fetch")))

    assert result.progress_events == 2
    assert result.result_events == 1


# --- 失败路径：现场信息必须齐 -------------------------------------------------


def test_failure_carries_full_context() -> None:
    """进程 §9.2：工具、原始返回码、耗时、通道、HRESULT、末 20 行、脱敏命令。"""
    script, exit_code = load_script(FIXTURES / "dism" / "add_package_hresult_fail.jsonl")
    runner = make_runner(FakeBackend(script, exit_code=exit_code))

    with pytest.raises(ToolExecutionError) as caught:
        run_sync(runner.run(spec_for()))

    error = caught.value
    assert error.code is ErrorCode.TOOL_FAILED
    assert error.exit_code is ExitCode.FAILURE
    assert error.context.tool == "dism"
    assert error.context.returncode == 87
    assert error.context.stage == "Integrate"
    assert error.context.channel == "stderr"
    assert error.context.hresult == "0x800f081f"
    assert error.context.duration_seconds is not None
    assert "The source files could not be found." in error.context.raw_excerpt
    payload = error.to_dict()
    assert payload["error"] == "tool_failed"
    assert payload["code"] == 2


def test_check_false_returns_result_instead_of_raising() -> None:
    script, exit_code = load_script(FIXTURES / "dism" / "add_package_hresult_fail.jsonl")
    runner = make_runner(FakeBackend(script, exit_code=exit_code))
    result = run_sync(runner.run(spec_for(), check=False))
    assert not result.succeeded
    assert result.returncode == 87


def test_missing_executable_maps_to_exit_code_4() -> None:
    class _RaisingBackend(FakeBackend):
        async def spawn(self, spec: RunSpec) -> SpawnedProcess:
            raise FileNotFoundError(spec.args[0])

    runner = make_runner(_RaisingBackend())
    with pytest.raises(ToolMissingError) as caught:
        run_sync(runner.run(spec_for(ToolKind.WIMLIB)))

    assert caught.value.code is ErrorCode.TOOL_MISSING
    assert caught.value.exit_code is ExitCode.INVALID_ARGUMENT


def test_permission_error_maps_to_permission_denied() -> None:
    class _DeniedBackend(FakeBackend):
        async def spawn(self, spec: RunSpec) -> SpawnedProcess:
            raise PermissionError(5, "Access is denied")

    runner = make_runner(_DeniedBackend())
    with pytest.raises(PermissionDeniedError) as caught:
        run_sync(runner.run(spec_for()))

    assert caught.value.code is ErrorCode.PERMISSION_DENIED
    assert caught.value.exit_code is ExitCode.INVALID_ARGUMENT


# --- 取消 -------------------------------------------------------------------


def test_cancel_without_console_skips_ctrl_break() -> None:
    """进程 §7.1：无控制台时第一步 Ctrl-Break 是空转，直接 terminate。"""
    script = [ScriptedStep("stdout", b"working\n", 0.01) for _ in range(20)]
    backend = FakeBackend(script, console=False)
    runner = make_runner(backend)
    token = _Token(cancel_after=1)

    with pytest.raises(UserCancelledError) as caught:
        run_sync(runner.run(spec_for(), cancel=token))

    assert caught.value.exit_code is ExitCode.CANCELLED
    assert caught.value.code is ErrorCode.CANCELED


def test_cancel_with_console_uses_ctrl_break_first() -> None:
    script = [ScriptedStep("stdout", b"working\n", 0.01) for _ in range(20)]
    backend = FakeBackend(script, console=True)
    runner = make_runner(backend)
    token = _Token(cancel_after=1)

    with pytest.raises(UserCancelledError):
        run_sync(runner.run(spec_for(), cancel=token))


# --- 并发 -------------------------------------------------------------------


def test_concurrency_is_fixed_to_one() -> None:
    """进程 §8：想注入并发也只按 1 跑，并记 ``concurrency_ignored``。"""
    backend = FakeBackend([], exit_code=0)
    runner = make_runner(backend)
    result = run_sync(runner.run(spec_for(), concurrency_requested=8))
    assert "concurrency_ignored" in result.warnings


def test_gate_reuses_lock_per_resource() -> None:
    gate = ConcurrencyGate()
    assert gate.lock("image") is gate.lock("image")
    assert gate.lock("image") is not gate.lock("download")


def test_lock_is_held_while_tool_runs() -> None:
    """镜像类必须串行：跑的时候锁必须是持着的。"""
    gate = ConcurrencyGate()
    observed: list[bool] = []
    script = [ScriptedStep("stdout", b"The operation completed successfully.\n")]
    runner = ProcessRunner(
        backend=FakeBackend(script, exit_code=0),
        sleep=_no_sleep,
        gate=gate,
    )

    run_sync(
        runner.run(spec_for(), on_event=lambda _e: observed.append(gate.lock("image").locked()))
    )

    assert observed and all(observed)


# --- 行处理细节 -------------------------------------------------------------


def test_truncated_and_partial_lines_are_marked() -> None:
    huge = b"x" * (70 * 1024) + b"\n"
    script = [ScriptedStep("stdout", huge), ScriptedStep("stdout", b"no-newline-tail")]
    seen: list[ProcessEvent] = []
    runner = make_runner(FakeBackend(script, exit_code=0))

    run_sync(runner.run(spec_for(), on_event=seen.append))

    assert any(event.truncated for event in seen), "超长行必须标记 truncated"
    assert any(event.partial for event in seen), "无换行残留必须标记 partial"


def test_decoding_fallback_is_visible_in_event() -> None:
    """GBK 字节用 utf-8 解会失败 → 回退 cp936，事件里要能看出实际编码。"""
    script = [ScriptedStep("stdout", "操作成功完成。\n".encode("gbk"))]
    seen: list[ProcessEvent] = []
    runner = make_runner(FakeBackend(script, exit_code=0))

    run_sync(
        runner.run(
            RunSpec(tool=ToolKind.WIMLIB, args=("wimlib-imagex",), cwd=CWD), on_event=seen.append
        )
    )

    assert any(event.encoding == "cp936" for event in seen)


# --- 脱敏 -------------------------------------------------------------------


def test_redact_command_masks_credentials() -> None:
    text = redact_command(
        ["aria2c", "--http-user=u", "--http-passwd=secret", "--header=Authorization: Bearer tok"]
    )
    assert "secret" not in text
    assert "--http-passwd=***" in text


def test_redact_command_handles_split_value() -> None:
    text = redact_command(["tool", "--password", "p@ss", "--file=a.cab"])
    assert "p@ss" not in text
    assert "--file=a.cab" in text


# --- 夹具加载器 --------------------------------------------------------------


def test_load_script_supports_hex_bytes(tmp_path: Path) -> None:
    fixture = tmp_path / "hex.jsonl"
    fixture.write_text(
        '{"channel":"stdout","bytes":"b2b3d3c3b9a6cdeab3c9a1a30a"}\n{"exit":7}\n',
        encoding="utf-8",
    )
    script, exit_code = load_script(fixture)
    assert script[0].data == bytes.fromhex("b2b3d3c3b9a6cdeab3c9a1a30a")
    assert exit_code == 7


def test_delay_is_attached_to_next_event(tmp_path: Path) -> None:
    fixture = tmp_path / "delay.jsonl"
    fixture.write_text('{"delay_ms":250}\n{"channel":"stdout","text":"a"}\n', encoding="utf-8")
    script, _ = load_script(fixture)
    assert script[0].delay_seconds == pytest.approx(0.25)
