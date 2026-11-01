"""依赖矩阵：双轨合并与阻断数据（规则 §4）。

两个来源，地位不同：

* **手写硬约束**（本文件的 ``capabilities`` / ``blockedCombos``）——阻断依据，可评审、可解释；
* **实测观测数据**（脚本生成）——只用于补全与提示，**不单独用于阻断**；与手写冲突时以手写为准，
  并记 ``WARN matrix_conflict``（规则 §4.1）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BlockedCombo",
    "CapabilityEntry",
    "DependencyMatrix",
    "GeneratedFrom",
    "load_matrix",
    "merge_observed",
]


class GeneratedFrom(BaseModel):
    """观测数据的来源（实测样本）。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    build: str = ""
    edition: str = ""
    generated_at: str = Field(default="", alias="generatedAt")


class CapabilityEntry(BaseModel):
    """能力 → 提供它的包 / 依赖它的规则。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    provided_by: list[str] = Field(default_factory=list, alias="providedBy")
    required_by_rules: list[str] = Field(default_factory=list, alias="requiredByRules")


class BlockedCombo(BaseModel):
    """禁止组合（规则 §4.2）。

    Attributes:
        remove: 同时移除这些规则时出问题。
        breaks: 会破坏的东西（人可读，用于提示影响面）。
        severity: ``block`` 拒绝执行并说明；``warning`` 允许但需二次确认。
        evidence: 判定依据（实测记录/评审结论）。
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    remove: list[str] = Field(default_factory=list)
    breaks: list[str] = Field(default_factory=list)
    severity: Literal["block", "warning"] = "warning"
    evidence: str = ""


class DependencyMatrix(BaseModel):
    """``rules\\dependency-matrix.json``。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: str = Field(default="1.0", alias="schemaVersion")
    generated_from: GeneratedFrom = Field(default_factory=GeneratedFrom, alias="generatedFrom")
    capabilities: list[CapabilityEntry] = Field(default_factory=list)
    blocked_combos: list[BlockedCombo] = Field(default_factory=list, alias="blockedCombos")
    note: str = ""

    def capability_map(self) -> dict[str, CapabilityEntry]:
        return {entry.name: entry for entry in self.capabilities}

    def block_combos(self) -> tuple[BlockedCombo, ...]:
        return tuple(combo for combo in self.blocked_combos if combo.severity == "block")

    def warn_combos(self) -> tuple[BlockedCombo, ...]:
        return tuple(combo for combo in self.blocked_combos if combo.severity == "warning")


def load_matrix(path: Path) -> DependencyMatrix:
    """读取矩阵文件；不存在时返回空矩阵（不视为错误——M0 阶段内容为空）。

    Raises:
        ValueError: 文件存在但结构非法。
    """
    if not path.is_file():
        return DependencyMatrix()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"依赖矩阵无法解析：{exc}") from exc
    return DependencyMatrix.model_validate(payload)


def merge_observed(
    hard: DependencyMatrix,
    observed: DependencyMatrix,
) -> tuple[DependencyMatrix, tuple[str, ...]]:
    """把手写硬约束与实测观测合并。

    冲突口径：同一能力名在两边都有、但 ``providedBy`` 不同 → **保留手写**并记冲突；
    只在一侧出现的项直接并集。

    Returns:
        ``(合并后的矩阵, 冲突告警列表)``；告警文本形如
        ``matrix_conflict capability=<name> keep=hard``。
    """
    merged = hard.model_copy(deep=True)
    warnings: list[str] = []
    index = {entry.name: entry for entry in merged.capabilities}

    for entry in observed.capabilities:
        existing = index.get(entry.name)
        if existing is None:
            merged.capabilities.append(entry.model_copy(deep=True))
            index[entry.name] = entry
            continue

        if set(existing.provided_by) != set(entry.provided_by):
            warnings.append(f"matrix_conflict capability={entry.name} keep=hard")
            continue

        for rule_id in entry.required_by_rules:
            if rule_id not in existing.required_by_rules:
                existing.required_by_rules.append(rule_id)

    # 观测数据不参与阻断：blockedCombos 只保留手写的
    return merged, tuple(warnings)
