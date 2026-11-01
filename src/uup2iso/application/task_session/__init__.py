"""任务状态机与断点续跑（WP1-T3）。

* :class:`TaskSession` —— 状态迁移（draft→queued→running→终态）与检查点写入；
* :meth:`TaskSession.plan_resume` —— 按编排 §6 的四条件判定「复用哪些、重做哪些」；
* 检查点持久化在 :class:`~uup2iso.infrastructure.storage.checkpoint_store.CheckpointStore`（原子写）。

检查点结构见 `work\\<task_id>\\checkpoint.json`（编排 §5）。
"""

from __future__ import annotations

from uup2iso.application.task_session.session import TaskSession

__all__ = ["TaskSession"]
