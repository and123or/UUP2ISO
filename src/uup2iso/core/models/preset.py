"""预设模型与校验（配置 §4）。

四类预设共用一个骨架，只有 ``items`` 形态分两种（配置 §4.1）：

| kind | items |
| --- | --- |
| ``slim`` / ``optimize`` | ``{ruleIds: [...], params: {...}}`` —— 按 ruleId 绑定，未知 id 必须拒绝 |
| ``integrate`` / ``unattend`` | 对应 TaskConfig 片段的深拷贝 —— 走片段自己的 V1+V2 |

关键约束：**未知 ruleId 一律拒绝导入并列出**（配置 §4.2）。静默丢项会让用户以为删了其实没删。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from uup2iso.core.models.config import (
    IntegrateSection,
    OptimizeSection,
    PresetOrigin,
    SlimSection,
    UnattendSection,
)
from uup2iso.core.models.enums import PresetKind

__all__ = [
    "RULE_BASED_KINDS",
    "PresetDocument",
    "PresetIssue",
    "PresetRuleItems",
    "PresetSnapshot",
    "check_preset",
    "fragment_model_for",
]

PRESET_SCHEMA_VERSION = "1.0"

RULE_BASED_KINDS: frozenset[PresetKind] = frozenset({PresetKind.SLIM, PresetKind.OPTIMIZE})
"""items 按 ruleId 绑定的类别；其余类别是配置片段。"""


class PresetRuleItems(BaseModel):
    """规则类预设的 ``items``（配置 §4.1）。"""

    model_config = ConfigDict(extra="allow")

    rule_ids: list[str] = Field(default_factory=list, alias="ruleIds")
    params: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PresetIssue(BaseModel):
    """一条预设校验问题。"""

    field: str
    code: str
    message: str


class PresetDocument(BaseModel):
    """预设文件（``presets\\<kind>\\<id>.json``）。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: str = Field(default=PRESET_SCHEMA_VERSION, alias="schemaVersion")
    kind: PresetKind
    id: str
    version: str = "1.0"
    name: str = ""
    created_at: datetime | None = Field(default=None, alias="createdAt")
    app_version: str = Field(default="", alias="appVersion")
    items: dict[str, Any] = Field(default_factory=dict)


class PresetSnapshot(BaseModel):
    """内联进 ``TaskConfig`` 的预设快照（配置 §4.2）。

    规则类预设内联为 ``{origin, ruleIds, params}``；配置类预设直接展开为对应字段。
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    origin: PresetOrigin = Field(default_factory=PresetOrigin)
    slim: SlimSection | None = None
    optimize: OptimizeSection | None = None
    integrate: IntegrateSection | None = None
    unattend: UnattendSection | None = None


def fragment_model_for(kind: PresetKind) -> type[BaseModel] | None:
    """返回配置类预设对应的片段模型；规则类返回 ``None``。"""
    if kind is PresetKind.INTEGRATE:
        return IntegrateSection
    if kind is PresetKind.UNATTEND:
        return UnattendSection
    return None


def check_preset(
    document: PresetDocument,
    *,
    known_rule_ids: set[str] | None = None,
) -> list[PresetIssue]:
    """校验预设有内容（配置 §4.2）。

    Args:
        document: 预设文件内容。
        known_rule_ids: 规则库中存在的 ruleId；``None`` 表示不校验（规则库未加载时）。

    Returns:
        问题列表；有 ``ruleIds`` 未知项时**整份拒绝**，不做部分导入。
    """
    issues: list[PresetIssue] = []

    if document.kind in RULE_BASED_KINDS:
        try:
            items = PresetRuleItems.model_validate(document.items)
        except ValueError as exc:
            issues.append(
                PresetIssue(
                    field="items",
                    code="CONFIG_SCHEMA_INVALID",
                    message=f"规则类预设的 items 形态非法：{exc}",
                )
            )
            return issues

        if not items.rule_ids:
            issues.append(
                PresetIssue(
                    field="items.ruleIds", code="CONFIG_SCHEMA_INVALID", message="预设不含任何规则"
                )
            )
        if known_rule_ids is not None:
            unknown = sorted(set(items.rule_ids) - known_rule_ids)
            if unknown:
                issues.append(
                    PresetIssue(
                        field="items.ruleIds",
                        code="RULEPACK_INVALID",
                        message=f"规则库中不存在这些 ruleId：{unknown}",
                    )
                )
        return issues

    model = fragment_model_for(document.kind)
    if model is not None:
        try:
            model.model_validate(document.items)
        except ValueError as exc:
            issues.append(
                PresetIssue(
                    field="items",
                    code="CONFIG_SCHEMA_INVALID",
                    message=f"{document.kind.value} 预设片段不合法：{exc}",
                )
            )
    return issues
