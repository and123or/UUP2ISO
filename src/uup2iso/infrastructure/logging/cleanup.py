"""日志清理与审计只读（日志 §4、§5.4）。

三条规则必须同时成立才算达标：**目录总大小、保留天数、文件数量**（都是硬上限）。
删除按修改时间从旧到新，**当前正在写的文件永不删**；审计删除前解除只读，并且必须留痕。

两个池子分开统计、分开清理（日志 §4.1）：

* 运行池 = ``run_*.log`` + ``diag_*.zip``（诊断包与日志共池，用户只调一组数字）；
* 审计池 = ``audit_*.log``。
"""

from __future__ import annotations

import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "AUDIT_PATTERNS",
    "RUN_PATTERNS",
    "LogThresholds",
    "PruneReport",
    "ensure_audit_readonly",
    "is_readonly",
    "prune",
    "set_readonly",
]

RUN_PATTERNS: tuple[str, ...] = ("run_*.log", "diag_*.zip")
AUDIT_PATTERNS: tuple[str, ...] = ("audit_*.log",)

_MB = 1024 * 1024
_MIN_SIZE_MB = 10
_MIN_DAYS = 1
_MIN_FILES = 5

_FILE_ATTRIBUTE_READONLY = 0x00000001


@dataclass(frozen=True, slots=True)
class LogThresholds:
    """三项阈值（日志 §4.2 的默认值，GUI 可改但不得低于下限）。"""

    max_total_mb: int = 200
    max_days: int = 30
    max_files: int = 100

    def validate(self) -> None:
        """日志 §4.3 的输入下限保护：不达标不允许保存。"""
        if self.max_total_mb < _MIN_SIZE_MB:
            raise ValueError(f"目录总大小不得低于 {_MIN_SIZE_MB} MB")
        if self.max_days < _MIN_DAYS:
            raise ValueError(f"保留天数不得低于 {_MIN_DAYS} 天")
        if self.max_files < _MIN_FILES:
            raise ValueError(f"文件数量不得低于 {_MIN_FILES} 个")


@dataclass(frozen=True, slots=True)
class PruneReport:
    """一次清理的结果（供调用方把 ``audit_pruned`` 写进运行日志）。"""

    deleted: tuple[Path, ...] = ()
    audit_deleted: tuple[Path, ...] = ()
    reasons: dict[str, str] = field(default_factory=dict)
    skipped: tuple[Path, ...] = ()
    remaining_files: int = 0
    remaining_mb: float = 0.0

    @property
    def changed(self) -> bool:
        return bool(self.deleted)


def set_readonly(path: Path, readonly: bool = True) -> None:
    """设置/清除只读属性（Windows 上就是 ``FILE_ATTRIBUTE_READONLY``）。"""
    try:
        current = path.stat().st_mode
        if readonly:
            path.chmod(current & ~stat.S_IWRITE)
        else:
            path.chmod(current | stat.S_IWRITE)
    except OSError:
        return


def is_readonly(path: Path) -> bool:
    """判断只读属性（用 ``st_file_attributes``，比 ``os.access`` 可靠）。"""
    try:
        attributes = getattr(path.stat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _FILE_ATTRIBUTE_READONLY)


def ensure_audit_readonly(log_dir: Path, *, limit: int = 3) -> tuple[Path, ...]:
    """启动时只补最近 ``limit`` 个审计文件的只读属性（日志 §5.4）。

    为什么只补 3 个：异常退出最多漏设上一次运行的文件。全量扫描会让启动耗时随文件数线性增长，
    验收要求「500 个历史文件时与 3 个同级」。
    """
    if not log_dir.is_dir():
        return ()

    candidates = sorted(
        (path for pattern in AUDIT_PATTERNS for path in log_dir.glob(pattern)),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )[:limit]

    fixed: list[Path] = []
    for path in candidates:
        if not is_readonly(path):
            set_readonly(path, True)
            fixed.append(path)
    return tuple(fixed)


def prune(
    log_dir: Path,
    *,
    run: LogThresholds,
    audit: LogThresholds,
    current: tuple[str, ...] | list[str] = (),
    now: float | None = None,
) -> PruneReport:
    """按三项阈值清理两个池子（日志 §4.1）。

    Args:
        log_dir: ``logs`` 目录。
        run: 运行池阈值（含 ``diag_*.zip``）。
        audit: 审计池阈值。
        current: 当前进程正在写的文件名（永不删除）。
        now: 注入时间戳（测试用）。

    Returns:
        :class:`PruneReport`；审计删除项要由调用方记 ``event=audit_pruned``。
    """
    if not log_dir.is_dir():
        return PruneReport()

    clock = time.time() if now is None else now
    protected = set(current)

    run_files = _collect(log_dir, RUN_PATTERNS)
    audit_files = _collect(log_dir, AUDIT_PATTERNS)

    deleted, reasons, skipped = _prune_pool(run_files, run, protected, clock, audit_pool=False)
    audit_deleted, audit_reasons, audit_skipped = _prune_pool(
        audit_files, audit, protected, clock, audit_pool=True
    )

    reasons.update(audit_reasons)
    remaining = _collect(log_dir, RUN_PATTERNS)
    return PruneReport(
        deleted=tuple(deleted),
        audit_deleted=tuple(audit_deleted),
        reasons=reasons,
        skipped=tuple(skipped + audit_skipped),
        remaining_files=len(remaining),
        remaining_mb=sum(path.stat().st_size for path in remaining) / _MB,
    )


def _collect(directory: Path, patterns: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        files.extend(path for path in directory.glob(pattern) if path.is_file())
    return sorted(files, key=lambda item: item.stat().st_mtime)


def _violations(files: list[Path], thresholds: LogThresholds, now: float) -> set[str]:
    if not files:
        return set()
    total_mb = sum(path.stat().st_size for path in files) / _MB
    oldest_days = (now - min(path.stat().st_mtime for path in files)) / 86400
    breached: set[str] = set()
    if total_mb > thresholds.max_total_mb:
        breached.add("size")
    if oldest_days > thresholds.max_days:
        breached.add("age")
    if len(files) > thresholds.max_files:
        breached.add("count")
    return breached


def _prune_pool(
    files: list[Path],
    thresholds: LogThresholds,
    protected: set[str],
    now: float,
    *,
    audit_pool: bool,
) -> tuple[list[Path], dict[str, str], list[Path]]:
    deleted: list[Path] = []
    reasons: dict[str, str] = {}
    skipped: list[Path] = []

    remaining = list(files)
    while True:
        breached = _violations(remaining, thresholds, now)
        if not breached:
            break
        if not remaining:
            break
        victim = remaining.pop(0)
        if victim.name in protected:
            skipped.append(victim)
            continue
        reason = "+".join(sorted(breached))
        if audit_pool:
            set_readonly(victim, False)  # 清理被授权解除只读（日志 §5.4）
        try:
            victim.unlink()
        except OSError:
            skipped.append(victim)
            continue
        deleted.append(victim)
        reasons[victim.name] = reason

    return deleted, reasons, skipped
