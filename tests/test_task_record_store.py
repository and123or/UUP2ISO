"""任务记录与保留策略门禁（编排 §10）。

验收：保留最近 50 个；**只删中间物**（不碰输出目录）；运行中的任务永不清理。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from uup2iso.core.models.checkpoint import TaskRuntime
from uup2iso.core.models.enums import TaskState
from uup2iso.infrastructure.storage import TaskRecordStore

BASE = datetime(2026, 11, 1, 12, 0, 0)


def make_store(tmp_path: Path) -> TaskRecordStore:
    return TaskRecordStore(tmp_path / "tasks", work_root=tmp_path / "work")


def write_record(
    store: TaskRecordStore,
    task_id: str,
    *,
    status: TaskState = TaskState.SUCCEEDED,
    offset_minutes: int = 0,
    with_work: bool = True,
) -> Path:
    stamp = BASE + timedelta(minutes=offset_minutes)
    store.write_runtime(
        task_id,
        TaskRuntime(status=status, startedAt=stamp, finishedAt=stamp),
    )
    if with_work:
        work = store.work_dir_for(task_id)
        assert work is not None
        (work / "image").mkdir(parents=True, exist_ok=True)
        (work / "image" / "install.wim").write_bytes(b"WIM")
    return store.path_for(task_id)


# --- 读写 -------------------------------------------------------------------


def test_write_runtime_preserves_config_fields(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    path = store.path_for("7f3a1b2c")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"name": "我的任务", "input": {"mode": "image"}}), encoding="utf-8")

    store.write_runtime("7f3a1b2c", TaskRuntime(status=TaskState.RUNNING, startedAt=BASE))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["name"] == "我的任务"
    assert payload["input"] == {"mode": "image"}
    assert payload["runtime"]["status"] == "running"


def test_runtime_roundtrip(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    write_record(store, "aaaa1111", status=TaskState.FAILED)

    runtime = store.runtime_of("aaaa1111")
    assert runtime is not None
    assert runtime.status is TaskState.FAILED
    assert runtime.started_at is not None


def test_missing_record_reads_empty(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    assert store.read("ffff0000") == {}
    assert store.runtime_of("ffff0000") is None


def test_invalid_task_id_is_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.path_for("../escape")


# --- 列表 -------------------------------------------------------------------


def test_summaries_skip_non_task_files(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    write_record(store, "aaaa1111")
    (tmp_path / "tasks" / "notes.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tasks" / "broken.json").write_text("{oops", encoding="utf-8")

    assert [item.task_id for item in store.summaries()] == ["aaaa1111"]


# --- 保留策略 ---------------------------------------------------------------


def test_prune_keeps_newest_and_removes_old(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    for index in range(5):
        write_record(store, f"0000000{index}", offset_minutes=index)

    report = store.prune(retention=3)

    assert report.kept == 3
    assert set(report.pruned) == {"00000000", "00000001"}
    assert all(not store.path_for(task_id).exists() for task_id in report.pruned)
    for task_id in report.pruned:
        work = store.work_dir_for(task_id)
        assert work is not None and not work.exists(), "工作目录必须一并清理"
    assert store.path_for("00000004").exists()


def test_prune_never_touches_output_dir(tmp_path: Path) -> None:
    """只删中间物：用户输出目录里的最终产物绝不能碰（编排 §10）。"""
    store = make_store(tmp_path)
    output = tmp_path / "out" / "Win11.iso"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"ISO")

    for index in range(4):
        write_record(store, f"0000000{index}", offset_minutes=index)

    store.prune(retention=1)

    assert output.is_file(), "最终产物不属于清理范围"


def test_running_task_is_never_pruned(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    # 运行中的任务故意放成"最旧"，它本该被 retention 裁掉，但必须被保护
    write_record(store, "00000009", status=TaskState.RUNNING, offset_minutes=0)
    write_record(store, "00000000", offset_minutes=5)

    report = store.prune(retention=1)

    assert "00000009" in report.skipped_running
    assert store.path_for("00000009").exists(), "正在运行的任务永不清理"
    assert store.path_for("00000000").exists(), "保留最新的 1 个"


def test_keep_argument_protects_task(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    for index in range(3):
        write_record(store, f"0000000{index}", offset_minutes=index)

    report = store.prune(retention=1, keep={"00000000"})

    assert store.path_for("00000000").exists()
    assert "00000000" not in report.pruned


def test_prune_without_work_root_only_removes_records(tmp_path: Path) -> None:
    store = TaskRecordStore(tmp_path / "tasks")  # 不传 work_root
    write_record(store, "00000000", with_work=False)
    write_record(store, "00000001", with_work=False, offset_minutes=1)

    assert store.prune(retention=1).pruned_work_dirs == ()


def test_prune_empty_directory_is_harmless(tmp_path: Path) -> None:
    store = TaskRecordStore(tmp_path / "nope", work_root=tmp_path / "work")
    report = store.prune()
    assert report.kept == 0
    assert report.pruned == ()
