"""任务与检查点模型（编排 §4、§5、§10；术语表 §2.2）。

状态取值**不自建**：直接复用 :mod:`uup2iso.core.models.enums` 里的
``TaskState`` / ``StageState`` / ``SubstepState``（术语表 §2.2 的唯一写法）。
本模块只定义「带时间戳的记录结构」与检查点本身：

| 类 | 用途 |
| --- | --- |
| :class:`SubstepRecord` | 子步骤状态 + 起止时间 |
| :class:`StageRecord` | 阶段状态 + 尝试次数 + 子步骤表 |
| :class:`ArtifactRecord` | 可复用产物（四条件里的第 4 条靠 size + sha256） |
| :class:`Checkpoint` | `work\\<task_id>\\checkpoint.json` 的完整结构 |
| :class:`TaskRuntime` | `tasks\\<id>.json` 的 ``runtime`` 段 |
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from uup2iso.core.models.enums import StageState, SubstepState, TaskState

__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "RESUMABLE_TASK_STATES",
    "TERMINAL_TASK_STATES",
    "ArtifactRecord",
    "Checkpoint",
    "ResumeDecision",
    "ResumePlan",
    "StageRecord",
    "SubstepRecord",
    "TaskRuntime",
]

CHECKPOINT_SCHEMA_VERSION = "1.0"

TERMINAL_TASK_STATES: frozenset[TaskState] = frozenset(
    {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}
)
"""终态；其中 `failed`/`cancelled` 允许 `--resume` 转回 `running`（`succeeded` 不允许）。"""

RESUMABLE_TASK_STATES: frozenset[TaskState] = frozenset(
    {TaskState.FAILED, TaskState.CANCELLED, TaskState.INTERRUPTED, TaskState.RUNNING}
)
"""可以续跑的来源状态（`running` 表示上次没正常结束，先标 interrupted 再续）。"""


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SubstepRecord(_Model):
    """子步骤状态（完成即写，不等阶段结束）。"""

    status: SubstepState = SubstepState.PENDING
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")

    @property
    def done(self) -> bool:
        return self.status in (SubstepState.DONE, SubstepState.SKIPPED)


class StageRecord(_Model):
    """阶段状态与它的子步骤。"""

    status: StageState = StageState.PENDING
    attempts: int = 0
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    skip_reason: str | None = Field(default=None, alias="skipReason")
    substeps: dict[str, SubstepRecord] = Field(default_factory=dict)

    @property
    def finished(self) -> bool:
        return self.status in (StageState.SUCCEEDED, StageState.SKIPPED)


class ArtifactRecord(_Model):
    """一个可复用产物。"""

    stage: str
    name: str
    path: str
    size: int = 0
    sha256: str = ""
    reusable: bool = True


class TaskRuntime(_Model):
    """`tasks\\<id>.json` 的 ``runtime`` 段（编排 §10）。"""

    status: TaskState = TaskState.DRAFT
    started_at: datetime | None = Field(default=None, alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    result: str = ""
    artifacts_summary: list[str] = Field(default_factory=list, alias="artifactsSummary")
    output_dir: str = Field(default="", alias="outputDir")
    staging_dir: str = Field(default="", alias="stagingDir")
    log_files: list[str] = Field(default_factory=list, alias="logFiles")
    audit_files: list[str] = Field(default_factory=list, alias="auditFiles")
    resumed: bool = False


class Checkpoint(_Model):
    """`work\\<task_id>\\checkpoint.json`（编排 §5）。"""

    schema_version: str = Field(default=CHECKPOINT_SCHEMA_VERSION, alias="schemaVersion")
    task_id: str = Field(default="", alias="taskId")
    config_fingerprint: str = Field(default="", alias="configFingerprint")
    input_fingerprint: str = Field(default="", alias="inputFingerprint")
    status: TaskState = TaskState.RUNNING
    current_stage: str | None = Field(default=None, alias="currentStage")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    stages: dict[str, StageRecord] = Field(default_factory=dict)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)

    def stage(self, name: str) -> StageRecord | None:
        return self.stages.get(name)

    def artifacts_for(self, stage: str) -> list[ArtifactRecord]:
        return [item for item in self.artifacts if item.stage == stage]


class ResumeDecision(StrEnum):
    """续跑判定（编排 §9 的分支）。"""

    NEW_TASK = "new_task"
    """没有检查点（或已损坏）：当作新任务，从 P0 开始。"""

    ALREADY_SUCCEEDED = "already_succeeded"
    """上次已成功：提示「已完成」，不要重复执行。"""

    RESUME = "resume"
    """可以续跑：按复用条件跳过已完成部分。"""

    REJECT_CONFIG_CHANGED = "reject_config_changed"
    """配置变了：拒绝续跑（``CONFIG_CHANGED``）。"""

    REJECT_INPUT_CHANGED = "reject_input_changed"
    """输入变了：从 `Prepare` 重做（``INPUT_CHANGED``）。"""

    INTERRUPTED = "interrupted"
    """检查点仍是 running：上次异常退出，标 interrupted 后按中断续跑。"""


class ResumePlan(BaseModel):
    """一次续跑判定的结果。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    decision: ResumeDecision
    message: str = ""
    reuse_stages: tuple[str, ...] = ()
    redo_stages: tuple[str, ...] = ()
    reuse_artifacts: tuple[str, ...] = ()
    resume_from_substep: dict[str, str] = Field(default_factory=dict)
    """阶段名 → 该阶段从哪个子步骤重做（第一个非 done 的子步骤）。"""

    @property
    def resumable(self) -> bool:
        return self.decision in (ResumeDecision.RESUME, ResumeDecision.INTERRUPTED)

    @property
    def needs_full_restart(self) -> bool:
        return self.decision in (
            ResumeDecision.NEW_TASK,
            ResumeDecision.REJECT_INPUT_CHANGED,
        )
