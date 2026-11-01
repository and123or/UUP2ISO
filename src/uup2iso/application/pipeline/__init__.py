"""流水线（WP1-T1 / WP1-T2）：阶段计划与阶段驱动。

* :func:`plan_pipeline` —— 把 `TaskConfig` 裁剪成 8 阶段计划（含跳过原因与子步骤幂等标注）；
* :class:`PipelineOrchestrator` —— 按计划执行：白名单重试、取消、事件记账、`--dry-run` 输出。

阶段处理器由 WS2/WS3 提供（`StageHandler`）；编排器只管「什么时候跑、跑几次、怎么记账」。
检查点持久化（`checkpoint.json`、续跑）属 WP1-T3。
"""

from __future__ import annotations

from uup2iso.application.pipeline.handlers import (
    CleanupHook,
    StageContext,
    StageHandler,
    StageOutcome,
)
from uup2iso.application.pipeline.orchestrator import (
    DryRunReport,
    DryRunStage,
    PipelineOrchestrator,
    PipelineRun,
)
from uup2iso.application.pipeline.planner import plan_pipeline, stage_index
from uup2iso.application.pipeline.retry import RetryPolicy, is_retryable

__all__ = [
    "CleanupHook",
    "DryRunReport",
    "DryRunStage",
    "PipelineOrchestrator",
    "PipelineRun",
    "RetryPolicy",
    "StageContext",
    "StageHandler",
    "StageOutcome",
    "is_retryable",
    "plan_pipeline",
    "stage_index",
]
