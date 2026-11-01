"""规则数据模型（规则 §2）。

规则库是**外置 JSON**（`rules/*.json`），所以这里用轻量 dataclass 做类型化访问，
合法性由「JSON Schema + 加载器语义校验」保证，而不是靠 pydantic 重复声明一遍。

约定：

* ``raw`` 保留原始字段，供将来新增字段在不改模型时也能读到；
* 高风险判定集中在 :attr:`Rule.is_high_risk`，加载器与选择器共用同一口径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "HIGH_RISK",
    "Rule",
    "RuleLibrary",
    "RulePack",
    "RulePackIssue",
]

HIGH_RISK = "high"
"""规则 §2.2 的硬要求只针对这一档：必须有 verify，不可逆时必须有 recovery。"""


@dataclass(frozen=True, slots=True)
class Rule:
    """一条规则（字段语义见规则 §2.1–§2.2）。"""

    id: str
    name: str
    domain: str
    group: str
    level: str
    order: int = 0
    default_on: bool = False
    requires: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    presets: tuple[str, ...] = ()
    context: tuple[str, ...] = ()
    description: str = ""
    warning: str = ""
    actions: tuple[dict[str, Any], ...] = ()
    verify: dict[str, Any] | None = None
    reversible: bool = False
    recovery: str = ""
    min_engine: str | None = None
    estimated_bytes: int | None = None
    pack: str = ""
    """来源包文件名，便于报错时指明是哪个文件出的问题。"""

    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_high_risk(self) -> bool:
        return self.level == HIGH_RISK


@dataclass(frozen=True, slots=True)
class RulePack:
    """一个规则包文件（`fr4-rules.json` / `fr5-rules.json` / 外置包）。"""

    path: Path
    schema_version: str
    pack_version: str = ""
    updated: str = ""
    source: str = ""
    rules: tuple[Rule, ...] = ()

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True, slots=True)
class RulePackIssue:
    """一条规则库问题（加载期使用，便于汇总报错）。"""

    where: str
    code: str
    message: str

    def as_text(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass(frozen=True, slots=True)
class RuleLibrary:
    """加载完成的规则库。

    Attributes:
        packs: 来源包（按加载顺序）。
        rules: id → 规则（**只含**通过校验且满足 ``minEngine`` 的规则）。
        skipped_min_engine: 因 ``minEngine`` 高于当前程序版本而跳过的规则 id。
        engine_version: 用于 ``minEngine`` 判定的程序版本。
    """

    packs: tuple[RulePack, ...] = ()
    rules: dict[str, Rule] = field(default_factory=dict)
    skipped_min_engine: tuple[str, ...] = ()
    engine_version: str = ""

    def get(self, rule_id: str) -> Rule | None:
        return self.rules.get(rule_id)

    def ids(self) -> tuple[str, ...]:
        """按执行顺序（``order`` 升序，同序按 id）返回全部规则 id（规则 §5.1）。"""
        return tuple(
            rule.id for rule in sorted(self.rules.values(), key=lambda item: (item.order, item.id))
        )

    def by_preset(self, preset: str) -> tuple[Rule, ...]:
        """预设名 → 规则（规则 §9：预设按 ``ruleId`` 绑定）。"""
        return tuple(
            rule
            for rule in sorted(self.rules.values(), key=lambda item: (item.order, item.id))
            if preset in rule.presets
        )

    def default_on_ids(self) -> tuple[str, ...]:
        return tuple(rule.id for rule in self.rules.values() if rule.default_on)

    def dependents_of(self, rule_id: str) -> tuple[str, ...]:
        """依赖该规则的规则 id（``requires`` 反向索引）。"""
        return tuple(rule.id for rule in self.rules.values() if rule_id in rule.requires)

    @property
    def rule_count(self) -> int:
        return len(self.rules)
