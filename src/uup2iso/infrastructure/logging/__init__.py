"""日志子系统（WP0-T10）：行格式、写入、脱敏、清理、按任务索引。

| 模块 | 职责 |
| --- | --- |
| :mod:`.format` | 行格式（级别 5 字符宽、单行约束） |
| :mod:`.sink` | `LogSink`：每进程一文件、审计分流、采样兜底、进度不落盘 |
| :mod:`.levels` | 级别与来源判定（exe→INFO / 源码 GUI→DEBUG / CLI→INFO / --debug） |
| :mod:`.cleanup` | 三条件清理 + 审计只读化 |
| :mod:`.index` | `tasks\\<id>.json` 日志索引与「不漏不多」的合并 |
| :mod:`.redact` | 脱敏（代理凭据、产品密钥、URL 签名） |

硬约束（日志 §2–§6）：一个进程一个文件、进度绝不落盘、审计退出后只读、
启动只补最近 3 个审计、清理三项同时达标。
"""

from __future__ import annotations

from uup2iso.infrastructure.logging.cleanup import (
    AUDIT_PATTERNS,
    RUN_PATTERNS,
    LogThresholds,
    PruneReport,
    ensure_audit_readonly,
    is_readonly,
    prune,
    set_readonly,
)
from uup2iso.infrastructure.logging.format import (
    AUDIT,
    DEBUG,
    ERROR,
    INFO,
    WARN,
    format_line,
    level_rank,
    single_line,
)
from uup2iso.infrastructure.logging.index import (
    AUDIT_FILES_KEY,
    LOG_FILES_KEY,
    RESUMED_KEY,
    LogSource,
    MergedLog,
    TaskLogIndex,
    TaskLogRecord,
    collect_task_log,
)
from uup2iso.infrastructure.logging.levels import resolve_level
from uup2iso.infrastructure.logging.redact import redact_text, redact_url
from uup2iso.infrastructure.logging.sink import (
    AuditWriteError,
    LogPaths,
    LogSink,
    RepetitionSampler,
)

__all__ = [
    "AUDIT",
    "AUDIT_FILES_KEY",
    "AUDIT_PATTERNS",
    "DEBUG",
    "ERROR",
    "INFO",
    "LOG_FILES_KEY",
    "RESUMED_KEY",
    "RUN_PATTERNS",
    "WARN",
    "AuditWriteError",
    "LogPaths",
    "LogSink",
    "LogSource",
    "LogThresholds",
    "MergedLog",
    "PruneReport",
    "RepetitionSampler",
    "TaskLogIndex",
    "TaskLogRecord",
    "collect_task_log",
    "ensure_audit_readonly",
    "format_line",
    "is_readonly",
    "level_rank",
    "prune",
    "redact_text",
    "redact_url",
    "resolve_level",
    "set_readonly",
    "single_line",
]
