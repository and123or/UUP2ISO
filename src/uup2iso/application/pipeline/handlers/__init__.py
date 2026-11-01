"""阶段处理器契约（编排 §2/§3）。

编排器只负责「什么时候跑、跑几次、怎么记账」，**具体怎么干活**由各阶段处理器实现：

| 阶段 | 处理器落地位置 |
| --- | --- |
| `Prepare` | WP1-T4（输入识别与映像准备） |
| `Fetch` | WS2（数据源与下载） |
| `Convert` / `Slim` / `Optimize` / `Integrate` / `Unattend` | WS2 / WS3 |
| `Output` | WS3（输出与校验） |

处理器可选实现 `describe()` 与 `reusable_artifacts()`，供 `--dry-run` 列出命令与将复用产物。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from uup2iso.core.abstractions.runtime import CancelToken
from uup2iso.core.models.config import TaskConfig
from uup2iso.core.models.pipeline import StageKind, StagePlan

__all__ = [
    "CleanupHook",
    "PrepareOps",
    "PrepareOutcome",
    "PrepareStageHandler",
    "StageContext",
    "StageHandler",
    "StageOutcome",
    "has_dry_run_info",
]

CleanupHook = Callable[["StageContext"], Awaitable[None]]
"""重试前的残留清理（挂载类阶段必需，对应引擎 §7 的 `Dism /Cleanup-Wim`）。"""


@dataclass(slots=True)
class StageContext:
    """一次阶段执行的全部输入。

    Attributes:
        task_id: 任务短 id（日志/产物命名都用它）。
        config: 已校验的任务配置。
        stage: 当前阶段。
        plan: 该阶段的计划（含子步骤与幂等标注）。
        work_dir: 任务工作目录 ``work\\<task_id>\\``。
        attempt: 第几次尝试（1 起）。
        cancel: 取消令牌。
        log: 运行日志写入端。
        progress: 进度通道（**不落盘**）。
        extras: 处理器之间传递的运行时数据（如 `install.wim` 路径）。
    """

    task_id: str
    config: TaskConfig
    stage: StageKind
    plan: StagePlan
    work_dir: Path
    attempt: int = 1
    cancel: CancelToken | None = None
    log: object | None = None
    progress: object | None = None
    extras: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class StageHandler(Protocol):
    """阶段处理器：只要求 ``run``，`describe` / `reusable_artifacts` 可选。"""

    stage: StageKind

    async def run(self, ctx: StageContext) -> None:
        """执行本阶段；失败抛异常（由编排器决定是否重试）。"""
        ...


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """一个阶段的执行结局。"""

    stage: StageKind
    status: str
    """取值同编排 §4.2 的阶段态：``succeeded`` / ``failed`` / ``skipped`` / ``cancelled``。"""

    attempts: int = 0
    duration: float = 0.0
    error: BaseException | None = None
    skip_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"succeeded", "skipped"}


def has_dry_run_info(handler: object) -> tuple[bool, bool]:
    """处理器是否提供 ``describe`` / ``reusable_artifacts``（鸭子类型判断）。"""
    return (
        callable(getattr(handler, "describe", None)),
        callable(getattr(handler, "reusable_artifacts", None)),
    )


from uup2iso.application.pipeline.handlers.prepare import (  # noqa: E402 - 放末尾避免循环导入
    PrepareOps,
    PrepareOutcome,
    PrepareStageHandler,
)
