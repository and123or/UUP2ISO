"""错误模型门禁（WP0-T4 验收：错误含阶段/文件/工具/返回码/原始片段）。

五组断言：

1. 退出码取值与 ``GUI框架 §5.6`` 逐条一致，且**每个异常类映射到正确码**；
2. 错误码不得「兜底成 INTERNAL」——缺 ``code`` 必须构造函数级报错；
3. ``to_dict()`` 形状符合日志 §6.1 的 ``{"error":..,"code":..}``，并携带现场字段；
4. 现场信息工具（``tail_lines`` / ``extract_hresult``）行为正确（进程 §9.2：20 行、HRESULT）；
5. ``exit_code_for`` 对未知异常按 2 处理（不假装成功）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uup2iso.core.errors import (
    MAX_EXCERPT_LINES,
    AlreadyRunningError,
    DependencyError,
    ErrorCode,
    ErrorContext,
    ExitCode,
    InputRejectedError,
    InternalError,
    StageExecutionError,
    UsageError,
    UserCancelledError,
    Uup2IsoError,
    exit_code_for,
    extract_hresult,
    tail_lines,
)

# --- 1. 退出码表与映射 -------------------------------------------------------


def test_exit_codes_match_gui_spec() -> None:
    """GUI框架 §5.6：0 成功 / 1 警告 / 2 失败 / 3 取消 / 4 参数依赖 / 5 已有实例。"""
    assert {int(item) for item in ExitCode} == {0, 1, 2, 3, 4, 5}
    assert ExitCode.SUCCESS == 0
    assert ExitCode.WARNING == 1
    assert ExitCode.FAILURE == 2
    assert ExitCode.CANCELLED == 3
    assert ExitCode.INVALID_ARGUMENT == 4
    assert ExitCode.ALREADY_RUNNING == 5


def test_warning_counts_as_success() -> None:
    """1 表示「完成但有警告」，属于成功；2 及以上才是失败。"""
    assert ExitCode.SUCCESS.is_success
    assert ExitCode.WARNING.is_success
    assert not ExitCode.FAILURE.is_success
    assert not ExitCode.CANCELLED.is_success


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (StageExecutionError("转换失败", code=ErrorCode.TOOL_FAILED), ExitCode.FAILURE),
        (
            UsageError("配置不合法", code=ErrorCode.CONFIG_SEMANTIC_INVALID),
            ExitCode.INVALID_ARGUMENT,
        ),
        (DependencyError("缺 wimlib", code=ErrorCode.TOOL_MISSING), ExitCode.INVALID_ARGUMENT),
        (InputRejectedError("加密 ESD", code=ErrorCode.INPUT_ENCRYPTED), ExitCode.INVALID_ARGUMENT),
        (UserCancelledError(), ExitCode.CANCELLED),
        (AlreadyRunningError(), ExitCode.ALREADY_RUNNING),
        (InternalError("空指针"), ExitCode.FAILURE),
    ],
)
def test_exception_exit_code_mapping(error: Uup2IsoError, expected: ExitCode) -> None:
    """每个异常类必须落到正确退出码。"""
    assert error.exit_code is expected


def test_fixed_code_exceptions_carry_default_code() -> None:
    """取消 / 单实例 / 内部错误自带固定码，调用方无需重复传。"""
    assert UserCancelledError().code is ErrorCode.CANCELED
    assert AlreadyRunningError().code is ErrorCode.ALREADY_RUNNING
    assert InternalError("x").code is ErrorCode.INTERNAL_ERROR


# --- 2. 错误码必须显式 -------------------------------------------------------


def test_stage_failure_requires_explicit_code() -> None:
    """缺 code 时不得静默兜底成 INTERNAL_ERROR。"""
    with pytest.raises(TypeError, match="code"):
        StageExecutionError("没写错误码")


def test_usage_error_requires_explicit_code() -> None:
    with pytest.raises(TypeError, match="code"):
        DependencyError("缺工具但没给码")


# --- 3. to_dict 形状与现场字段 -----------------------------------------------


def test_to_dict_matches_cli_json_contract() -> None:
    """日志 §6.1：``{"error":"already_running","code":5}``。"""
    payload = AlreadyRunningError().to_dict()
    assert payload["error"] == "already_running"
    assert payload["code"] == 5
    assert isinstance(payload["message"], str)


def test_to_dict_carries_full_context() -> None:
    """GUI框架 §5.6：错误对象须含阶段、文件、工具、返回码、原始片段。"""
    error = StageExecutionError(
        "应用更新包失败",
        code=ErrorCode.TOOL_FAILED,
        stage="Integrate",
        substep="updates",
        file=Path(r"D:\work\26100\install.wim"),
        tool="dism",
        returncode=87,
        command="dism /Image:D:\\mnt /Add-Package /PackagePath:x.msu",
        channel="stderr",
        hresult="0x800f081f",
        duration_seconds=12.5,
        raw_excerpt=("Error: 0x800f081f", "The source files could not be found."),
    )
    payload = error.to_dict()

    assert payload["error"] == "tool_failed"
    assert payload["code"] == 2
    assert payload["stage"] == "Integrate"
    assert payload["substep"] == "updates"
    assert payload["file"] == r"D:\work\26100\install.wim"
    assert payload["tool"] == "dism"
    assert payload["returncode"] == 87
    assert payload["channel"] == "stderr"
    assert payload["hresult"] == "0x800f081f"
    assert payload["duration_seconds"] == 12.5
    assert payload["raw_excerpt"] == (
        "Error: 0x800f081f",
        "The source files could not be found.",
    )


def test_to_dict_omits_empty_fields() -> None:
    """未采集到的字段不出现，避免 GUI 卡片出现一片 null。"""
    payload = UserCancelledError().to_dict()
    assert "file" not in payload
    assert "raw_excerpt" not in payload
    assert "returncode" not in payload


def test_str_is_readable_for_logs() -> None:
    """日志里的一行要能直接看出码、阶段与工具返回码。"""
    error = StageExecutionError(
        "挂载失败", code=ErrorCode.TOOL_FAILED, stage="Slim", tool="dism", returncode=87
    )
    text = str(error)
    assert "TOOL_FAILED" in text
    assert "stage=Slim" in text
    assert "tool=dism" in text
    assert "exit=87" in text


def test_with_context_merges_and_keeps_existing() -> None:
    """补全现场信息时不能把已有字段冲掉。"""
    error = StageExecutionError(
        "失败", code=ErrorCode.TOOL_FAILED, stage="Convert", tool="wimlib-imagex"
    )
    error.with_context(ErrorContext(returncode=55, hresult="0x00000037"))

    assert error.context.stage == "Convert"
    assert error.context.tool == "wimlib-imagex"
    assert error.context.returncode == 55
    assert error.context.hresult == "0x00000037"


def test_with_context_returns_self_for_chaining() -> None:
    error = StageExecutionError("失败", code=ErrorCode.TOOL_FAILED)
    assert error.with_context(ErrorContext(stage="Output")) is error


# --- 4. 现场信息提取工具 -----------------------------------------------------


def test_tail_lines_keeps_last_20() -> None:
    """进程 §9.2：只留最后 20 条，过多输出不进错误对象。"""
    text = "\n".join(f"line-{i}" for i in range(50))
    excerpt = tail_lines(text)
    assert len(excerpt) == MAX_EXCERPT_LINES
    assert excerpt[-1] == "line-49"
    assert excerpt[0] == "line-30"


def test_tail_lines_accepts_sequences_and_empty() -> None:
    assert tail_lines(["a", "b"]) == ("a", "b")
    assert tail_lines("") == ()
    assert tail_lines([]) == ()


def test_extract_hresult_takes_last_match() -> None:
    """前面的 HRESULT 可能是无关值，出错的那个在末尾。"""
    lines = ["precheck 0x00000000 ok", "Error: 0x800f081f source not found"]
    assert extract_hresult(lines) == "0x800f081f"
    assert extract_hresult("no hresult here") is None


def test_error_context_serializes_path_as_string() -> None:
    ctx = ErrorContext(file=Path("D:/x.iso"), stage="Prepare")
    payload = ctx.as_dict()
    assert payload["file"] == "D:\\x.iso"
    assert payload["stage"] == "Prepare"
    assert "tool" not in payload


# --- 5. 异常 → 退出码的兜底 --------------------------------------------------


def test_exit_code_for_known_error() -> None:
    assert exit_code_for(UserCancelledError()) is ExitCode.CANCELLED


def test_exit_code_for_unknown_exception_is_failure() -> None:
    """未预期异常按失败处理，绝不返回 0。"""
    assert exit_code_for(ValueError("boom")) is ExitCode.FAILURE


def test_error_code_slug_matches_documented_json() -> None:
    """对外 JSON 用小写 slug，与日志 §6.1 的 `already_running` 一致。"""
    assert ErrorCode.ALREADY_RUNNING.slug == "already_running"
    assert ErrorCode.MANIFEST_INVALID.slug == "manifest_invalid"
