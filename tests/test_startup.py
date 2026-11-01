"""启动序列门禁（日志 §2.2、§4.3、§6.1）。

核心验收：**冲突实例不产生日志文件、不执行清理、不写审计**。
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from uup2iso.application.startup import (
    ALREADY_RUNNING_PAYLOAD,
    StartupConflictError,
    acquire_instance,
    bootstrap,
    default_log_dir,
)
from uup2iso.infrastructure.logging import LogThresholds, is_readonly
from uup2iso.infrastructure.single_instance import SingleInstanceGuard

DAY = 86400


def unique_name() -> str:
    return rf"Local\UUP2ISO.Test.Startup.{uuid.uuid4().hex}"


def test_bootstrap_creates_logs_and_reports_level(tmp_path: Path) -> None:
    name = unique_name()
    report = bootstrap(entry="cli", log_dir=tmp_path / "logs", mutex_name=name)
    try:
        assert report.sink.paths.run.is_file()
        assert report.sink.paths.audit.is_file()
        assert report.level == "INFO", "CLI 默认 INFO（日志 §3）"
        text = report.sink.paths.run.read_text(encoding="utf-8")
        assert "event=startup" in text
        assert "entry=cli" in text
        assert "log_level_source=" in text
    finally:
        report.close()


def test_gui_source_run_defaults_to_debug(tmp_path: Path) -> None:
    report = bootstrap(
        entry="gui", log_dir=tmp_path / "logs", mutex_name=unique_name(), frozen=False
    )
    try:
        assert report.level == "DEBUG"
    finally:
        report.close()


def test_conflict_creates_no_logs(tmp_path: Path) -> None:
    name = unique_name()
    first = bootstrap(entry="cli", log_dir=tmp_path / "first", mutex_name=name)
    second_dir = tmp_path / "second"
    try:
        with pytest.raises(StartupConflictError) as caught:
            bootstrap(entry="cli", log_dir=second_dir, mutex_name=name)

        assert caught.value.exit_code == 5
        assert caught.value.payload == ALREADY_RUNNING_PAYLOAD
        assert not second_dir.exists() or list(second_dir.iterdir()) == [], (
            "冲突实例绝不能创建日志文件（日志 §6.1）"
        )
    finally:
        first.close()


def test_conflict_does_not_prune_existing_logs(tmp_path: Path) -> None:
    """冲突时连清理都不许做——否则第二个进程会删掉第一个正在写的日志。"""
    name = unique_name()
    first_dir = tmp_path / "logs"
    first = bootstrap(entry="cli", log_dir=first_dir, mutex_name=name)
    try:
        stale = first_dir / "run_20200101-000000.log"
        stale.write_text("old\n", encoding="utf-8")
        os.utime(stale, (time.time() - 400 * DAY,) * 2)

        with pytest.raises(StartupConflictError):
            bootstrap(
                entry="cli",
                log_dir=first_dir,
                mutex_name=name,
                run_thresholds=LogThresholds(max_days=1),
            )

        assert stale.exists(), "冲突实例不得执行清理"
    finally:
        first.close()


def test_startup_fixes_audit_readonly(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    old_audit = logs / "audit_20260101-000000.log"
    old_audit.write_text("x\n", encoding="utf-8")

    report = bootstrap(entry="cli", log_dir=logs, mutex_name=unique_name())
    try:
        assert is_readonly(old_audit)
        assert old_audit in report.readonly_fixed
    finally:
        report.close()


def test_startup_prunes_and_logs_audit_pruned(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    for index in range(6):
        path = logs / f"audit_2026100{index + 1}-100000.log"
        path.write_text("x\n", encoding="utf-8")
        os.utime(path, (time.time() - (10 - index) * DAY,) * 2)

    report = bootstrap(
        entry="cli",
        log_dir=logs,
        mutex_name=unique_name(),
        audit_thresholds=LogThresholds(max_total_mb=20, max_days=365, max_files=5),
    )
    try:
        assert report.pruned.audit_deleted, "审计池超过 5 个，应按数量删最旧"
        run_text = report.sink.paths.run.read_text(encoding="utf-8")
        assert "event=audit_pruned" in run_text, "被删的审计必须在运行日志留痕"
        assert "event=prune" in run_text
    finally:
        report.close()


def test_unwritable_log_dir_raises_and_releases_guard(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    name = unique_name()

    from uup2iso.infrastructure.logging import AuditWriteError

    with pytest.raises(AuditWriteError):
        bootstrap(entry="cli", log_dir=blocker / "logs", mutex_name=name)

    # 守卫必须已释放，否则一次失败启动会把程序永久锁死
    guard = SingleInstanceGuard(name)
    try:
        assert guard.acquire() is True
    finally:
        guard.release()


def test_adopt_preset_session(tmp_path: Path) -> None:
    """GUI 会先抢再决定「激活还是继续」，bootstrap 应复用已持有的会话。"""
    session = acquire_instance(unique_name())
    assert session is not None and session.held

    report = bootstrap(entry="gui", log_dir=tmp_path / "logs", session=session, frozen=True)
    try:
        assert report.guard is session.guard
        assert report.level == "INFO"
    finally:
        report.close()


def test_default_log_dir_points_to_logs() -> None:
    assert default_log_dir().name == "logs"
