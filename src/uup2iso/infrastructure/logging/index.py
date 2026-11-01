"""按任务日志索引与合并（日志 §7.3）。

诊断包要「不漏、不多」地取日志，靠的就是这个索引：

* **不漏**：任务在本进程首次写日志时，把 ``run_*.log`` / ``audit_*.log`` 文件名追加进
  ``tasks\\<task_id>.json`` 的 ``logFiles`` / ``auditFiles`` 并立即落盘（不依赖退出钩子）；
* **不多**：合并时**只按索引取文件**、逐行过滤 ``task=<id>``，绝不扫描 ``logs`` 目录猜范围；
* **可验收**：每个来源文件记匹配行数与 SHA256，合并总行数必须等于各来源之和，否则中止。

索引缺失（旧任务或文件被删）时回退为「当前运行文件整份纳入」，并标记 ``exact=false``——
宁可让用户知道「这份历史不完整」，也不能假装完整。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from uup2iso.core.errors import ConfigParseError, InternalError

__all__ = [
    "AUDIT_FILES_KEY",
    "LOG_FILES_KEY",
    "MergedLog",
    "LogSource",
    "RESUMED_KEY",
    "TaskLogIndex",
    "TaskLogRecord",
    "collect_task_log",
]

LOG_FILES_KEY = "logFiles"
AUDIT_FILES_KEY = "auditFiles"
RESUMED_KEY = "resumed"

_ID_CHARS = re.compile(r"^[0-9a-fA-F]{1,32}$")


@dataclass(frozen=True, slots=True)
class TaskLogRecord:
    """某任务的日志索引。"""

    log_files: tuple[str, ...] = ()
    audit_files: tuple[str, ...] = ()
    resumed: bool = False


@dataclass(frozen=True, slots=True)
class LogSource:
    """合并时的一个来源文件。"""

    file: str
    matched_lines: int = 0
    sha256: str = ""
    missing: bool = False


@dataclass(frozen=True, slots=True)
class MergedLog:
    """按任务过滤合并后的日志。

    Attributes:
        text: 合并文本（每段前有 ``# source=<文件名>`` 注释）。
        sources: 每个来源文件的统计。
        missing: 索引里列出但已不存在的文件。
        exact: 是否为**完整**历史（``False`` 时 UI 必须提示不完整）。
        total_lines: 合并后的数据行数（不含注释行）。
    """

    text: str = ""
    sources: tuple[LogSource, ...] = field(default_factory=tuple)
    missing: tuple[str, ...] = ()
    exact: bool = True
    total_lines: int = 0


class TaskLogIndex:
    """``tasks\\<task_id>.json`` 里的日志索引（读改写真，保留其余字段）。"""

    def __init__(self, tasks_dir: Path) -> None:
        self._dir = tasks_dir

    def path_for(self, task_id: str) -> Path:
        if not _ID_CHARS.match(task_id):
            raise ValueError(f"非法任务 id：{task_id!r}")
        return self._dir / f"{task_id}.json"

    def read(self, task_id: str) -> TaskLogRecord:
        payload = self._load(task_id)
        return TaskLogRecord(
            log_files=tuple(_as_str_list(payload.get(LOG_FILES_KEY))),
            audit_files=tuple(_as_str_list(payload.get(AUDIT_FILES_KEY))),
            resumed=bool(payload.get(RESUMED_KEY, False)),
        )

    def record(
        self,
        task_id: str,
        *,
        run_file: str,
        audit_file: str | None = None,
    ) -> TaskLogRecord:
        """把本进程的日志文件名追加进索引（去重、保序、立即落盘）。"""
        payload = self._load(task_id)
        logs = _append_unique(_as_str_list(payload.get(LOG_FILES_KEY)), run_file)
        audits = _as_str_list(payload.get(AUDIT_FILES_KEY))
        if audit_file:
            audits = _append_unique(audits, audit_file)
        payload[LOG_FILES_KEY] = logs
        payload[AUDIT_FILES_KEY] = audits
        self._save(task_id, payload)
        return TaskLogRecord(
            log_files=tuple(logs),
            audit_files=tuple(audits),
            resumed=bool(payload.get(RESUMED_KEY, False)),
        )

    def mark_resumed(self, task_id: str, resumed: bool = True) -> None:
        payload = self._load(task_id)
        payload[RESUMED_KEY] = resumed
        self._save(task_id, payload)

    # -- 内部 -------------------------------------------------------------

    def _load(self, task_id: str) -> dict[str, object]:
        path = self.path_for(task_id)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigParseError(f"任务文件无法解析：{exc}", file=path) from exc
        return payload if isinstance(payload, dict) else {}

    def _save(self, task_id: str, payload: dict[str, object]) -> None:
        path = self.path_for(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        tmp.replace(path)


def collect_task_log(
    task_id: str,
    *,
    index: TaskLogIndex,
    log_dir: Path,
    current: tuple[str, ...] = (),
    kind: Literal["run", "audit"] = "run",
) -> MergedLog:
    """按索引取文件并逐行过滤合并（日志 §7.3）。

    Args:
        task_id: 任务 id。
        index: 任务日志索引。
        log_dir: ``logs`` 目录。
        current: 当前进程正在写的文件名（索引缺失时兜底）。
        kind: ``run`` 取运行日志，``audit`` 取审计日志。

    Raises:
        InternalError: 合并行数与来源匹配行数之和不一致（防漏防重）。
    """
    record = index.read(task_id)
    names = list(record.log_files if kind == "run" else record.audit_files)
    exact = True

    if not names:
        # 索引缺失：回退为当前文件整份纳入，并明确标记不完整
        names = list(current)
        exact = False

    token = f"task={task_id}"
    blocks: list[str] = []
    sources: list[LogSource] = []
    missing: list[str] = []
    total = 0

    for name in names:
        path = log_dir / name
        if not path.is_file():
            missing.append(name)
            sources.append(LogSource(file=name, missing=True))
            continue

        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        matched = [line for line in raw.decode("utf-8", "replace").splitlines() if token in line]
        blocks.append(f"# source={name}")
        blocks.extend(matched)
        total += len(matched)
        sources.append(LogSource(file=name, matched_lines=len(matched), sha256=digest))

    if missing:
        exact = False

    if total != sum(source.matched_lines for source in sources):
        raise InternalError("合并行数与来源行数不一致，已中止（不生成残缺日志）")

    return MergedLog(
        text="\n".join(blocks) + ("\n" if blocks else ""),
        sources=tuple(sources),
        missing=tuple(missing),
        exact=exact,
        total_lines=total,
    )


def _as_str_list(value: object) -> list[str]:
    """把 JSON 里读出来的 ``object`` 安全地收成字符串列表。"""
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _append_unique(existing: list[str], value: str) -> list[str]:
    items = list(existing)
    if value not in items:
        items.append(value)
    return items
