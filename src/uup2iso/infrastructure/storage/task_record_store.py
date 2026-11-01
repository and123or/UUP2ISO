"""任务记录与保留策略（编排 §10）。

`tasks\\<id>.json` = `TaskConfig` + `runtime` 段；本模块只管 ``runtime`` 的读写与**保留最近 50 个**。

清理动作严格限定：删记录文件 + 删 `work\\<id>\\`。
**绝不**碰用户输出目录里的最终产物与伴生文件；正在运行的任务永不清理。
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from uup2iso.core.models.checkpoint import TaskRuntime
from uup2iso.core.models.enums import TaskState

__all__ = [
    "DEFAULT_RETENTION",
    "RUNTIME_KEY",
    "TaskPruneReport",
    "TaskRecord",
    "TaskRecordStore",
]

RUNTIME_KEY = "runtime"
DEFAULT_RETENTION = 50
"""保留最近 50 个任务记录（编排 §10，决策 D4）。"""

_TASK_ID = re.compile(r"^[0-9a-fA-F]{1,32}$")


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """任务记录的概要。"""

    task_id: str
    status: TaskState
    started_at: datetime | None = None
    finished_at: datetime | None = None
    path: Path | None = None

    @property
    def sort_time(self) -> datetime:
        """排序键：优先 `finishedAt`，未完成用 `startedAt`，都没有就当最旧。"""
        stamp = self.finished_at or self.started_at
        return stamp or datetime.min.replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class TaskPruneReport:
    """一次保留策略清理的结果。"""

    kept: int = 0
    pruned: tuple[str, ...] = ()
    pruned_work_dirs: tuple[Path, ...] = ()
    skipped_running: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.pruned)


class TaskRecordStore:
    """``tasks`` 目录的读写端。

    Args:
        tasks_dir: ``{程序目录}\\tasks``。
        work_root: ``{程序目录}\\work``；不传则不删工作目录（只删记录）。
    """

    def __init__(self, tasks_dir: Path, *, work_root: Path | None = None) -> None:
        self._tasks_dir = tasks_dir
        self._work_root = work_root

    # -- 路径 -------------------------------------------------------------

    def path_for(self, task_id: str) -> Path:
        if not _TASK_ID.match(task_id):
            raise ValueError(f"非法任务 id：{task_id!r}")
        return self._tasks_dir / f"{task_id}.json"

    def work_dir_for(self, task_id: str) -> Path | None:
        if self._work_root is None:
            return None
        return self._work_root / task_id

    # -- 读写 -------------------------------------------------------------

    def read(self, task_id: str) -> dict[str, object]:
        path = self.path_for(task_id)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def write_runtime(self, task_id: str, runtime: TaskRuntime) -> Path:
        """把 ``runtime`` 段合并进任务文件（保留 config 等其它字段），原子写。"""
        payload = self.read(task_id)
        payload[RUNTIME_KEY] = runtime.model_dump(mode="json", by_alias=True, exclude_none=True)
        path = self.path_for(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        tmp.replace(path)
        return path

    def runtime_of(self, task_id: str) -> TaskRuntime | None:
        payload = self.read(task_id)
        raw = payload.get(RUNTIME_KEY)
        if not isinstance(raw, dict):
            return None
        try:
            return TaskRuntime.model_validate(raw)
        except ValueError:
            return None

    # -- 列表与保留 -------------------------------------------------------

    def summaries(self) -> tuple[TaskRecord, ...]:
        """所有任务记录的概要（跳过损坏文件，不让一条坏记录拖垮列表）。"""
        if not self._tasks_dir.is_dir():
            return ()
        records: list[TaskRecord] = []
        for path in sorted(self._tasks_dir.glob("*.json")):
            task_id = path.stem
            if not _TASK_ID.match(task_id):
                continue
            runtime = self.runtime_of(task_id)
            records.append(
                TaskRecord(
                    task_id=task_id,
                    status=runtime.status if runtime else TaskState.DRAFT,
                    started_at=runtime.started_at if runtime else None,
                    finished_at=runtime.finished_at if runtime else None,
                    path=path,
                )
            )
        return tuple(records)

    def prune(
        self,
        *,
        retention: int = DEFAULT_RETENTION,
        keep: Iterable[str] = (),
    ) -> TaskPruneReport:
        """保留最近 ``retention`` 个记录，超出的删记录 + 删工作目录。

        Args:
            retention: 保留数量（默认 50）。
            keep: 额外必须保留的 task id（例如正在运行的任务）。

        Returns:
            :class:`TaskPruneReport`；调用方据此记 ``event=task_record_pruned``。
        """
        protected = {*keep}
        records = sorted(self.summaries(), key=lambda item: item.sort_time, reverse=True)
        kept: list[str] = []
        pruned: list[str] = []
        skipped_running: list[str] = []
        removed_dirs: list[Path] = []

        for index, record in enumerate(records):
            must_keep = index < retention or record.task_id in protected
            if must_keep:
                kept.append(record.task_id)
                continue
            if record.status is TaskState.RUNNING:
                skipped_running.append(record.task_id)  # 正在运行的任务永不清理
                kept.append(record.task_id)
                continue

            if record.path is not None:
                record.path.unlink(missing_ok=True)
            work_dir = self._safe_work_dir(record.task_id)
            if work_dir is not None and work_dir.is_dir():
                shutil.rmtree(work_dir, ignore_errors=True)
                removed_dirs.append(work_dir)
            pruned.append(record.task_id)

        return TaskPruneReport(
            kept=len(kept),
            pruned=tuple(pruned),
            pruned_work_dirs=tuple(removed_dirs),
            skipped_running=tuple(skipped_running),
        )

    # -- 内部 -------------------------------------------------------------

    def _safe_work_dir(self, task_id: str) -> Path | None:
        """只允许删除 ``work_root/<合法 id>``，防止路径穿越误删。"""
        candidate = self.work_dir_for(task_id)
        if candidate is None:
            return None
        root = self._work_root.resolve() if self._work_root else None
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if root is None or resolved.parent != root:
            return None
        return resolved
