"""规则执行结果模型（规则 §5）。

字段刻意保持最小：规则的上下文跳过、可逆性、失败分级等语义在 WP4/WP5 落地时
才细化，这里只保证「跳过必须可见」这一条硬要求有承载。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RuleOutcome", "RuleReport"]


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """单条规则的执行结局。

    Attributes:
        rule_id: 规则 id（如 ``FR4-SL-A01``）。
        applied: 是否真正执行并生效。
        skipped_reason: 跳过原因（如 ``context_offline_only``、``input_satisfied``）；
            为 ``None`` 表示未跳过。**跳过的规则必须在汇总里出现**（规则 §5）。
        error: 失败信息；``None`` 表示未失败。
    """

    rule_id: str
    applied: bool
    skipped_reason: str | None = None
    error: str | None = None

    @property
    def skipped(self) -> bool:
        """是否被跳过（而非执行或失败）。"""
        return not self.applied and self.error is None

    @property
    def failed(self) -> bool:
        """是否失败。"""
        return self.error is not None


@dataclass(frozen=True, slots=True)
class RuleReport:
    """一次规则批次执行的汇总（精简 / 优化 / 集成共用）。"""

    outcomes: tuple[RuleOutcome, ...] = ()

    @property
    def applied(self) -> tuple[RuleOutcome, ...]:
        """真正生效的规则。"""
        return tuple(item for item in self.outcomes if item.applied)

    @property
    def skipped(self) -> tuple[RuleOutcome, ...]:
        """被跳过的规则；GUI/CLI 必须展示（规则 §5）。"""
        return tuple(item for item in self.outcomes if item.skipped)

    @property
    def failed(self) -> tuple[RuleOutcome, ...]:
        """失败项；``high`` 风险规则的失败应中断阶段（规则 §5）。"""
        return tuple(item for item in self.outcomes if item.failed)

    @property
    def has_failures(self) -> bool:
        """是否存在失败项。"""
        return bool(self.failed)
