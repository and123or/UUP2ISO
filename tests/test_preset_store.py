"""预设存储门禁（配置 §4）。

核心两条：未知 ``ruleId`` 整份拒绝（不静默丢项）、配置类预设必须过片段校验。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from uup2iso.core.errors import ConfigSchemaError
from uup2iso.core.models.enums import PresetKind
from uup2iso.infrastructure.storage import PresetStore

SLIM_KIND = PresetKind.SLIM
INTEGRATE_KIND = PresetKind.INTEGRATE


def call(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def make_store(tmp_path: Path, known: set[str] | None = None) -> PresetStore:
    return PresetStore(tmp_path / "presets", known_rule_ids=known)


def slim_items() -> dict[str, object]:
    return {
        "ruleIds": ["FR4-SL-A01", "FR4-SL-C03"],
        "params": {"FR4-SL-C03": {"keepDays": 7}},
    }


# --- 规则类预设 -------------------------------------------------------------


def test_save_list_load_roundtrip(tmp_path: Path) -> None:
    store = make_store(tmp_path, known={"FR4-SL-A01", "FR4-SL-C03"})

    path = call(store.save(SLIM_KIND, "slim-safe", {"items": slim_items(), "name": "安全精简"}))
    assert Path(path).is_file()

    assert call(store.list(SLIM_KIND)) == ("slim-safe",)

    loaded = call(store.load(SLIM_KIND, "slim-safe"))
    assert loaded["kind"] == "slim"
    assert loaded["items"]["ruleIds"] == ["FR4-SL-A01", "FR4-SL-C03"]


def test_unknown_rule_id_rejected(tmp_path: Path) -> None:
    """配置 §4.2：未知 ruleId 一律拒绝并列出，不允许部分导入。"""
    store = make_store(tmp_path, known={"FR4-SL-A01"})

    with pytest.raises(ConfigSchemaError) as caught:
        call(store.save(SLIM_KIND, "bad", {"items": slim_items()}))
    assert "FR4-SL-C03" in caught.value.message

    assert call(store.list(SLIM_KIND)) == (), "被拒绝的预设不应落盘"


def test_empty_rule_list_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(ConfigSchemaError) as caught:
        call(store.save(SLIM_KIND, "empty", {"items": {"ruleIds": []}}))
    assert "不含任何规则" in caught.value.message


def test_rule_validation_skipped_without_rule_library(tmp_path: Path) -> None:
    """规则库未加载时不校验 ruleId（``known_rule_ids=None``）。"""
    store = make_store(tmp_path, known=None)
    call(store.save(SLIM_KIND, "whatever", {"items": slim_items()}))
    assert call(store.list(SLIM_KIND)) == ("whatever",)


# --- 配置类预设 -------------------------------------------------------------


def test_integrate_preset_validates_fragment(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    items = {
        "updates": {"paths": ["D:/Updates"], "recursive": True, "order": "auto"},
        "drivers": {"paths": [], "recursive": True, "requireSignature": True},
        "language": {"add": [], "remove": [], "fod": []},
        "registry": {"imports": []},
    }
    call(store.save(INTEGRATE_KIND, "lab", {"items": items, "name": "实验室"}))
    loaded = call(store.load(INTEGRATE_KIND, "lab"))
    assert loaded["items"]["updates"]["paths"] == ["D:/Updates"]


def test_integrate_preset_bad_fragment_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(ConfigSchemaError):
        call(store.save(INTEGRATE_KIND, "bad", {"items": {"updates": "不是对象"}}))


def test_kind_mismatch_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(ConfigSchemaError) as caught:
        call(store.save(SLIM_KIND, "x", {"kind": "optimize", "items": slim_items()}))
    assert "类别不匹配" in caught.value.message


# --- 导入导出 ---------------------------------------------------------------


def test_export_then_import(tmp_path: Path) -> None:
    store = make_store(tmp_path, known={"FR4-SL-A01", "FR4-SL-C03"})
    call(store.save(SLIM_KIND, "slim-safe", {"items": slim_items()}))

    export_path = tmp_path / "export" / "slim-safe.json"
    call(store.export_(SLIM_KIND, "slim-safe", export_path))
    assert export_path.is_file()

    other = PresetStore(tmp_path / "other-presets", known_rule_ids={"FR4-SL-A01", "FR4-SL-C03"})
    imported = call(other.import_(SLIM_KIND, export_path))
    assert imported == "slim-safe"
    assert call(other.list(SLIM_KIND)) == ("slim-safe",)


def test_import_rejects_unknown_rule_at_source(tmp_path: Path) -> None:
    store = make_store(tmp_path, known={"FR4-SL-A01"})
    source = tmp_path / "incoming.json"
    source.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "kind": "slim",
                "id": "incoming",
                "items": {"ruleIds": ["FR4-SL-ZZZ"]},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigSchemaError) as caught:
        call(store.import_(SLIM_KIND, source))
    assert "FR4-SL-ZZZ" in caught.value.message
    assert call(store.list(SLIM_KIND)) == ()


def test_missing_preset_raises_file_not_found(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(FileNotFoundError):
        call(store.load(SLIM_KIND, "nope"))


def test_list_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    assert call(store.list(PresetKind.OPTIMIZE)) == ()
