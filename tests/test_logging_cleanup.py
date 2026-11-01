"""清理与审计只读门禁（日志 §4、§5.4）。

三个维度都是硬上限，任一超限即清理；当前文件永不删；审计删除前先解除只读并留痕。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from uup2iso.infrastructure.logging import (
    LogThresholds,
    ensure_audit_readonly,
    is_readonly,
    prune,
    set_readonly,
)

DAY = 86400


def touch(path: Path, *, size: int = 10, age_days: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def thresholds(**overrides: object) -> LogThresholds:
    base = {"max_total_mb": 200, "max_days": 30, "max_files": 100}
    base.update(overrides)
    return LogThresholds(**base)  # type: ignore[arg-type]


# --- 只读属性 ---------------------------------------------------------------


def test_readonly_roundtrip(tmp_path: Path) -> None:
    path = touch(tmp_path / "a.log")
    set_readonly(path, True)
    assert is_readonly(path)
    set_readonly(path, False)
    assert not is_readonly(path)


def test_ensure_audit_readonly_only_touches_latest_three(tmp_path: Path) -> None:
    """日志 §5.4：启动只补最近 3 个，避免上万文件时启动线性变慢。"""
    paths = [touch(tmp_path / f"audit_2026110{i}-100000.log", age_days=i) for i in range(5)]

    fixed = ensure_audit_readonly(tmp_path, limit=3)

    assert len(fixed) == 3
    # 最新的 3 个（age 0/1/2）被置只读，最早的两个不动
    assert is_readonly(paths[0]) and is_readonly(paths[1]) and is_readonly(paths[2])
    assert not is_readonly(paths[3]) and not is_readonly(paths[4])


def test_ensure_audit_readonly_is_idempotent(tmp_path: Path) -> None:
    touch(tmp_path / "audit_20261101-100000.log")
    first = ensure_audit_readonly(tmp_path)
    second = ensure_audit_readonly(tmp_path)
    assert len(first) == 1
    assert second == (), "已只读的文件不应重复处理"


def test_ensure_audit_readonly_handles_missing_dir(tmp_path: Path) -> None:
    assert ensure_audit_readonly(tmp_path / "nope") == ()


# --- 阈值下限保护 -----------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_total_mb": 9}, "MB"),
        ({"max_days": 0}, "天"),
        ({"max_files": 4}, "文件"),
    ],
)
def test_threshold_lower_bounds(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError) as caught:
        thresholds(**overrides).validate()
    assert message in str(caught.value)


def test_default_thresholds_meet_spec() -> None:
    """日志 §4.2 的默认值：运行 200MB/30天/100，审计 20MB/365天/200。"""
    run = LogThresholds()
    audit = LogThresholds(max_total_mb=20, max_days=365, max_files=200)
    run.validate()
    audit.validate()
    assert (run.max_total_mb, run.max_days, run.max_files) == (200, 30, 100)


# --- 按数量清理 -------------------------------------------------------------


def test_prune_by_count_removes_oldest(tmp_path: Path) -> None:
    paths = [touch(tmp_path / f"run_2026100{i}-100000.log", age_days=10 - i) for i in range(1, 9)]

    report = prune(tmp_path, run=thresholds(max_files=5), audit=thresholds())

    # 8 个文件、上限 5 → 删最旧的 3 个
    assert len(report.deleted) == 3
    assert set(report.deleted) == set(paths[:3])
    assert report.reasons[paths[0].name] == "count"
    assert len(list(tmp_path.glob("run_*.log"))) == 5


def test_current_file_is_never_deleted(tmp_path: Path) -> None:
    paths = [touch(tmp_path / f"run_2026100{i}-100000.log", age_days=10 - i) for i in range(1, 7)]

    report = prune(
        tmp_path,
        run=thresholds(max_files=2),
        audit=thresholds(),
        current=(paths[0].name,),
    )

    assert paths[0].exists(), "当前正在写的文件必须保留"
    assert paths[0] in report.skipped
    assert all(path.exists() for path in paths[-2:])


def test_prune_by_age(tmp_path: Path) -> None:
    old = touch(tmp_path / "run_20250101-100000.log", age_days=400)
    fresh = touch(tmp_path / "run_20261101-100000.log", age_days=1)

    report = prune(tmp_path, run=thresholds(), audit=thresholds())

    assert old in report.deleted
    assert report.reasons[old.name] == "age"
    assert fresh.exists()


def test_prune_by_size_includes_diag_packages(tmp_path: Path) -> None:
    """诊断包与运行日志共用同一个池（日志 §4.1）。"""
    mb = 1024 * 1024
    for index in range(4):
        touch(tmp_path / f"run_2026110{index + 1}-100000.log", size=4 * mb, age_days=10 - index)
    diag = touch(tmp_path / "diag_20261001-100000.zip", size=4 * mb, age_days=20)

    report = prune(tmp_path, run=thresholds(max_total_mb=10), audit=thresholds())

    assert diag in report.deleted, "诊断包必须参与运行池清理"
    total_mb = sum(path.stat().st_size for path in tmp_path.glob("run_*.log")) / mb
    assert total_mb <= 10


# --- 审计池独立 -------------------------------------------------------------


def test_audit_pool_is_cleaned_separately(tmp_path: Path) -> None:
    run_file = touch(tmp_path / "run_20261101-100000.log", size=1024, age_days=100)
    audits = [
        touch(tmp_path / f"audit_2026100{i}-100000.log", age_days=10 - i) for i in range(1, 7)
    ]

    report = prune(
        tmp_path,
        run=thresholds(max_files=100, max_days=365),
        audit=thresholds(max_files=3),
        now=time.time(),
    )

    assert run_file.exists(), "运行池上限宽松，不该删运行日志"
    assert len(report.audit_deleted) == 3
    assert all(not path.exists() for path in audits[:3])


def test_audit_deletion_clears_readonly(tmp_path: Path) -> None:
    """审计只读必须在清理时被解除，否则删不掉（日志 §5.4 的清理例外）。"""
    paths = [touch(tmp_path / f"audit_2026100{i}-100000.log", age_days=10 - i) for i in range(1, 5)]
    for path in paths:
        set_readonly(path, True)

    report = prune(tmp_path, run=thresholds(), audit=thresholds(max_files=2))

    assert len(report.audit_deleted) == 2
    assert report.reasons[paths[0].name] == "count"
    assert paths[-1].exists() and is_readonly(paths[-1])


def test_unrelated_files_are_ignored(tmp_path: Path) -> None:
    keep = touch(tmp_path / "notes.txt")
    keep2 = touch(tmp_path / "settings.json")
    for index in range(6):
        touch(tmp_path / f"run_2026100{index}-100000.log", age_days=10 - index)

    prune(tmp_path, run=thresholds(max_files=2), audit=thresholds())

    assert keep.exists() and keep2.exists(), "只处理本工具生成的日志文件"


def test_empty_dir_is_harmless(tmp_path: Path) -> None:
    report = prune(tmp_path / "nope", run=thresholds(), audit=thresholds())
    assert report.deleted == ()
    assert report.remaining_files == 0
