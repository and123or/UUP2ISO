"""检查点持久化（编排 §5、§12-8）。

三条硬性要求：

* **原子写**：`checkpoint.json.tmp` → `flush` + `fsync` → `os.replace`；
  断电也只会留下旧文件或新文件，不会出现半截 JSON；
* **损坏可识别**：读到非法 JSON 不抛到调用方（那会让程序起不来），
  而是返回 `corrupt=True`，由上层按"新任务"处理并记告警；
* **唯一写者**：单实例保证（WP0-T11），本模块不再加锁。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from uup2iso.core.models.checkpoint import CHECKPOINT_SCHEMA_VERSION, Checkpoint
from uup2iso.core.models.enums import TaskState

__all__ = ["CHECKPOINT_FILENAME", "CheckpointLoad", "CheckpointStore"]

CHECKPOINT_FILENAME = "checkpoint.json"


@dataclass(frozen=True, slots=True)
class CheckpointLoad:
    """一次读取的结果。"""

    checkpoint: Checkpoint | None = None
    corrupt: bool = False
    reason: str = ""

    @property
    def found(self) -> bool:
        return self.checkpoint is not None


class CheckpointStore:
    """``work\\<task_id>\\checkpoint.json`` 的读写端。"""

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = work_dir

    @property
    def path(self) -> Path:
        return self._work_dir / CHECKPOINT_FILENAME

    def exists(self) -> bool:
        return self.path.is_file()

    # -- 读 ---------------------------------------------------------------

    def load(self) -> CheckpointLoad:
        """读取检查点；文件缺失返回空结果，内容非法返回 ``corrupt=True``。"""
        if not self.path.is_file():
            return CheckpointLoad()

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return CheckpointLoad(corrupt=True, reason=f"无法解析：{exc}")

        try:
            return CheckpointLoad(checkpoint=Checkpoint.model_validate(payload))
        except ValidationError as exc:
            return CheckpointLoad(corrupt=True, reason=f"结构不合法：{exc.errors()[:2]}")

    # -- 写 ---------------------------------------------------------------

    def save(self, checkpoint: Checkpoint, *, now: datetime | None = None) -> None:
        """原子写入（写 tmp → fsync → replace）。"""
        checkpoint.schema_version = CHECKPOINT_SCHEMA_VERSION
        checkpoint.updated_at = now or datetime.now().astimezone()
        payload = checkpoint.model_dump(mode="json", by_alias=True, exclude_none=True)

        self._work_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())  # 断电保护：先落盘再替换
        tmp.replace(self.path)

    def initialize(
        self,
        *,
        task_id: str,
        config_fingerprint: str,
        input_fingerprint: str = "",
        started_at: datetime | None = None,
    ) -> Checkpoint:
        """新建一个 ``running`` 检查点并落盘。"""
        stamp = started_at or datetime.now().astimezone()
        checkpoint = Checkpoint(
            taskId=task_id,
            configFingerprint=config_fingerprint,
            inputFingerprint=input_fingerprint,
            status=TaskState.RUNNING,
            startedAt=stamp,
        )
        self.save(checkpoint, now=stamp)
        return checkpoint

    def heartbeat(self, checkpoint: Checkpoint, *, now: datetime | None = None) -> None:
        """心跳：只更新 ``updatedAt``（运行中每 60 秒一次，编排 §5）。"""
        self.save(checkpoint, now=now)
