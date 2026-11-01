"""LogSink 门禁（日志 §2、§3、§5）。

验收对应的硬点：每进程一文件、级别过滤、**进度不落盘**、审计分流、退出只读、采样兜底、脱敏。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from uup2iso.infrastructure.logging import (
    DEBUG,
    INFO,
    AuditWriteError,
    LogSink,
    is_readonly,
)

TS = datetime(2026, 11, 1, 10, 0, 0)


def make_sink(tmp_path: Path, *, level: str = INFO, started_at: datetime = TS) -> LogSink:
    return LogSink.open(
        tmp_path / "logs",
        level=level,
        started_at=started_at,
        clock=lambda: started_at,
    )


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- 文件模型 ---------------------------------------------------------------


def test_open_creates_pair_with_same_timestamp(tmp_path: Path) -> None:
    sink = make_sink(tmp_path)
    try:
        assert sink.paths.run.name == "run_20261101-100000.log"
        assert sink.paths.audit.name == "audit_20261101-100000.log"
        assert sink.paths.run.is_file()
        assert sink.paths.audit.is_file()
    finally:
        sink.close()


def test_same_second_collision_keeps_pair_in_sync(tmp_path: Path) -> None:
    first = make_sink(tmp_path)
    second = make_sink(tmp_path)
    try:
        assert first.paths.run.name == "run_20261101-100000.log"
        assert second.paths.run.name == "run_20261101-100000_1.log"
        assert second.paths.audit.name == "audit_20261101-100000_1.log"
    finally:
        first.close()
        second.close()


def test_one_file_per_process_not_per_module(tmp_path: Path) -> None:
    """模块/阶段再多也只有一个文件（日志 §2.2）。"""
    sink = make_sink(tmp_path)
    try:
        sink.info("infra.process", "a", stage="Convert")
        sink.info("app.pipeline", "b", stage="Slim")
        sink.warn("app.rules", "c", stage="Slim")
    finally:
        sink.close()

    logs = sorted((tmp_path / "logs").glob("*.log"))
    assert len(logs) == 2, "只有 run + audit 两个文件"


# --- 级别 -------------------------------------------------------------------


def test_level_filters_debug_but_keeps_warn_and_error(tmp_path: Path) -> None:
    sink = make_sink(tmp_path, level=INFO)
    try:
        sink.debug("m", "调试细节")
        sink.info("m", "普通信息")
        sink.warn("m", "警告")
        sink.error("m", "错误")
    finally:
        sink.close()

    text = read(sink.paths.run)
    assert "调试细节" not in text
    assert "普通信息" in text
    assert "[WARN ]" in text
    assert "[ERROR]" in text


def test_debug_level_writes_debug(tmp_path: Path) -> None:
    sink = make_sink(tmp_path, level=DEBUG)
    try:
        sink.debug("m", "调试可见")
    finally:
        sink.close()
    assert "调试可见" in read(sink.paths.run)


# --- 进度绝不落盘 -----------------------------------------------------------


def test_progress_never_reaches_file(tmp_path: Path) -> None:
    sink = make_sink(tmp_path)
    try:
        sink.info("m", "起点")
        before = sink.paths.run.stat().st_size
        for percent in (10, 20, 30):
            sink.progress(stage="Convert", percent=percent)
        sink.info("m", "终点")
    finally:
        sink.close()

    text = read(sink.paths.run)
    assert "%" not in text, "进度百分比绝不能落盘"
    assert len(text.splitlines()) == 2, "只应有两行（起点/终点）"
    assert "起点" in text and "终点" in text
    assert sink.paths.run.stat().st_size > before


# --- 审计 -------------------------------------------------------------------


def test_audit_goes_to_separate_file_with_task_and_event(tmp_path: Path) -> None:
    sink = make_sink(tmp_path)
    try:
        sink.audit(
            "app.audit",
            "启用高风险优化项",
            event="high_risk_enabled",
            task="7f3a",
            stage="Slim",
            rule="FR5-OP-T60",
            risk="high",
            confirmed=True,
        )
    finally:
        sink.close()

    audit = read(sink.paths.audit)
    assert audit.startswith("2026-11-01 10:00:00.000 [AUDIT] [Slim] app.audit:")
    assert "task=7f3a" in audit
    assert "event=high_risk_enabled" in audit
    assert "confirmed=true" in audit
    assert "启用高风险优化项" not in read(sink.paths.run)


def test_audit_write_after_close_raises(tmp_path: Path) -> None:
    sink = make_sink(tmp_path)
    sink.close()
    with pytest.raises(AuditWriteError):
        sink.audit("app.audit", "x", event="settings_changed")


def test_audit_file_becomes_readonly_on_close(tmp_path: Path) -> None:
    sink = make_sink(tmp_path)
    sink.audit("app.audit", "x", event="settings_changed", task="none")
    sink.close()

    assert is_readonly(sink.paths.audit), "审计退出后必须只读（日志 §5.4）"
    assert not is_readonly(sink.paths.run)


def test_readonly_can_be_disabled_for_tests(tmp_path: Path) -> None:
    sink = make_sink(tmp_path)
    sink.close(read_only_audit=False)
    assert not is_readonly(sink.paths.audit)


# --- 采样兜底 ---------------------------------------------------------------


def test_repetition_sampling_caps_noise(tmp_path: Path) -> None:
    """150 行同前缀 → 远少于 150 行落盘，末行带 sampled 标记（日志 §5.2）。"""
    sink = make_sink(tmp_path)
    try:
        for index in range(150):
            sink.info("infra.process", f"重复输出 第{index}条")
    finally:
        sink.close()

    lines = read(sink.paths.run).splitlines()
    assert len(lines) < 60, f"采样未生效，写了 {len(lines)} 行"
    assert any("sampled=true" in line for line in lines)
    assert any("total=150" in line for line in lines)


def test_warn_lines_are_never_sampled(tmp_path: Path) -> None:
    sink = make_sink(tmp_path)
    try:
        for index in range(120):
            sink.warn("m", f"同一警告 第{index}条")
    finally:
        sink.close()

    text = read(sink.paths.run)
    assert text.count("[WARN ]") == 120, "WARN 一条都不能丢"


# --- 工具调用头尾 -----------------------------------------------------------


def test_tool_start_and_finish_events(tmp_path: Path) -> None:
    sink = make_sink(tmp_path)
    try:
        sink.tool_started("wimlib-imagex", "wimlib-imagex export a b", cwd="D:/work", task="7f3a")
        sink.tool_finished(
            "wimlib-imagex", 0, 19.4, out="D:/ISO/boot.wim", size_bytes=458 * 1024 * 1024
        )
    finally:
        sink.close()

    text = read(sink.paths.run)
    assert "event=start" in text and "tool=wimlib-imagex" in text
    assert "event=finish" in text and "exit=0" in text and "duration=19.4s" in text
    assert "conclusion=success" in text
    assert "458.0MB" in text


# --- 异常与脱敏 -------------------------------------------------------------


def test_exception_writes_prefixed_lines(tmp_path: Path) -> None:
    sink = make_sink(tmp_path)
    try:
        try:
            raise ValueError("坏掉了")
        except ValueError as exc:
            sink.exception("infra.x", "执行失败", exc, stage="Convert")
    finally:
        sink.close()

    text = read(sink.paths.run)
    assert "EXC ValueError: 坏掉了" in text
    assert "  | " in text, "续行要带缩进前缀（日志 §2.3）"


def test_sensitive_values_are_redacted_before_write(tmp_path: Path) -> None:
    sink = make_sink(tmp_path)
    try:
        sink.info(
            "infra.download",
            "proxy http://bob:hunter2@proxy.local:8080 密码 password=hunter2 密钥 ABCDE-12345-FGHIJ-67890-KLMNO",
            task="7f3a",
        )
    finally:
        sink.close()

    text = read(sink.paths.run)
    assert "hunter2" not in text
    assert "ABCDE-12345" not in text
    assert "XXXXX-XXXXX-XXXXX-XXXXX-KLMNO" in text
