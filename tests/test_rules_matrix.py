"""依赖矩阵门禁（规则 §4）。

关键口径：手写硬约束是阻断依据；实测数据只补全，冲突时**保留手写**并记 ``matrix_conflict``。
"""

from __future__ import annotations

import json
from pathlib import Path

from uup2iso.infrastructure.rules import DependencyMatrix, load_matrix, merge_observed

RULES_DIR = Path(__file__).resolve().parents[1] / "docs" / "规范" / "rules"


def matrix(**overrides: object) -> DependencyMatrix:
    base: dict[str, object] = {"schemaVersion": "1.0"}
    base.update(overrides)
    return DependencyMatrix.model_validate(base)


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    result = load_matrix(tmp_path / "nope.json")
    assert result.capabilities == []
    assert result.blocked_combos == []
    assert result.schema_version == "1.0"


def test_bundled_matrix_loads() -> None:
    result = load_matrix(RULES_DIR / "dependency-matrix.json")
    assert result.schema_version == "1.0"
    # 骨架允许为空，但结构必须合法
    assert isinstance(result.capabilities, list)
    assert isinstance(result.blocked_combos, list)


def test_invalid_matrix_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        '{"schemaVersion": "1.0", "blockedCombos": [{"severity": "nope"}]}', encoding="utf-8"
    )
    try:
        load_matrix(path)
    except ValueError as exc:
        assert "severity" in str(exc) or "matrix" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("非法矩阵应当报错")


def test_merge_adds_observed_only_capability() -> None:
    hard = matrix()
    observed = matrix(capabilities=[{"name": "Cap.A", "providedBy": ["Pkg.1"]}])

    merged, warnings = merge_observed(hard, observed)
    assert warnings == ()
    assert merged.capability_map()["Cap.A"].provided_by == ["Pkg.1"]


def test_merge_conflict_keeps_hard_and_warns() -> None:
    hard = matrix(capabilities=[{"name": "Cap.A", "providedBy": ["Pkg.Hand"]}])
    observed = matrix(capabilities=[{"name": "Cap.A", "providedBy": ["Pkg.Observed"]}])

    merged, warnings = merge_observed(hard, observed)
    assert merged.capability_map()["Cap.A"].provided_by == ["Pkg.Hand"], "冲突时以手写为准"
    assert warnings == ("matrix_conflict capability=Cap.A keep=hard",)


def test_merge_unions_required_by_rules() -> None:
    hard = matrix(
        capabilities=[{"name": "Cap.A", "providedBy": ["P"], "requiredByRules": ["FR4-1"]}]
    )
    observed = matrix(
        capabilities=[{"name": "Cap.A", "providedBy": ["P"], "requiredByRules": ["FR5-2"]}]
    )

    merged, warnings = merge_observed(hard, observed)
    assert warnings == ()
    assert sorted(merged.capability_map()["Cap.A"].required_by_rules) == ["FR4-1", "FR5-2"]


def test_observed_blocked_combos_are_not_used_for_blocking() -> None:
    """实测数据不参与阻断（规则 §4.1：只有手写硬约束能阻断）。"""
    hard = matrix()
    observed = matrix(
        blockedCombos=[{"remove": ["FR4-1"], "breaks": ["某功能"], "severity": "block"}]
    )

    merged, _ = merge_observed(hard, observed)
    assert merged.blocked_combos == []


def test_combo_severity_helpers() -> None:
    data = matrix(
        blockedCombos=[
            {"remove": ["FR4-1"], "breaks": ["A"], "severity": "block"},
            {"remove": ["FR4-2"], "breaks": ["B"], "severity": "warning"},
        ]
    )
    assert len(data.block_combos()) == 1
    assert len(data.warn_combos()) == 1


def test_matrix_roundtrip_is_json_serializable() -> None:
    data = matrix(blockedCombos=[{"remove": ["FR4-1"], "breaks": ["A"], "severity": "block"}])
    payload = json.loads(data.model_dump_json(by_alias=True))
    assert payload["blockedCombos"][0]["severity"] == "block"
