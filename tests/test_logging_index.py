"""按任务日志索引与合并门禁（日志 §7.3）。

这是诊断包「不漏、不多」的基础：索引驱动取文件、逐行过滤 `task=<id>`、行数必须对得上。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uup2iso.infrastructure.logging import (
    LOG_FILES_KEY,
    TaskLogIndex,
    collect_task_log,
)

TASK = "7f3a1b2c"
OTHER = "deadbeef"


def make_index(tmp_path: Path) -> TaskLogIndex:
    return TaskLogIndex(tmp_path / "tasks")


def write_log(path: Path, *, task_lines: int, other_lines: int = 0, prefix: str = "run") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"2026-11-01 10:00:00.000 [INFO ] [Convert] x: 本任务 | task={TASK}"]
    lines = lines * task_lines
    lines += [f"2026-11-01 10:00:00.000 [INFO ] [Convert] x: 别的任务 | task={OTHER}"] * other_lines
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- 索引本身 ---------------------------------------------------------------


def test_record_appends_and_dedupes(tmp_path: Path) -> None:
    index = make_index(tmp_path)

    first = index.record(TASK, run_file="run_a.log", audit_file="audit_a.log")
    assert first.log_files == ("run_a.log",)
    assert first.audit_files == ("audit_a.log",)

    second = index.record(TASK, run_file="run_a.log", audit_file="audit_a.log")
    assert second.log_files == ("run_a.log",), "重复接触同一文件不应重复记录"

    third = index.record(TASK, run_file="run_b.log", audit_file="audit_b.log")
    assert third.log_files == ("run_a.log", "run_b.log"), "保序追加"
    assert third.audit_files == ("audit_a.log", "audit_b.log")


def test_record_preserves_other_task_fields(tmp_path: Path) -> None:
    """索引写在任务配置里，不能把其它字段冲掉。"""
    path = tmp_path / "tasks" / f"{TASK}.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"name": "我的任务", "input": {"mode": "image"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    index = make_index(tmp_path)
    index.record(TASK, run_file="run_a.log")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["name"] == "我的任务"
    assert payload["input"] == {"mode": "image"}
    assert payload[LOG_FILES_KEY] == ["run_a.log"]


def test_mark_resumed_is_persisted(tmp_path: Path) -> None:
    index = make_index(tmp_path)
    index.record(TASK, run_file="run_a.log")
    index.mark_resumed(TASK)

    assert index.read(TASK).resumed is True


def test_missing_task_file_reads_empty(tmp_path: Path) -> None:
    record = make_index(tmp_path).read(TASK)
    assert record.log_files == ()
    assert record.resumed is False


def test_invalid_task_id_is_rejected(tmp_path: Path) -> None:
    index = make_index(tmp_path)
    with pytest.raises(ValueError):
        index.path_for("../../etc/passwd")


# --- 合并（诊断包的核心） ---------------------------------------------------


def test_collect_filters_and_counts(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    first = write_log(log_dir / "run_a.log", task_lines=3, other_lines=2)
    second = write_log(log_dir / "run_b.log", task_lines=4)

    index = make_index(tmp_path)
    index.record(TASK, run_file=first.name)
    index.record(TASK, run_file=second.name)

    merged = collect_task_log(TASK, index=index, log_dir=log_dir)

    assert merged.total_lines == 7
    assert merged.exact is True
    assert merged.missing == ()
    assert [source.matched_lines for source in merged.sources] == [3, 4]
    assert all(len(source.sha256) == 64 for source in merged.sources)
    assert merged.text.count("task=" + OTHER) == 0, "不能把别的任务的行带进来"
    assert "# source=run_a.log" in merged.text


def test_missing_file_marks_inexact(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    existing = write_log(log_dir / "run_a.log", task_lines=2)

    index = make_index(tmp_path)
    index.record(TASK, run_file=existing.name)
    index.record(TASK, run_file="run_gone.log")

    merged = collect_task_log(TASK, index=index, log_dir=log_dir)

    assert merged.exact is False
    assert merged.missing == ("run_gone.log",)
    assert merged.total_lines == 2


def test_no_index_falls_back_to_current_and_marks_inexact(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    current = write_log(log_dir / "run_now.log", task_lines=2, other_lines=1)

    merged = collect_task_log(
        TASK,
        index=make_index(tmp_path),
        log_dir=log_dir,
        current=(current.name,),
    )

    assert merged.exact is False, "索引缺失时必须提示不完整"
    assert merged.total_lines == 2
    assert "别的任务" not in merged.text


def test_audit_kind_uses_audit_index(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    audit = write_log(log_dir / "audit_a.log", task_lines=2)

    index = make_index(tmp_path)
    index.record(TASK, run_file="run_a.log", audit_file=audit.name)

    merged = collect_task_log(TASK, index=index, log_dir=log_dir, kind="audit")
    assert merged.total_lines == 2
    assert merged.sources[0].file == "audit_a.log"


def test_empty_sources_give_empty_text(tmp_path: Path) -> None:
    merged = collect_task_log(TASK, index=make_index(tmp_path), log_dir=tmp_path / "logs")
    assert merged.text == ""
    assert merged.total_lines == 0
