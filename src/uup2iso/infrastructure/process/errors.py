"""进程层的错误类型（进程 §9.1 → 核心错误模型）。

这里只负责「把工具/权限/启动失败映射成带现场信息的异常」，
退出码与错误码语义仍在 :mod:`uup2iso.core.errors` 统一定义。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from uup2iso.core.errors import (
    DependencyError,
    ErrorCode,
    ErrorContext,
    StageExecutionError,
    extract_hresult,
    tail_lines,
)
from uup2iso.infrastructure.process.backend import RunSpec
from uup2iso.infrastructure.process.events import ProcessResult

__all__ = [
    "PermissionDeniedError",
    "ToolExecutionError",
    "ToolMissingError",
    "ToolStartError",
    "build_tool_failure",
    "redact_command",
]

_SENSITIVE_FLAG = re.compile(
    r"(passw|passwd|password|token|secret|apikey|api-key|key=)",
    re.IGNORECASE,
)


def redact_command(args: Iterable[str]) -> str:
    """脱敏命令行（日志 §5.5）：把敏感开关后面的值替换成 ``***``。

    Args:
        args: 原始参数序列。

    Returns:
        单行命令文本；含敏感关键字的参数只保留开关名。
    """
    parts: list[str] = []
    items = list(args)
    for index, item in enumerate(items):
        if _SENSITIVE_FLAG.search(item):
            if "=" in item:
                flag, _, _value = item.partition("=")
                parts.append(f"{flag}=***")
            else:
                parts.append(item)
                if index + 1 < len(items):
                    parts.append("***")
                    items[index + 1] = ""
            continue
        if item:
            parts.append(item)
    return " ".join(parts)


class ToolExecutionError(StageExecutionError):
    """工具返回非 0（进程 §9.1 ``TOOL_FAILED``，退出码 2）。"""

    default_code = ErrorCode.TOOL_FAILED


class ToolStartError(DependencyError):
    """创建进程失败（``TOOL_START_FAILED``，退出码 4）：路径不存在、参数非法等。"""

    default_code = ErrorCode.TOOL_START_FAILED


class PermissionDeniedError(DependencyError):
    """权限被拒绝（``PERMISSION_DENIED``，退出码 4）：需要管理员或被系统拒绝。"""

    default_code = ErrorCode.PERMISSION_DENIED


class ToolMissingError(DependencyError):
    """工具不存在（``TOOL_MISSING``，退出码 4）：由 ToolProvider 抛出，Runner 也会在
    ``FileNotFoundError`` 时兜底映射。"""

    default_code = ErrorCode.TOOL_MISSING


def build_tool_failure(
    spec: RunSpec,
    result: ProcessResult,
    *,
    channel: str | None = None,
) -> ToolExecutionError:
    """按进程 §9.2 组装失败异常：工具、原始返回码、耗时、通道、HRESULT、末 20 行、脱敏命令。"""
    excerpt = tail_lines(result.last_lines)
    joined = "\n".join(excerpt)
    context = ErrorContext(
        stage=spec.stage,
        tool=spec.tool.value,
        returncode=result.returncode,
        command=redact_command(spec.args),
        channel=channel,
        hresult=extract_hresult(joined),
        duration_seconds=result.duration_seconds,
        raw_excerpt=excerpt,
    )
    return ToolExecutionError(
        f"{spec.tool.value} 返回 {result.returncode}",
        context=context,
    )
