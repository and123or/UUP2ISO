"""检查点持久化门禁（编排 §5、§12-8）。

两个硬点：**原子写**（不留半截 JSON、不留 tmp）与**损坏可识别**（不把程序卡死在启动）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from uup2iso.core.models.checkpoint import Checkpoint, StageRecord
from uup2iso.core.models.enums import StageState
from uup2iso.infrastructure.storage import CheckpointStore

NOW = datetime(2026, 11, 1, 12, 0, 0)


def make_store(tmp_path: Path) -> CheckpointStore:
    return CheckpointStore(tmp_path / "work" / "7f3a1b2c")


def test_initialize_writes_running_checkpoint(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    checkpoint = store.initialize(
        task_id="7f3a1b2c",
        config_fingerprint="cfg123",
        input_fingerprint="in456",
        started_at=NOW,
    )

    assert store.exists()
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["taskId"] == "7f3a1b2c"
    assert payload["configFingerprint"] == "cfg123"
    assert payload["inputFingerprint"] == "in456"
    assert payload["status"] == "running"
    assert payload["schemaVersion"] == "1.0"
    assert checkpoint.status.value == "running"


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    checkpoint = store.initialize(task_id="t1", config_fingerprint="cfg", started_at=NOW)
    checkpoint.stages["Convert"] = StageRecord(status=StageState.RUNNING, attempts=2)
    store.save(checkpoint, now=NOW)

    loaded = store.load()
    assert loaded.corrupt is False
    assert loaded.checkpoint is not None
    assert loaded.checkpoint.stages["Convert"].attempts == 2
    assert loaded.checkpoint.stages["Convert"].status is StageState.RUNNING


def test_save_is_atomic_and_leaves_no_tmp(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    checkpoint = store.initialize(task_id="t1", config_fingerprint="cfg", started_at=NOW)
    store.save(checkpoint, now=NOW)

    leftovers = list(store.path.parent.glob("*.tmp"))
    assert leftovers == [], "写完必须只剩 checkpoint.json"


def test_missing_file_is_not_corrupt(tmp_path: Path) -> None:
    result = make_store(tmp_path).load()
    assert result.checkpoint is None
    assert result.corrupt is False


def test_broken_json_is_reported_not_raised(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"taskId": "x", ', encoding="utf-8")

    result = store.load()
    assert result.corrupt is True
    assert result.checkpoint is None
    assert "无法解析" in result.reason


def test_structurally_invalid_is_reported(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"status": "unknown-status"}), encoding="utf-8")

    result = store.load()
    assert result.corrupt is True
    assert "结构不合法" in result.reason


def test_heartbeat_updates_timestamp(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    checkpoint = store.initialize(task_id="t1", config_fingerprint="cfg", started_at=NOW)
    first = checkpoint.updated_at

    later = datetime(2026, 11, 1, 12, 1, 0)
    store.heartbeat(checkpoint, now=later)

    reloaded = store.load().checkpoint
    assert reloaded is not None
    assert reloaded.updated_at is not None and reloaded.updated_at > first


def test_unknown_fields_are_preserved(tmp_path: Path) -> None:
    """前向兼容：新版本多写的字段不该在旧版本读写后消失。"""
    store = make_store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    payload = Checkpoint(taskId="t1", status="running").model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    payload["futureField"] = {"a": 1}
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = store.load().checkpoint
    assert loaded is not None
    assert loaded.model_extra == {"futureField": {"a": 1}}
