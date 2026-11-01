"""启动序列（日志 §2.2、§4.3、§6.1）：单实例 → 建日志 → 补只读 → 清理。

顺序是硬契约，不能调换：

1. **先抢单实例**——冲突实例绝不建日志、不清理、不写审计；
2. 再建运行日志与审计日志（同时间戳）；
3. 启动补设最近 3 个审计文件的只读属性；
4. 按三项阈值清理一次（运行期不再扫描）；
5. 把级别来源、清理结果写进日志（含 ``event=audit_pruned`` 留痕）。

GUI 与 CLI 共用这一条序列，区别只在「冲突时做什么」：GUI 激活已有窗口退 0，CLI 输出
``already_running`` 退 5。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from uup2iso.infrastructure.logging import (
    AuditWriteError,
    LogSink,
    LogThresholds,
    PruneReport,
    ensure_audit_readonly,
    prune,
)
from uup2iso.infrastructure.single_instance import DEFAULT_MUTEX_NAME, SingleInstanceGuard

__all__ = [
    "ALREADY_RUNNING_PAYLOAD",
    "StartupConflictError",
    "StartupReport",
    "StartupSession",
    "acquire_instance",
    "bootstrap",
    "default_log_dir",
    "release_instance",
]

ALREADY_RUNNING_PAYLOAD: dict[str, object] = {"error": "already_running", "code": 5}
"""CLI 冲突时输出的 JSON（日志 §6.1）。"""


class StartupConflictError(RuntimeError):
    """已有实例在运行（退出码 5）。"""

    exit_code = 5

    def __init__(self, message: str = "程序已在运行") -> None:
        super().__init__(message)
        self.payload = dict(ALREADY_RUNNING_PAYLOAD)


@dataclass(slots=True)
class StartupSession:
    """已抢到的单实例会话（对上层隐藏互斥量细节）。"""

    guard: SingleInstanceGuard

    @property
    def held(self) -> bool:
        return self.guard.held


def acquire_instance(name: str = DEFAULT_MUTEX_NAME) -> StartupSession | None:
    """尝试成为唯一实例。

    Returns:
        抢到返回会话；已有实例返回 ``None``（**不建日志、不清理**，由调用方决定退 0 还是退 5）。
    """
    guard = SingleInstanceGuard(name)
    return StartupSession(guard=guard) if guard.acquire() else None


def release_instance(session: StartupSession) -> None:
    """释放会话（异常路径也要调，否则会把自己锁在门外）。"""
    session.guard.release()


@dataclass(slots=True)
class StartupReport:
    """一次成功启动的产物。"""

    sink: LogSink
    guard: SingleInstanceGuard
    pruned: PruneReport = field(default_factory=PruneReport)
    readonly_fixed: tuple[Path, ...] = ()

    @property
    def level(self) -> str:
        return self.sink.level

    @property
    def level_source(self) -> str:
        return self.sink.level_source

    def close(self) -> None:
        """收尾：关日志（审计置只读）后释放单实例。"""
        self.sink.close()
        self.guard.release()


def default_log_dir() -> Path:
    """日志目录：便携分发，永远在程序目录下的 ``logs``（日志 §2.1）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "logs"
    # 开发期用仓库根（便于直接看日志）；与打包后的语义一致
    return Path(__file__).resolve().parents[3] / "logs"


def bootstrap(
    *,
    entry: str,
    log_dir: Path | None = None,
    mutex_name: str = DEFAULT_MUTEX_NAME,
    debug: bool = False,
    frozen: bool | None = None,
    run_thresholds: LogThresholds | None = None,
    audit_thresholds: LogThresholds | None = None,
    session: StartupSession | None = None,
) -> StartupReport:
    """执行启动序列。

    Args:
        entry: ``"gui"`` 或 ``"cli"``，决定默认日志级别（日志 §3）。
        log_dir: 覆盖日志目录（测试用）。
        mutex_name: 覆盖互斥量名（测试用唯一名）。
        debug: 是否带 ``--debug``。
        frozen: 覆盖 ``sys.frozen`` 判定（测试用）。
        run_thresholds / audit_thresholds: 覆盖清理阈值。
        session: 已经抢到的会话（GUI 需要先抢再决定「激活还是继续」）。

    Returns:
        :class:`StartupReport`；调用方负责最终 :meth:`StartupReport.close`。

    Raises:
        StartupConflictError: 已有实例在运行（**此时没有创建任何日志文件**）。
        AuditWriteError: 日志目录不可写（日志 §2.1 要求启动即报错，不静默回退）。
    """
    active = session or acquire_instance(mutex_name)
    if active is None:
        raise StartupConflictError()

    target_dir = log_dir or default_log_dir()
    try:
        sink = LogSink.open(target_dir, entry=entry, debug=debug, frozen=frozen)
    except AuditWriteError:
        release_instance(active)
        raise

    readonly_fixed = ensure_audit_readonly(target_dir)
    pruned = prune(
        target_dir,
        run=run_thresholds or LogThresholds(),
        audit=audit_thresholds or LogThresholds(max_total_mb=20, max_days=365, max_files=200),
        current=sink.paths.names,
    )

    sink.info(
        "app.startup",
        "程序启动",
        event="startup",
        entry=entry,
        log_level=sink.level,
        log_level_source=sink.level_source,
        log=sink.paths.run.name,
    )
    if readonly_fixed:
        sink.info(
            "app.startup",
            "补设审计只读",
            event="audit_readonly_fixed",
            count=len(readonly_fixed),
        )
    if pruned.changed or pruned.skipped:
        sink.info(
            "app.startup",
            "按阈值清理日志",
            event="prune",
            deleted=len(pruned.deleted),
            skipped=len(pruned.skipped),
            remaining_files=pruned.remaining_files,
            remaining_mb=f"{pruned.remaining_mb:.1f}",
        )
    for path in pruned.audit_deleted:
        sink.info(
            "app.startup",
            "清理审计日志",
            event="audit_pruned",
            file=path.name,
            reason=pruned.reasons.get(path.name, "unknown"),
        )

    return StartupReport(
        sink=sink, guard=active.guard, pruned=pruned, readonly_fixed=readonly_fixed
    )
