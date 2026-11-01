"""执行前阻断判定（规则 §4.3）。

判定顺序固定，**阻断是逐项的**：只去掉有问题的项并说明原因，不因为一条冲突让整个阶段失败。

```
① requires 未满足（依赖规则没被选中）  → 阻断该项，提示「需同时选择 X」
② conflicts 命中（互斥同时选中）        → 阻断，提示二选一
③ blockedCombos severity=block 命中     → 阻断，说明影响面
④ 高风险项未确认                        → 阻断该项（不是整任务）
⑤ 传递依赖有环                          → 加载期已判 RULEPACK_CYCLIC（不在这里）
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from uup2iso.core.models.rulepack import RuleLibrary
from uup2iso.infrastructure.rules.matrix import DependencyMatrix

__all__ = [
    "BlockedItem",
    "BlockCode",
    "SelectionResult",
    "evaluate_selection",
]


class BlockCode(StrEnum):
    """阻断原因码（写进 ``rules-report.json``，便于用户理解为什么被去掉）。"""

    UNKNOWN_RULE = "UNKNOWN_RULE"
    MISSING_REQUIRES = "MISSING_REQUIRES"
    CONFLICT = "CONFLICT"
    BLOCKED_COMBO = "BLOCKED_COMBO"
    HIGH_RISK_UNCONFIRMED = "HIGH_RISK_UNCONFIRMED"


@dataclass(frozen=True, slots=True)
class BlockedItem:
    """一条被阻断的规则。"""

    rule_id: str
    code: BlockCode
    message: str

    def as_text(self) -> str:
        return f"{self.rule_id}: {self.message}"


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """选择结果。

    Attributes:
        allowed: 可以执行的规则 id（按执行顺序）。
        blocked: 被去掉的项及原因。
        warnings: 需要提示但不禁用的情况（``warning`` 级禁止组合等）。
        requires_confirmation: 需用户二次确认后仍可执行的规则 id（高风险/警告级组合）。
    """

    allowed: tuple[str, ...] = ()
    blocked: tuple[BlockedItem, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = ()
    requires_confirmation: tuple[str, ...] = ()

    @property
    def has_blocked(self) -> bool:
        return bool(self.blocked)

    @property
    def blocked_ids(self) -> tuple[str, ...]:
        return tuple(item.rule_id for item in self.blocked)


def evaluate_selection(
    selected: list[str] | tuple[str, ...] | set[str],
    library: RuleLibrary,
    matrix: DependencyMatrix | None = None,
    *,
    confirmed_high_risk: frozenset[str] | set[str] = frozenset(),
) -> SelectionResult:
    """按规则 §4.3 判定哪些规则能执行。

    Args:
        selected: 用户选中的规则 id（来自 ``TaskConfig.slim.ruleIds`` 等）。
        library: 已加载的规则库。
        matrix: 依赖矩阵（可空）。
        confirmed_high_risk: 用户已确认的高风险规则 id。

    Returns:
        :class:`SelectionResult`；``allowed`` 已按 ``order`` + id 排序。
    """
    chosen = set(selected)
    blocked: list[BlockedItem] = []
    warnings: list[str] = []
    confirm: set[str] = set()

    for rule_id in sorted(chosen - set(library.rules)):
        blocked.append(
            BlockedItem(
                rule_id=rule_id,
                code=BlockCode.UNKNOWN_RULE,
                message="规则库中不存在该 id（预设可能来自更新版本）",
            )
        )

    for rule_id in library.ids():
        if rule_id not in chosen or rule_id in {item.rule_id for item in blocked}:
            continue
        rule = library.rules[rule_id]

        missing = [item for item in rule.requires if item not in chosen]
        if missing:
            blocked.append(
                BlockedItem(
                    rule_id=rule_id,
                    code=BlockCode.MISSING_REQUIRES,
                    message=f"需同时选择 {', '.join(missing)}",
                )
            )
            continue

        conflicts = [item for item in rule.conflicts if item in chosen]
        if conflicts:
            blocked.append(
                BlockedItem(
                    rule_id=rule_id,
                    code=BlockCode.CONFLICT,
                    message=f"与 {', '.join(conflicts)} 互斥，请二选一",
                )
            )
            continue

        if rule.is_high_risk and rule_id not in confirmed_high_risk:
            blocked.append(
                BlockedItem(
                    rule_id=rule_id,
                    code=BlockCode.HIGH_RISK_UNCONFIRMED,
                    message="高风险项需二次确认后才能执行",
                )
            )
            continue

    if matrix is not None:
        blocked_ids = {item.rule_id for item in blocked}
        for combo in matrix.blocked_combos:
            hit = [item for item in combo.remove if item in chosen]
            if not hit:
                continue
            impact = "、".join(combo.breaks) or "未说明的影响"
            if combo.severity == "block":
                for rule_id in hit:
                    if rule_id in blocked_ids:
                        continue
                    blocked.append(
                        BlockedItem(
                            rule_id=rule_id,
                            code=BlockCode.BLOCKED_COMBO,
                            message=f"与所选组合冲突：{impact}",
                        )
                    )
                    blocked_ids.add(rule_id)
            else:
                warnings.append(f"组合警告 {hit} 会破坏：{impact}（允许执行，请确认）")
                confirm.update(hit)

    blocked_ids = {item.rule_id for item in blocked}
    allowed = tuple(
        rule_id for rule_id in library.ids() if rule_id in chosen and rule_id not in blocked_ids
    )
    return SelectionResult(
        allowed=allowed,
        blocked=tuple(blocked),
        warnings=tuple(warnings),
        requires_confirmation=tuple(sorted(confirm - blocked_ids)),
    )
