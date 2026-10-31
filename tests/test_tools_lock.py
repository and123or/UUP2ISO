"""锁定清单门禁（引擎 §9.1）。

重点守两件事：``identitySha256`` 的自指定义必须按规范算（否则永远不相等）、
清单被改动手工改过时必须检出并回退内置副本。
"""

from __future__ import annotations

import json
from pathlib import Path

from uup2iso.infrastructure.tools import lock as lock_module
from uup2iso.infrastructure.tools.lock import (
    EMPTY_LOCK,
    LockedTool,
    ToolLock,
    compute_identity,
    load_lock,
    write_lock,
)


def sample_tools() -> list[LockedTool]:
    return [
        LockedTool(
            name="aria2c",
            url="https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2.zip",
            sha256="a" * 64,
            version="1.37.0",
            license="GPL-2.0",
            required=True,
        ),
        LockedTool(
            name="uup-converter-wimlib",
            url="",
            sha256="b" * 64,
            version="1.2",
            license="MIT",
            required=True,
            managed=False,
        ),
    ]


def test_identity_ignores_its_own_value() -> None:
    """§9.1 自指定义：置空该字段后序列化求哈希，写入当前值不影响结果。"""
    left = ToolLock(tools=sample_tools(), identity_sha256="")
    right = ToolLock(tools=sample_tools(), identity_sha256="deadbeef")
    assert compute_identity(left) == compute_identity(right)


def test_identity_changes_when_tools_change() -> None:
    base = ToolLock(tools=sample_tools())
    changed = ToolLock(tools=[LockedTool(name="7z", sha256="c" * 64)])
    assert compute_identity(base) != compute_identity(changed)


def test_load_lock_reports_tamper_and_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "tools.lock.json"
    lock = write_lock(path, sample_tools())

    ok = load_lock(path, expected_identity=lock.identity_sha256)
    assert ok.tampered is False
    assert ok.source == "file"
    assert len(ok.lock.tools) == 2

    bad = load_lock(path, expected_identity="0" * 64)
    assert bad.tampered is True
    assert bad.source == "builtin"
    assert bad.lock.tools == []
    assert any("manifest_tampered" in warning for warning in bad.warnings)


def test_hand_edit_is_detected(tmp_path: Path) -> None:
    """手工把哈希改小一点，身份校验必须发现。"""
    path = tmp_path / "tools.lock.json"
    lock = write_lock(path, sample_tools())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tools"][0]["sha256"] = "d" * 64
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = load_lock(path, expected_identity=lock.identity_sha256)
    assert result.tampered is True


def test_missing_file_returns_builtin(tmp_path: Path) -> None:
    result = load_lock(tmp_path / "nope.json", expected_identity="x")
    assert result.lock is EMPTY_LOCK
    assert result.source == "builtin"
    assert "manifest_missing" in result.warnings


def test_unreadable_file_returns_builtin(tmp_path: Path) -> None:
    path = tmp_path / "tools.lock.json"
    path.write_text("{ not json", encoding="utf-8")
    result = load_lock(path)
    assert result.tampered is True
    assert result.source == "builtin"


def test_write_lock_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "tools.lock.json"
    lock = write_lock(path, sample_tools())

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["identitySha256"] == lock.identity_sha256
    assert payload["schemaVersion"] == "1.0"
    assert load_lock(path, expected_identity=lock.identity_sha256).tampered is False


def test_entry_lookup_and_managed_names() -> None:
    lock = ToolLock(tools=sample_tools())
    assert lock.entry_for("aria2c") is not None
    assert lock.entry_for("7z") is None
    # 转换器 managed=False，不算「由清单管理」
    assert lock.managed_names() == frozenset({"aria2c"})


def test_shipped_lock_is_valid() -> None:
    """随包清单必须能被解析、且身份自洽（空注册表也允许）。"""
    repo_lock = Path(__file__).resolve().parents[1] / "tools" / "tools.lock.json"
    result = load_lock(
        repo_lock,
        expected_identity=compute_identity(
            ToolLock.model_validate(json.loads(repo_lock.read_text(encoding="utf-8")))
        ),
    )
    assert result.tampered is False
    assert lock_module.LOCK_SCHEMA_VERSION == "1.0"
