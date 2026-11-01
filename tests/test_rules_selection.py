"""执行前阻断判定门禁（规则 §4.3）。

逐项阻断：只去掉有问题的项并说明原因，不因为一条冲突让整个阶段失败。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from uup2iso.core.models.rulepack import RuleLibrary
from uup2iso.infrastructure.rules import (
    BlockCode,
    DependencyMatrix,
    evaluate_selection,
    load_library,
)

RULES_DIR = Path(__file__).resolve().parents[1] / "docs" / "规范" / "rules"


def rule(rule_id: str, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": rule_id,
        "name": rule_id,
        "domain": "FR4",
        "group": "Test",
        "level": "low",
        "actions": [{"type": "runCommand", "cmd": "echo hi"}],
    }
    data.update(overrides)
    return data


@pytest.fixture
def library(tmp_path: Path) -> RuleLibrary:
    shutil.copy2(RULES_DIR / "rules.schema.json", tmp_path / "rules.schema.json")
    payload = {
        "schemaVersion": "1.0",
        "updated": "2026-11-01T00:00:00Z",
        "source": "test",
        "rules": [
            rule("FR4-A01", order=10),
            rule("FR4-B01", order=20, requires=["FR4-A01"]),
            rule("FR4-C01", order=30, conflicts=["FR4-A01"]),
            rule(
                "FR4-D01",
                order=40,
                level="high",
                verify={"type": "fileAbsent", "path": "C:/x"},
                reversible=False,
                recovery="重装系统可恢复",
            ),
        ],
    }
    (tmp_path / "test-rules.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return load_library(rules_dir=tmp_path)


def test_requires_satisfied_allows_both(library: RuleLibrary) -> None:
    result = evaluate_selection({"FR4-A01", "FR4-B01"}, library)
    assert result.allowed == ("FR4-A01", "FR4-B01")
    assert not result.has_blocked


def test_missing_requires_blocks_dependent(library: RuleLibrary) -> None:
    result = evaluate_selection({"FR4-B01"}, library)

    assert result.allowed == ()
    assert result.blocked[0].rule_id == "FR4-B01"
    assert result.blocked[0].code is BlockCode.MISSING_REQUIRES
    assert "FR4-A01" in result.blocked[0].message


def test_conflict_blocks_later_rule(library: RuleLibrary) -> None:
    result = evaluate_selection({"FR4-A01", "FR4-C01"}, library)

    assert result.allowed == ("FR4-A01",), "冲突时保留先出现（order 更小）的那条"
    assert result.blocked[0].rule_id == "FR4-C01"
    assert result.blocked[0].code is BlockCode.CONFLICT
    assert "二选一" in result.blocked[0].message


def test_high_risk_requires_confirmation(library: RuleLibrary) -> None:
    blocked = evaluate_selection({"FR4-D01"}, library)
    assert blocked.blocked[0].code is BlockCode.HIGH_RISK_UNCONFIRMED

    confirmed = evaluate_selection({"FR4-D01"}, library, confirmed_high_risk={"FR4-D01"})
    assert confirmed.allowed == ("FR4-D01",)
    assert not confirmed.has_blocked


def test_unknown_rule_blocked(library: RuleLibrary) -> None:
    result = evaluate_selection({"FR4-ZZZ99"}, library)
    assert result.blocked[0].code is BlockCode.UNKNOWN_RULE


def test_blocked_combo_with_block_severity(library: RuleLibrary) -> None:
    matrix = DependencyMatrix.model_validate(
        {
            "schemaVersion": "1.0",
            "blockedCombos": [
                {
                    "remove": ["FR4-A01", "FR4-B01"],
                    "breaks": ["Windows 搜索"],
                    "severity": "block",
                    "evidence": "实测：删除后搜索无结果",
                }
            ],
        }
    )
    result = evaluate_selection({"FR4-A01", "FR4-B01"}, library, matrix)

    assert result.allowed == ()
    assert {item.code for item in result.blocked} == {BlockCode.BLOCKED_COMBO}
    assert "Windows 搜索" in result.blocked[0].message


def test_blocked_combo_with_warning_severity_requires_confirmation(library: RuleLibrary) -> None:
    matrix = DependencyMatrix.model_validate(
        {
            "schemaVersion": "1.0",
            "blockedCombos": [{"remove": ["FR4-A01"], "breaks": ["某体验"], "severity": "warning"}],
        }
    )
    result = evaluate_selection({"FR4-A01"}, library, matrix)

    assert result.allowed == ("FR4-A01",), "warning 级组合允许执行"
    assert result.warnings and "某体验" in result.warnings[0]
    assert result.requires_confirmation == ("FR4-A01",)


def test_allowed_order_follows_order_then_id(library: RuleLibrary) -> None:
    result = evaluate_selection(
        {"FR4-D01", "FR4-B01", "FR4-A01"}, library, confirmed_high_risk={"FR4-D01"}
    )
    assert result.allowed == ("FR4-A01", "FR4-B01", "FR4-D01")


def test_empty_selection_is_harmless(library: RuleLibrary) -> None:
    result = evaluate_selection(set(), library)
    assert result.allowed == ()
    assert not result.has_blocked
    assert result.warnings == ()


def test_bundled_library_can_evaluate_a_preset() -> None:
    """用随包规则库跑一遍真实预设，确保选择器与真实数据兼容。"""
    bundled = load_library(rules_dir=RULES_DIR, engine_version="0.1.15")
    safe = [rule.id for rule in bundled.by_preset("safe")]
    highs = [rule.id for rule in bundled.by_preset("safe") if rule.is_high_risk]

    result = evaluate_selection(safe, bundled, confirmed_high_risk=set(highs))
    assert len(result.allowed) == len(safe)
    assert not result.has_blocked
