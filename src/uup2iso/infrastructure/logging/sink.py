"""`LogSink`：每进程一个运行日志 + 一个审计日志（日志 §2、§3、§5）。

四条硬约束直接写进实现里：

1. **一个进程一个文件**：`run_<启动时间戳>.log` / `audit_<启动时间戳>.log`，两者时间戳一致，
   同秒冲突追加 `_1`、`_2`（两边序号同步）；
2. **进度绝不落盘**：:meth:`LogSink.progress` 是显式空操作，只把值转发给进度通道；
3. **审计独立且退出只读**：:meth:`audit` 写独立文件，关闭时置只读；写不进去就抛错阻断高风险操作；
4. **脱敏在写入前**：消息与字段值统一过 :func:`~.redact.redact_text`。

另外实现日志 §5.2 的**采样兜底**：同前缀连续行超过 50 条时只留「首 1 + 每 100 条 1 条 + 末 1」，
并在末行追加 ``sampled=true total=<n>``。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from uup2iso.core.errors import DependencyError, ErrorCode
from uup2iso.infrastructure.logging.format import (
    AUDIT,
    DEBUG,
    ERROR,
    INFO,
    WARN,
    format_line,
    level_rank,
)
from uup2iso.infrastructure.logging.levels import resolve_level
from uup2iso.infrastructure.logging.redact import redact_text

__all__ = [
    "AuditWriteError",
    "LogPaths",
    "LogSink",
    "RepetitionSampler",
    "RUN_PREFIX",
    "AUDIT_PREFIX",
]

RUN_PREFIX = "run_"
AUDIT_PREFIX = "audit_"
_TS_FORMAT = "%Y%m%d-%H%M%S"
_BURST_LIMIT = 50
_KEEP_EVERY = 100


class AuditWriteError(DependencyError):
    """审计写入失败（日志 §5.3：写不进审计就不许做高风险操作）。"""

    default_code = ErrorCode.PERMISSION_DENIED


@dataclass(frozen=True, slots=True)
class LogPaths:
    """一个进程的两个日志文件。"""

    run: Path
    audit: Path

    @property
    def names(self) -> tuple[str, str]:
        return (self.run.name, self.audit.name)


class RepetitionSampler:
    """同前缀连续行采样（日志 §5.2）。

    实测动机：一次转换里工具可能刷出几十万行同构输出，全落盘会写出几十 MB。
    """

    def __init__(self, *, burst: int = _BURST_LIMIT, keep_every: int = _KEEP_EVERY) -> None:
        self._burst = burst
        self._keep_every = keep_every
        self._key: str | None = None
        self._count = 0
        self._pending: Any = None
        self._pending_total = 0

    def feed(self, key: str, item: Any) -> list[tuple[Any, int | None]]:
        """喂入一行；返回 ``[(要写出的项, sampled_total 或 None)]``。"""
        if key != self._key:
            flushed = self.flush()
            self._key = key
            self._count = 1
            self._pending = None
            return [*flushed, (item, None)]

        self._count += 1
        if self._count <= self._burst:
            return [(item, None)]
        if self._count % self._keep_every == 0:
            self._pending = None
            return [(item, None)]

        self._pending = item
        self._pending_total = self._count
        return []

    def flush(self) -> list[tuple[Any, int | None]]:
        """换前缀或收尾时把「末 1 条」补写出来（带 sampled 标记）。"""
        if self._pending is None:
            self._key = None
            self._count = 0
            return []
        item, total = self._pending, self._pending_total
        self._pending = None
        self._pending_total = 0
        self._key = None
        self._count = 0
        return [(item, total)]


@dataclass
class _Line:
    level: str
    logger_name: str
    message: str
    stage: str | None
    fields: dict[str, object] = field(default_factory=dict)


class LogSink:
    """运行日志 + 审计日志的写入端。

    Args:
        paths: 两个文件路径（由 :meth:`open` 生成）。
        level: 生效级别（DEBUG/INFO/WARN/ERROR）。
        level_source: 级别来源说明，仅用于展示（如「发布版」「--debug」）。
        clock: 可注入时钟（测试用）。
    """

    def __init__(
        self,
        *,
        paths: LogPaths,
        level: str = INFO,
        level_source: str = "",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._paths = paths
        self._level = level
        self._level_source = level_source
        self._clock = clock or datetime.now
        self._sampler = RepetitionSampler()
        self._run_handle = paths.run.open("a", encoding="utf-8", newline="\n")
        self._audit_handle = paths.audit.open("a", encoding="utf-8", newline="\n")
        self._closed = False

    # -- 构造 -------------------------------------------------------------

    @classmethod
    def open(
        cls,
        log_dir: Path,
        *,
        level: str | None = None,
        entry: str = "gui",
        debug: bool = False,
        started_at: datetime | None = None,
        frozen: bool | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> LogSink:
        """按日志 §2.1 建两个文件；目录不可写时立即报错（不静默回退）。"""
        import sys

        is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        if level is None:
            level, source = resolve_level(frozen=is_frozen, entry=entry, debug=debug)
        else:
            source = "配置指定"

        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AuditWriteError(
                f"日志目录不可写：{log_dir}。请将程序解压到可写目录（如 D:\\Tools\\UUP2ISO）",
            ) from exc

        paths = _allocate_paths(log_dir, started_at or datetime.now())
        return cls(paths=paths, level=level, level_source=source, clock=clock)

    # -- 属性 -------------------------------------------------------------

    @property
    def paths(self) -> LogPaths:
        return self._paths

    @property
    def level(self) -> str:
        return self._level

    @property
    def level_source(self) -> str:
        return self._level_source

    # -- 写日志 -----------------------------------------------------------

    def debug(
        self, logger_name: str, message: str, *, stage: str | None = None, **fields: object
    ) -> None:
        self.log(DEBUG, logger_name, message, stage=stage, **fields)

    def info(
        self, logger_name: str, message: str, *, stage: str | None = None, **fields: object
    ) -> None:
        self.log(INFO, logger_name, message, stage=stage, **fields)

    def warn(
        self, logger_name: str, message: str, *, stage: str | None = None, **fields: object
    ) -> None:
        self.log(WARN, logger_name, message, stage=stage, **fields)

    def error(
        self, logger_name: str, message: str, *, stage: str | None = None, **fields: object
    ) -> None:
        self.log(ERROR, logger_name, message, stage=stage, **fields)

    def log(
        self,
        level: str,
        logger_name: str,
        message: str,
        *,
        stage: str | None = None,
        **fields: object,
    ) -> None:
        """写一行运行日志。

        级别过滤只作用于 DEBUG/INFO；**WARN/ERROR 在任何级别下都写**（日志 §3）。
        """
        if self._closed or level == AUDIT:
            return
        if level not in (WARN, ERROR) and level_rank(level) < level_rank(self._level):
            return

        line = _Line(
            level=level, logger_name=logger_name, message=message, stage=stage, fields=dict(fields)
        )

        # 采样只针对低级别刷屏；WARN/ERROR 一条都不能丢
        if level in (WARN, ERROR):
            self._write_run(line, sampled_total=None)
            return

        key = _sample_key(logger_name, message)
        for item, total in self._sampler.feed(key, line):
            self._write_run(item, sampled_total=total)

    def exception(
        self,
        logger_name: str,
        message: str,
        exc: BaseException,
        *,
        stage: str | None = None,
        **fields: object,
    ) -> None:
        """记录异常：首行 ``EXC`` 前缀，后续行 ``  | `` 缩进（日志 §2.3）。"""
        first = f"EXC {type(exc).__name__}: {exc}"
        self.error(logger_name, f"{message} | {first}" if message else first, stage=stage, **fields)
        for frame_line in _traceback_lines(exc):
            self.error(logger_name, f"  | {frame_line}", stage=stage)

    # -- 审计 -------------------------------------------------------------

    def audit(
        self,
        logger_name: str,
        message: str,
        *,
        event: str,
        task: str = "none",
        stage: str | None = None,
        **fields: object,
    ) -> None:
        """写审计（日志 §5.3）。

        审计行**必须**带 ``event`` 与 ``task``；写失败抛 :class:`AuditWriteError`，
        调用方必须据此阻断高风险操作（不做静默放行）。
        """
        if self._closed:
            raise AuditWriteError("日志已关闭，无法写审计")

        line = format_line(
            timestamp=self._clock(),
            level=AUDIT,
            stage=stage,
            logger_name=logger_name,
            message=redact_text(message),
            fields={
                "task": task,
                "event": event,
                **_redact_fields(fields),
            },
        )
        try:
            self._audit_handle.write(line + "\n")
            self._audit_handle.flush()
        except OSError as exc:
            raise AuditWriteError(f"审计写入失败：{exc}") from exc

    # -- 进度（绝不落盘） -------------------------------------------------

    def progress(self, *_args: object, **_kwargs: object) -> None:
        """进度**不写日志**（日志 §5.2）。保留此方法是让调用方语义明确。"""
        return

    # -- 工具调用头尾（进程 §10） -----------------------------------------

    def tool_started(
        self,
        tool: str,
        cmd: str,
        *,
        cwd: str | Path | None = None,
        file: str | Path | None = None,
        task: str | None = None,
        stage: str | None = None,
        source: str | None = None,
    ) -> None:
        self.info(
            "infra.process",
            "启动外部工具",
            stage=stage,
            event="start",
            tool=tool,
            cmd=cmd,
            cwd=str(cwd) if cwd else None,
            file=str(file) if file else None,
            source=source,
            task=task,
        )

    def tool_finished(
        self,
        tool: str,
        exit_code: int,
        duration_seconds: float,
        *,
        out: str | Path | None = None,
        size_bytes: int | None = None,
        task: str | None = None,
        stage: str | None = None,
    ) -> None:
        self.info(
            "infra.process",
            "外部工具结束",
            stage=stage,
            event="finish",
            tool=tool,
            exit=exit_code,
            duration=f"{duration_seconds:.1f}s",
            out=str(out) if out else None,
            size=_human_bytes(size_bytes),
            conclusion="success" if exit_code == 0 else "failed",
            task=task,
        )

    # -- 收尾 -------------------------------------------------------------

    def close(self, *, read_only_audit: bool = True) -> None:
        """冲刷并关闭；审计文件置只读（日志 §5.4）。"""
        if self._closed:
            return
        for item, total in self._sampler.flush():
            self._write_run(item, sampled_total=total)
        self._closed = True
        for handle in (self._run_handle, self._audit_handle):
            try:
                handle.flush()
            finally:
                handle.close()
        if read_only_audit:
            from uup2iso.infrastructure.logging.cleanup import set_readonly

            set_readonly(self._paths.audit, True)

    def __enter__(self) -> LogSink:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- 内部 -------------------------------------------------------------

    def _write_run(self, line: _Line, *, sampled_total: int | None) -> None:
        fields: Mapping[str, object] = dict(line.fields)
        if sampled_total is not None:
            fields = {**fields, "sampled": True, "total": sampled_total}
        text = format_line(
            timestamp=self._clock(),
            level=line.level,
            stage=line.stage,
            logger_name=line.logger_name,
            message=redact_text(line.message),
            fields=_redact_fields(fields),
        )
        try:
            self._run_handle.write(text + "\n")
            self._run_handle.flush()
        except OSError:
            # 运行日志写失败不阻断主流程，但不能把异常吞成静默
            raise


def _allocate_paths(log_dir: Path, started_at: datetime) -> LogPaths:
    """按启动时间戳分配文件名；同秒冲突时两侧序号保持一致（日志 §2.1）。"""
    stamp = started_at.strftime(_TS_FORMAT)
    for suffix in ("", *(f"_{index}" for index in range(1, 100))):
        run_path = log_dir / f"{RUN_PREFIX}{stamp}{suffix}.log"
        audit_path = log_dir / f"{AUDIT_PREFIX}{stamp}{suffix}.log"
        if not run_path.exists() and not audit_path.exists():
            return LogPaths(run=run_path, audit=audit_path)
    raise AuditWriteError(f"同一时间戳的日志文件过多：{stamp}")


def _redact_fields(fields: Mapping[str, object]) -> dict[str, object]:
    return {
        key: redact_text(str(value)) if isinstance(value, str) else value
        for key, value in fields.items()
    }


_DIGITS = re.compile(r"\d+")


def _sample_key(logger_name: str, message: str) -> str:
    """采样分组键：模块 + 抹掉数字后的前缀（进度类已在上游被排除）。"""
    return f"{logger_name}:{_DIGITS.sub('#', message)[:40]}"


def _traceback_lines(exc: BaseException) -> list[str]:
    """把整段 traceback 抓成「一行一条」——日志禁止多行消息。"""
    import traceback

    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def _human_bytes(size: int | None) -> str | None:
    if size is None:
        return None
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"
