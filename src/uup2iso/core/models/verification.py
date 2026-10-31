"""校验结果模型 —— 对应 输出 §6 的分级校验与 引擎 §3.2 R5。

`VerifyReport` 同时服务于两个调用方：

* ``Output.verify`` 子步骤（输出 §6）：L1/L2/L3 分级结论；
* ``IImageEngine.verify``（引擎 R5）：单文件深度校验的原始结论。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from uup2iso.core.models.enums import VerifyLevel

__all__ = ["VerifyFinding", "VerifyReport", "VerifySeverity"]


class VerifySeverity(StrEnum):
    """单条校验发现的严重程度。

    与日志级别同构（术语 §2.2）：``ERROR`` 即判该级别校验失败。
    """

    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class VerifyFinding:
    """一条校验发现。

    Attributes:
        code: 稳定的机器可读码，如 ``L1_MISSING_INSTALL_WIM``、``L2_COMPRESSION_MISMATCH``。
        message: 面向用户的中文说明。
        severity: 严重程度；``ERROR`` 使本轮校验失败。
        level: 该发现所属的校验级别。
    """

    code: str
    message: str
    severity: VerifySeverity = VerifySeverity.ERROR
    level: VerifyLevel = VerifyLevel.L1

    @property
    def is_fatal(self) -> bool:
        """是否导致失败（输出 §6：L1/L2 失败即判输出失败，退出码 2）。"""
        return self.severity is VerifySeverity.ERROR


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """一次校验的汇总。

    Attributes:
        level: 本次实际执行的最高级别；未勾选 L3 时不得出现 ``L3``（输出 §6）。
        findings: 全部发现，按产生顺序。
        duration_seconds: 耗时；L3 需要在日志记 ``output_validated level=3 duration=...``。
    """

    level: VerifyLevel
    findings: tuple[VerifyFinding, ...] = ()
    duration_seconds: float | None = None

    @property
    def ok(self) -> bool:
        """是否通过：没有任何 ``ERROR`` 级发现。"""
        return not any(item.is_fatal for item in self.findings)

    @property
    def errors(self) -> tuple[VerifyFinding, ...]:
        """失败项，用于退出码与 UI 汇总。"""
        return tuple(item for item in self.findings if item.is_fatal)
