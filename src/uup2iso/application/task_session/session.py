"""TaskSession：任务状态机与续跑判定（编排 §4、§6、§9）。

状态迁移只走这里，避免各处随手改 `status` 导致状态机被绕过：

```
draft → queued → running → succeeded
                    ├→ failed      （可 resume）
                    ├→ cancelled   （可 resume）
                    └→ interrupted （进程消失，可 resume）
```

续跑的判定完全按 §6 的**四条件**——配置指纹、输入指纹、状态、产物（大小 + SHA256）：
任一不满足就重做该阶段；配置变了直接拒绝；输入变了从 `Prepare` 重做。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from uup2iso.core.models.checkpoint import (
    RESUMABLE_TASK_STATES,
    ArtifactRecord,
    Checkpoint,
    ResumeDecision,
    ResumePlan,
    StageRecord,
    SubstepRecord,
)
from uup2iso.core.models.enums import StageState, SubstepState, TaskState
from uup2iso.core.models.pipeline import PIPELINE_ORDER
from uup2iso.infrastructure.storage.checkpoint_store import CheckpointLoad, CheckpointStore

__all__ = ["TaskSession"]

_UNREUSABLE_SUFFIXES = (".part", ".tmp")


class TaskSession:
    """一个任务的检查点与会话状态。

    Args:
        task_id: 任务短 id。
        work_dir: ``work\\<task_id>\\``。
        config_fingerprint: 当前配置指纹（续跑比对用）。
        store: 检查点存储（默认按 `work_dir` 构造）。
        clock: 可注入时钟（测试用）。
    """

    def __init__(
        self,
        *,
        task_id: str,
        work_dir: Path,
        config_fingerprint: str = "",
        store: CheckpointStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._task_id = task_id
        self._work_dir = work_dir
        self._config_fingerprint = config_fingerprint
        self._store = store or CheckpointStore(work_dir)
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._checkpoint: Checkpoint | None = None

    # -- 属性 -------------------------------------------------------------

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def work_dir(self) -> Path:
        return self._work_dir

    @property
    def store(self) -> CheckpointStore:
        return self._store

    @property
    def checkpoint(self) -> Checkpoint | None:
        return self._checkpoint

    # -- 创建与加载 -------------------------------------------------------

    def start(self, *, input_fingerprint: str = "") -> Checkpoint:
        """新建检查点（`running`）并落盘。"""
        self._checkpoint = self._store.initialize(
            task_id=self._task_id,
            config_fingerprint=self._config_fingerprint,
            input_fingerprint=input_fingerprint,
            started_at=self._clock(),
        )
        return self._checkpoint

    def load(self) -> CheckpointLoad:
        """读取已有检查点（不改变状态）。"""
        result = self._store.load()
        self._checkpoint = result.checkpoint
        return result

    def begin_resume(self, *, input_fingerprint: str | None = None) -> Checkpoint:
        """把可续跑的状态转回 `running`，沿用原指纹与 taskId（编排 §9）。"""
        checkpoint = self._require()
        checkpoint.status = TaskState.RUNNING
        if input_fingerprint is not None:
            checkpoint.input_fingerprint = input_fingerprint
        self._store.save(checkpoint, now=self._clock())
        return checkpoint

    # -- 状态迁移 ---------------------------------------------------------

    def begin_stage(self, stage: str) -> None:
        checkpoint = self._require()
        state = checkpoint.stages.setdefault(stage, StageRecord())
        state.status = StageState.RUNNING
        state.attempts += 1
        state.started_at = state.started_at or self._clock()
        checkpoint.current_stage = stage
        self._save()

    def end_stage(
        self,
        stage: str,
        status: StageState,
        *,
        skip_reason: str | None = None,
    ) -> None:
        checkpoint = self._require()
        state = checkpoint.stages.setdefault(stage, StageRecord())
        state.status = status
        state.skip_reason = skip_reason
        if status in (
            StageState.SUCCEEDED,
            StageState.FAILED,
            StageState.SKIPPED,
            StageState.CANCELLED,
        ):
            state.finished_at = self._clock()
        self._save()

    def begin_substep(self, stage: str, name: str) -> None:
        checkpoint = self._require()
        state = checkpoint.stages.setdefault(stage, StageRecord())
        substep = state.substeps.setdefault(name, SubstepRecord())
        substep.status = SubstepState.RUNNING
        substep.started_at = substep.started_at or self._clock()
        self._save()

    def end_substep(self, stage: str, name: str, status: SubstepState) -> None:
        """子步骤**完成即写**（编排 §3 规则 1），不等阶段结束。"""
        checkpoint = self._require()
        state = checkpoint.stages.setdefault(stage, StageRecord())
        substep = state.substeps.setdefault(name, SubstepRecord())
        substep.status = status
        if status in (SubstepState.DONE, SubstepState.SKIPPED, SubstepState.FAILED):
            substep.finished_at = self._clock()
        self._save()

    def record_artifact(
        self,
        stage: str,
        name: str,
        path: Path,
        *,
        sha256: str = "",
        reusable: bool = True,
    ) -> ArtifactRecord:
        """登记产物（相对 `work_dir` 存储，便于整个目录搬走）。"""
        checkpoint = self._require()
        relative = _relative_to(path, self._work_dir)
        size = path.stat().st_size if path.is_file() else 0
        digest = sha256 or (_sha256(path) if path.is_file() else "")
        record = ArtifactRecord(
            stage=stage,
            name=name,
            path=relative,
            size=size,
            sha256=digest,
            reusable=reusable,
        )
        checkpoint.artifacts = [
            item for item in checkpoint.artifacts if not (item.stage == stage and item.name == name)
        ]
        checkpoint.artifacts.append(record)
        self._save()
        return record

    def finish(self, status: TaskState, *, result: str = "") -> None:
        """落终态（`succeeded` / `failed` / `cancelled`）。"""
        checkpoint = self._require()
        checkpoint.status = status
        checkpoint.updated_at = self._clock()
        self._store.save(checkpoint, now=self._clock())
        _ = result

    def interrupt(self) -> bool:
        """把「仍是 running」的检查点标 `interrupted`（进程消失后下次启动调用）。"""
        checkpoint = self._checkpoint or self.load().checkpoint
        if checkpoint is None or checkpoint.status is not TaskState.RUNNING:
            return False
        checkpoint.status = TaskState.INTERRUPTED
        self._checkpoint = checkpoint
        self._store.save(checkpoint, now=self._clock())
        return True

    # -- 续跑判定 ---------------------------------------------------------

    def plan_resume(
        self,
        *,
        config_fingerprint: str | None = None,
        input_fingerprint: str | None = None,
    ) -> ResumePlan:
        """按编排 §9/§6 计算「复用哪些、重做哪些」。

        Args:
            config_fingerprint: 当前配置指纹；``None`` 用会话创建时的值。
            input_fingerprint: 当前输入指纹；``None`` 表示「未变化」，沿用检查点里的值。

        Returns:
            :class:`ResumePlan`。
        """
        load = self.load()
        if load.corrupt:
            return ResumePlan(
                decision=ResumeDecision.NEW_TASK,
                message=f"检查点不可用（{load.reason}），按新任务执行",
            )

        checkpoint = load.checkpoint
        if checkpoint is None:
            return ResumePlan(decision=ResumeDecision.NEW_TASK, message="没有检查点，按新任务执行")

        current_config = config_fingerprint or self._config_fingerprint
        if (
            checkpoint.config_fingerprint
            and current_config
            and (checkpoint.config_fingerprint != current_config)
        ):
            return ResumePlan(
                decision=ResumeDecision.REJECT_CONFIG_CHANGED,
                message="配置已变更，请新建任务或恢复原配置（不允许混用中间产物）",
            )

        if (
            input_fingerprint is not None
            and checkpoint.input_fingerprint
            and input_fingerprint != checkpoint.input_fingerprint
        ):
            return ResumePlan(
                decision=ResumeDecision.REJECT_INPUT_CHANGED,
                message="输入已变更，需从 Prepare 重做",
                redo_stages=tuple(stage.value for stage in PIPELINE_ORDER),
            )

        if checkpoint.status is TaskState.SUCCEEDED:
            return ResumePlan(
                decision=ResumeDecision.ALREADY_SUCCEEDED,
                message="该任务已完成，无需续跑",
                reuse_stages=tuple(checkpoint.stages),
            )

        decision = (
            ResumeDecision.INTERRUPTED
            if checkpoint.status is TaskState.RUNNING
            else ResumeDecision.RESUME
        )
        reuse, redo, reused_artifacts = _split_reusable(checkpoint, self._work_dir)
        from_substep = _resume_points(checkpoint, redo)

        message = (
            f"将复用 {len(reuse)} 个阶段 / {len(reused_artifacts)} 个产物，重做 {len(redo)} 个阶段"
            if decision is ResumeDecision.RESUME
            else "上次运行未正常结束（interrupted），将按中断续跑"
        )
        return ResumePlan(
            decision=decision,
            message=message,
            reuse_stages=tuple(reuse),
            redo_stages=tuple(redo),
            reuse_artifacts=tuple(reused_artifacts),
            resume_from_substep=from_substep,
        )

    def can_resume(self) -> bool:
        """当前检查点状态是否属于「可续跑」（不含指纹校验）。"""
        checkpoint = self._checkpoint or self.load().checkpoint
        return checkpoint is not None and checkpoint.status in RESUMABLE_TASK_STATES

    def reusable_artifacts(self) -> tuple[ArtifactRecord, ...]:
        """当前可复用的产物（四条件全满足的那些）。"""
        checkpoint = self._checkpoint or self.load().checkpoint
        if checkpoint is None:
            return ()
        return tuple(
            item
            for item in checkpoint.artifacts
            if item.reusable and _artifact_valid(item, self._work_dir)
        )

    # -- 内部 -------------------------------------------------------------

    def _require(self) -> Checkpoint:
        if self._checkpoint is None:
            raise RuntimeError("会话尚未 start() 或 load()")
        return self._checkpoint

    def _save(self) -> None:
        self._store.save(self._require(), now=self._clock())


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _split_reusable(
    checkpoint: Checkpoint, work_dir: Path
) -> tuple[list[str], list[str], list[str]]:
    reuse: list[str] = []
    redo: list[str] = []
    reused_artifacts: list[str] = []
    blocked = False

    for stage in PIPELINE_ORDER:
        name = stage.value
        state = checkpoint.stages.get(name)
        if blocked or state is None:
            blocked = True
            redo.append(name)
            continue
        if state.status is StageState.SKIPPED:
            reuse.append(name)
            continue

        artifacts = checkpoint.artifacts_for(name)
        valid = state.status is StageState.SUCCEEDED and all(
            item.reusable and _artifact_valid(item, work_dir) for item in artifacts
        )
        if valid:
            reuse.append(name)
            reused_artifacts.extend(item.name for item in artifacts if item.reusable)
        else:
            blocked = True
            redo.append(name)

    return reuse, redo, reused_artifacts


def _resume_points(checkpoint: Checkpoint, redo: list[str]) -> dict[str, str]:
    """对要重做的阶段，找出「第一个没做完的子步骤」（子步骤级续跑）。"""
    points: dict[str, str] = {}
    for name in redo:
        state = checkpoint.stages.get(name)
        if state is None:
            continue
        for substep_name, substep in state.substeps.items():
            if substep.status not in (SubstepState.DONE, SubstepState.SKIPPED):
                points[name] = substep_name
                break
    return points


def _artifact_valid(record: ArtifactRecord, work_dir: Path) -> bool:
    """四条件里的第 4 条：产物存在且大小、SHA256 与记录一致。"""
    if record.path.endswith(_UNREUSABLE_SUFFIXES):
        return False
    path = work_dir / record.path
    if not path.is_file():
        return False
    if record.size and path.stat().st_size != record.size:
        return False
    if record.sha256:
        return _sha256(path) == record.sha256
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_to(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)
