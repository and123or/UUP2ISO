"""统一错误基类与分类异常（GUI 框架 §5.6；进程 §9.1）。

设计三条硬规则：

1. **异常负责退出码**：`UserCancelledError`→3、参数/依赖→4、阶段失败→2、已有实例→5；
   退出码 1（有警告）**不是异常**，由编排层按警告明细折算。
2. **错误码必须显式**：除少数自带固定码的异常（取消/单实例/内部错误）外，
   构造时不传 ``code`` 直接 ``TypeError``——避免兜底成 ``INTERNAL_ERROR`` 掩盖分类缺失。
3. **现场信息随异常走**：阶段、文件、工具、原始返回码、原始输出片段都在
   :class:`~uup2iso.core.errors.context.ErrorContext` 里，``to_dict()`` 直接可给
   GUI 错误卡片与 CLI JSON 用（日志 §6.1 的 ``{"error":..,"code":..}`` 形状）。
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Self

from uup2iso.core.errors.codes import ErrorCode
from uup2iso.core.errors.context import ErrorContext
from uup2iso.core.errors.exit_code import ExitCode

__all__ = [
    "AlreadyRunningError",
    "ConfigError",
    "ConfigMigrationError",
    "ConfigParseError",
    "ConfigSchemaError",
    "ConfigSemanticError",
    "ConfigVersionError",
    "UserCancelledError",
    "DependencyError",
    "InputRejectedError",
    "InternalError",
    "StageExecutionError",
    "Uup2IsoError",
    "UsageError",
    "exit_code_for",
]


class Uup2IsoError(Exception):
    """所有可预期失败的基类。

    Attributes:
        code: 稳定错误码（机器可读，见 :class:`ErrorCode`）。
        message: 面向用户的中文说明（不含堆栈）。
        context: 现场信息。
    """

    default_exit_code: ClassVar[ExitCode] = ExitCode.FAILURE
    default_code: ClassVar[ErrorCode | None] = None

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        stage: str | None = None,
        substep: str | None = None,
        file: Path | None = None,
        tool: str | None = None,
        returncode: int | None = None,
        command: str | None = None,
        channel: str | None = None,
        hresult: str | None = None,
        duration_seconds: float | None = None,
        raw_excerpt: tuple[str, ...] = (),
        context: ErrorContext | None = None,
    ) -> None:
        resolved = code if code is not None else self.default_code
        if resolved is None:
            raise TypeError(f"{type(self).__name__} 必须显式传入 code（错误码要能被检索与统计）")

        super().__init__(message)
        self.code: ErrorCode = resolved
        self.message = message
        base = context or ErrorContext()
        self.context = ErrorContext(
            stage=stage if stage is not None else base.stage,
            substep=substep if substep is not None else base.substep,
            file=file if file is not None else base.file,
            tool=tool if tool is not None else base.tool,
            returncode=returncode if returncode is not None else base.returncode,
            command=command if command is not None else base.command,
            channel=channel if channel is not None else base.channel,
            hresult=hresult if hresult is not None else base.hresult,
            duration_seconds=(
                duration_seconds if duration_seconds is not None else base.duration_seconds
            ),
            raw_excerpt=raw_excerpt or base.raw_excerpt,
        )

    @property
    def exit_code(self) -> ExitCode:
        """该异常对应的进程退出码。"""
        return self.default_exit_code

    def with_context(self, context: ErrorContext) -> Self:
        """补全现场信息（返回自身，便于 ``raise Err(...).with_context(ctx)``）。"""
        self.context = ErrorContext(
            stage=context.stage or self.context.stage,
            substep=context.substep or self.context.substep,
            file=context.file or self.context.file,
            tool=context.tool or self.context.tool,
            returncode=context.returncode
            if context.returncode is not None
            else self.context.returncode,
            command=context.command or self.context.command,
            channel=context.channel or self.context.channel,
            hresult=context.hresult or self.context.hresult,
            duration_seconds=(
                context.duration_seconds
                if context.duration_seconds is not None
                else self.context.duration_seconds
            ),
            raw_excerpt=context.raw_excerpt or self.context.raw_excerpt,
        )
        return self

    def to_dict(self) -> dict[str, object]:
        """转成 CLI JSON / GUI 错误卡片可直接消费的字典。

        Returns:
            至少含 ``error``（小写码名）、``code``（进程退出码）、``message``，
            以及已采集到的现场字段。
        """
        payload: dict[str, object] = {
            "error": self.code.slug,
            "code": int(self.exit_code),
            "message": self.message,
        }
        payload.update(self.context.as_dict())
        return payload

    def __str__(self) -> str:
        parts = [f"[{self.code.value}] {self.message}"]
        if self.context.stage:
            parts.append(f"stage={self.context.stage}")
        if self.context.tool:
            parts.append(f"tool={self.context.tool}")
        if self.context.returncode is not None:
            parts.append(f"exit={self.context.returncode}")
        return " | ".join(parts)


class UsageError(Uup2IsoError):
    """参数/依赖错误（退出码 4）：配置不合法、输入不支持、工具缺失、非管理员。"""

    default_exit_code: ClassVar[ExitCode] = ExitCode.INVALID_ARGUMENT


class DependencyError(UsageError):
    """外部依赖不满足（退出码 4）：工具缺失、启动失败、权限不足。

    单独成类是为了让 GUI 能把「要装什么」与「配置写错了」区分开呈现。
    """


class InputRejectedError(UsageError):
    """输入被明确拒绝（退出码 4）：加密 ESD、多卷 SWM、分片 ESD、非 Windows 映像。"""


class StageExecutionError(Uup2IsoError):
    """阶段执行失败（退出码 2）：工具非 0、产物校验失败等。"""

    default_exit_code: ClassVar[ExitCode] = ExitCode.FAILURE


class UserCancelledError(Uup2IsoError):
    """用户取消（退出码 3）。

    取消**不是错误**：保留中间产物、不记 ERROR、允许 `--resume`（编排 §8）。
    """

    default_exit_code: ClassVar[ExitCode] = ExitCode.CANCELLED
    default_code: ClassVar[ErrorCode | None] = ErrorCode.CANCELED

    def __init__(self, message: str = "用户已取消任务", *, stage: str | None = None) -> None:
        super().__init__(message, stage=stage)


class AlreadyRunningError(Uup2IsoError):
    """已有实例在运行（退出码 5）。

    冲突实例不建日志、不清理、不写审计（日志 §6.1）；GUI 侧由主流程改用「激活已有窗口」。
    """

    default_exit_code: ClassVar[ExitCode] = ExitCode.ALREADY_RUNNING
    default_code: ClassVar[ErrorCode | None] = ErrorCode.ALREADY_RUNNING

    def __init__(self, message: str = "程序已在运行") -> None:
        super().__init__(message)


class ConfigError(UsageError):
    """配置校验失败（退出码 4）。具体错误码由子类固定。"""


class ConfigParseError(ConfigError):
    """V0 语法层失败（``CONFIG_PARSE_ERROR``）。"""

    default_code = ErrorCode.CONFIG_PARSE_ERROR


class ConfigSchemaError(ConfigError):
    """V1 Schema 层失败（``CONFIG_SCHEMA_INVALID``）。"""

    default_code = ErrorCode.CONFIG_SCHEMA_INVALID


class ConfigSemanticError(ConfigError):
    """V2 语义层失败（``CONFIG_SEMANTIC_INVALID``）。"""

    default_code = ErrorCode.CONFIG_SEMANTIC_INVALID


class ConfigVersionError(ConfigError):
    """配置主版本不受支持（``CONFIG_VERSION_UNSUPPORTED``）。"""

    default_code = ErrorCode.CONFIG_VERSION_UNSUPPORTED


class ConfigMigrationError(ConfigError):
    """迁移失败（``CONFIG_MIGRATION_FAILED``）：不写回、保留原文件。"""

    default_code = ErrorCode.CONFIG_MIGRATION_FAILED


class InternalError(Uup2IsoError):
    """未归类的内部错误（退出码 2）：兜底用，出现即应补分类。"""

    default_code: ClassVar[ErrorCode | None] = ErrorCode.INTERNAL_ERROR


def exit_code_for(error: BaseException) -> ExitCode:
    """把任意异常折算成进程退出码。

    未知异常按「阶段失败（2）」处理：宁可报失败，也不假装成功。

    Args:
        error: 捕获到的异常。

    Returns:
        对应的 :class:`ExitCode`。
    """
    if isinstance(error, Uup2IsoError):
        return error.exit_code
    return ExitCode.FAILURE
