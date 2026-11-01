"""规则库加载门禁（规则 §2、§8）。

重点守两条：**不做部分加载**（一条不合规就整体拒绝）、**高风险必须可验证**。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from uup2iso.core.errors import ConfigError, ErrorCode
from uup2iso.infrastructure.rules import load_library

RULES_DIR = Path(__file__).resolve().parents[1] / "docs" / "规范" / "rules"
SCHEMA = RULES_DIR / "rules.schema.json"


def base_rule(rule_id: str, **overrides: object) -> dict[str, object]:
    rule: dict[str, object] = {
        "id": rule_id,
        "name": f"规则 {rule_id}",
        "domain": "FR4",
        "group": "Test",
        "level": "low",
        "actions": [{"type": "runCommand", "cmd": "echo hi"}],
    }
    rule.update(overrides)
    return rule


def write_pack(directory: Path, name: str, rules: list[dict[str, object]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCHEMA, directory / "rules.schema.json")
    payload = {
        "schemaVersion": "1.0",
        "updated": "2026-11-01T00:00:00Z",
        "source": "test",
        "rules": rules,
    }
    path = directory / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# --- 随包规则库 -------------------------------------------------------------


def test_bundled_library_loads() -> None:
    library = load_library(rules_dir=RULES_DIR, engine_version="0.1.15")

    assert len(library.packs) == 2
    assert library.rule_count == 30
    ids = library.ids()
    assert len(ids) == len(set(ids))
    assert all(rule_id.startswith(("FR4-", "FR5-")) for rule_id in ids)


def test_bundled_presets_are_consistent() -> None:
    library = load_library(rules_dir=RULES_DIR, engine_version="0.1.15")
    aggressive = library.by_preset("aggressive")
    safe = library.by_preset("safe")

    assert len(aggressive) == library.rule_count, "aggressive 应包含全部规则"
    assert 0 < len(safe) < len(aggressive)
    # aggressive 里的高风险项必须是「有验证手段」的，否则加载期就该被拒
    assert all(rule.verify is not None for rule in aggressive if rule.is_high_risk)


def test_high_risk_rules_are_verifiable() -> None:
    """规则 §2.2：high 必须有 verify，不可逆必须有 recovery。"""
    library = load_library(rules_dir=RULES_DIR, engine_version="0.1.15")
    highs = [rule for rule in library.rules.values() if rule.is_high_risk]

    assert highs, "当前规则库应至少有一条高风险规则"
    for rule in highs:
        assert rule.verify is not None, f"{rule.id} 缺 verify"
        if not rule.reversible:
            assert rule.recovery, f"{rule.id} 不可逆却无 recovery"


def test_orders_are_unique_enough_to_be_deterministic() -> None:
    """执行顺序按 (order, id) 排序，必须可复现。"""
    library = load_library(rules_dir=RULES_DIR, engine_version="0.1.15")
    first = library.ids()
    second = load_library(rules_dir=RULES_DIR, engine_version="0.1.15").ids()
    assert first == second


# --- 非法规则（整体拒绝） ---------------------------------------------------


def test_missing_verify_on_high_risk_rejected(tmp_path: Path) -> None:
    write_pack(tmp_path, "bad-rules.json", [base_rule("FR4-T01", level="high")])
    with pytest.raises(ConfigError) as caught:
        load_library(rules_dir=tmp_path)

    assert caught.value.code is ErrorCode.RULEPACK_INVALID
    assert "FR4-T01" in caught.value.message
    assert "verify" in caught.value.message


def test_irreversible_without_recovery_rejected(tmp_path: Path) -> None:
    rule = base_rule(
        "FR4-T02",
        level="high",
        verify={"type": "fileAbsent", "path": "C:/x"},
    )  # reversible 缺省 false
    write_pack(tmp_path, "bad-rules.json", [rule])

    with pytest.raises(ConfigError) as caught:
        load_library(rules_dir=tmp_path)
    assert "recovery" in caught.value.message


def test_duplicate_id_across_packs_rejected(tmp_path: Path) -> None:
    write_pack(tmp_path, "a-rules.json", [base_rule("FR4-DUP01")])
    write_pack(tmp_path, "b-rules.json", [base_rule("FR4-DUP01")])

    with pytest.raises(ConfigError) as caught:
        load_library(rules_dir=tmp_path)
    assert "id 重复" in caught.value.message


def test_dangling_reference_rejected(tmp_path: Path) -> None:
    write_pack(tmp_path, "a-rules.json", [base_rule("FR4-REF01", requires=["FR4-NOPE99"])])

    with pytest.raises(ConfigError) as caught:
        load_library(rules_dir=tmp_path)
    assert "FR4-NOPE99" in caught.value.message


def test_schema_violation_rejected(tmp_path: Path) -> None:
    rule = base_rule("FR4-SCH01")
    rule.pop("actions")  # schema 必填
    write_pack(tmp_path, "a-rules.json", [rule])

    with pytest.raises(ConfigError) as caught:
        load_library(rules_dir=tmp_path)
    assert caught.value.code is ErrorCode.RULEPACK_INVALID


def test_cycle_detected(tmp_path: Path) -> None:
    write_pack(
        tmp_path,
        "a-rules.json",
        [
            base_rule("FR4-CYCA", requires=["FR4-CYCB"]),
            base_rule("FR4-CYCB", requires=["FR4-CYCC"]),
            base_rule("FR4-CYCC", requires=["FR4-CYCA"]),
        ],
    )

    with pytest.raises(ConfigError) as caught:
        load_library(rules_dir=tmp_path)
    assert caught.value.code is ErrorCode.RULEPACK_CYCLIC
    assert "→" in caught.value.message


def test_self_cycle_detected(tmp_path: Path) -> None:
    write_pack(tmp_path, "a-rules.json", [base_rule("FR4-SELF1", requires=["FR4-SELF1"])])
    with pytest.raises(ConfigError) as caught:
        load_library(rules_dir=tmp_path)
    assert caught.value.code is ErrorCode.RULEPACK_CYCLIC


def test_empty_pack_rejected(tmp_path: Path) -> None:
    write_pack(tmp_path, "a-rules.json", [])
    with pytest.raises(ConfigError) as caught:
        load_library(rules_dir=tmp_path)
    assert "非空" in caught.value.message


# --- minEngine 与外置更新 ---------------------------------------------------


def test_min_engine_rules_are_skipped_not_rejected(tmp_path: Path) -> None:
    write_pack(
        tmp_path,
        "a-rules.json",
        [
            base_rule("FR4-OK01"),
            base_rule("FR4-NEW01", minEngine="99.0.0"),
        ],
    )

    library = load_library(rules_dir=tmp_path, engine_version="0.1.15")
    assert "FR4-NEW01" in library.skipped_min_engine
    assert library.get("FR4-NEW01") is None
    assert library.get("FR4-OK01") is not None


def test_min_engine_satisfied_is_loaded(tmp_path: Path) -> None:
    write_pack(tmp_path, "a-rules.json", [base_rule("FR4-NEW02", minEngine="0.1.0")])
    library = load_library(rules_dir=tmp_path, engine_version="0.1.15")
    assert library.get("FR4-NEW02") is not None


def test_repo_library_is_self_consistent() -> None:
    """仓库内的规则与矩阵必须能一起加载（防止规则改动后 CI 才炸）。"""
    library = load_library(rules_dir=RULES_DIR, engine_version="0.1.15")
    matrix_path = RULES_DIR / "dependency-matrix.json"
    assert matrix_path.is_file()
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == "1.0"
    assert library.rule_count > 0
