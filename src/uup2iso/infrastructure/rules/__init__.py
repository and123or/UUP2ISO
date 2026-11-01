"""规则库（WP0-T9）：加载校验、依赖矩阵、执行前阻断。

| 模块 | 职责 |
| --- | --- |
| :mod:`.loader` | 规则包加载：JSON Schema + id 唯一 + 引用存在 + 无环 + 高风险必备；``RULEPACK_INVALID`` 不部分加载 |
| :mod:`.matrix` | ``dependency-matrix.json``：手写硬约束 + 实测观测合并（冲突记 ``matrix_conflict``） |
| :mod:`.selection` | 规则 §4.3 的逐项阻断判定 |

规则数据本身是**外置 JSON**（``rules/*.json``），查找顺序支持外置更新：
``程序目录\\rules`` → 包内 ``uup2iso/rules`` → 仓库 ``docs/规范/rules``。
"""

from __future__ import annotations

from uup2iso.infrastructure.rules.loader import (
    MATRIX_FILENAME,
    RULES_GLOB,
    SCHEMA_FILENAME,
    default_rules_dir,
    load_library,
    load_pack,
)
from uup2iso.infrastructure.rules.matrix import (
    BlockedCombo,
    CapabilityEntry,
    DependencyMatrix,
    GeneratedFrom,
    load_matrix,
    merge_observed,
)
from uup2iso.infrastructure.rules.selection import (
    BlockCode,
    BlockedItem,
    SelectionResult,
    evaluate_selection,
)

__all__ = [
    "MATRIX_FILENAME",
    "RULES_GLOB",
    "SCHEMA_FILENAME",
    "BlockCode",
    "BlockedCombo",
    "BlockedItem",
    "CapabilityEntry",
    "DependencyMatrix",
    "GeneratedFrom",
    "SelectionResult",
    "default_rules_dir",
    "evaluate_selection",
    "load_library",
    "load_matrix",
    "load_pack",
    "merge_observed",
]
