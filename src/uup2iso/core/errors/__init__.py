"""统一错误模型与退出码（WP0-T4）。

分层用法：

* **业务代码**：抛语义化子类（:class:`StageExecutionError` / :class:`DependencyError` …），
  带 ``code`` 与现场信息；
* **编排层**：把异常折算成 :class:`ExitCode`（``exit_code_for``），并决定是否重试；
* **CLI / GUI**：消费 ``error.to_dict()`` 渲染 JSON 或错误卡片，不再自己拼字符串。

退出码唯一来源：``GUI与框架设计.md`` §5.6；错误码来源见 :mod:`uup2iso.core.errors.codes`。
"""

from __future__ import annotations

from uup2iso.core.errors.base import (
    AlreadyRunningError,
    ConfigError,
    ConfigMigrationError,
    ConfigParseError,
    ConfigSchemaError,
    ConfigSemanticError,
    ConfigVersionError,
    DependencyError,
    InputError,
    InputRejectedError,
    InternalError,
    StageExecutionError,
    UsageError,
    UserCancelledError,
    Uup2IsoError,
    exit_code_for,
)
from uup2iso.core.errors.codes import ErrorCode
from uup2iso.core.errors.context import (
    MAX_EXCERPT_LINES,
    ErrorContext,
    extract_hresult,
    tail_lines,
)
from uup2iso.core.errors.exit_code import ExitCode

__all__ = [
    "ConfigError",
    "ConfigMigrationError",
    "ConfigParseError",
    "ConfigSchemaError",
    "ConfigSemanticError",
    "ConfigVersionError",
    "InputError",
    "MAX_EXCERPT_LINES",
    "AlreadyRunningError",
    "UserCancelledError",
    "DependencyError",
    "ErrorCode",
    "ErrorContext",
    "ExitCode",
    "InputRejectedError",
    "InternalError",
    "StageExecutionError",
    "Uup2IsoError",
    "UsageError",
    "exit_code_for",
    "extract_hresult",
    "tail_lines",
]
