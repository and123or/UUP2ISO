"""阶段模型（编排 §2、§2.1、§3）。

三条不可动摇的约定：

* **8 个阶段名是唯一写法**（`Prepare/Fetch/Convert/Slim/Optimize/Integrate/Unattend/Output`）；
  历史名 `DetectInput`/`PrepareImage`/`Validate` 已降级为子步骤（`Prepare.probe|materialize|extract`、`Output.verify`）；
* **`Prepare` 永不整段跳过**——即使 `startStage=Slim`，`probe` 与 `materialize|extract` 仍要执行；
* 子步骤带**幂等性标注**：`mount`/`unmount`/`inject` 非幂等，重跑前必须先清理残留（引擎 §7）。

阶段对外只有 8 个（UI 阶段串不膨胀），子步骤只出现在阶段详情里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "PIPELINE_ORDER",
    "SKIP_DRY_RUN",
    "SKIP_INPUT_SATISFIED",
    "SKIP_MERGED_INTO_FETCH_VERIFY",
    "SKIP_NOT_ENABLED",
    "SKIP_NO_OPTIONS",
    "SKIP_USER_START_STAGE",
    "STAGE_SUBSTEPS",
    "PipelinePlan",
    "PlannedSubstep",
    "StageKind",
    "StagePlan",
    "SubstepSpec",
]

# 跳过原因（写进 `status=skipped` 的 reason 字段）
SKIP_NO_OPTIONS = "no_options"
SKIP_INPUT_SATISFIED = "input_satisfied"
SKIP_NOT_ENABLED = "not_enabled"
SKIP_DRY_RUN = "dry_run"
SKIP_USER_START_STAGE = "user_start_stage"
SKIP_MERGED_INTO_FETCH_VERIFY = "merged_into_fetch_verify"
"""入口 A 的 `Prepare.selfcheck` 并入 `Fetch.verify`（编排 §2 P0 备注）。"""


class StageKind(StrEnum):
    """8 个阶段（顺序即执行顺序，不允许乱序）。"""

    PREPARE = "Prepare"
    FETCH = "Fetch"
    CONVERT = "Convert"
    SLIM = "Slim"
    OPTIMIZE = "Optimize"
    INTEGRATE = "Integrate"
    UNATTEND = "Unattend"
    OUTPUT = "Output"


PIPELINE_ORDER: tuple[StageKind, ...] = (
    StageKind.PREPARE,
    StageKind.FETCH,
    StageKind.CONVERT,
    StageKind.SLIM,
    StageKind.OPTIMIZE,
    StageKind.INTEGRATE,
    StageKind.UNATTEND,
    StageKind.OUTPUT,
)
"""固定顺序（编排 §2）：跳过某个阶段不改变其余阶段的相对次序。"""


@dataclass(frozen=True, slots=True)
class SubstepSpec:
    """子步骤的静态定义（编排 §3）。

    Attributes:
        name: 子步骤名，也是日志 ``substep=`` 的取值。
        idempotent: 是否幂等；非幂等项重跑前必须清理残留。
        cleanup_before_rerun: 重跑前是否强制做残留清理（挂载类）。
    """

    name: str
    idempotent: bool = True
    cleanup_before_rerun: bool = False


STAGE_SUBSTEPS: dict[StageKind, tuple[SubstepSpec, ...]] = {
    StageKind.PREPARE: (
        SubstepSpec("probe"),
        SubstepSpec("materialize"),  # ESD → WIM
        SubstepSpec("extract"),  # ISO 解包
        SubstepSpec("selfcheck"),
    ),
    StageKind.FETCH: (
        SubstepSpec("manifest"),
        SubstepSpec("download"),
        SubstepSpec("verify"),
    ),
    StageKind.CONVERT: (
        SubstepSpec("config"),
        SubstepSpec("cab"),
        SubstepSpec("aggregate"),  # 重跑覆盖 install.wim
        SubstepSpec("skeleton"),
        SubstepSpec("bootwim"),
    ),
    StageKind.SLIM: (
        SubstepSpec("mount", idempotent=False, cleanup_before_rerun=True),
        SubstepSpec("apply"),
        SubstepSpec("unmount", idempotent=False, cleanup_before_rerun=True),
    ),
    StageKind.OPTIMIZE: (
        SubstepSpec("mount", idempotent=False, cleanup_before_rerun=True),
        SubstepSpec("offline-reg"),
        SubstepSpec("cleanup"),
        SubstepSpec("unmount", idempotent=False, cleanup_before_rerun=True),
    ),
    StageKind.INTEGRATE: (
        SubstepSpec("mount", idempotent=False, cleanup_before_rerun=True),
        SubstepSpec("updates"),
        SubstepSpec("drivers"),
        SubstepSpec("language"),
        SubstepSpec("registry"),
        SubstepSpec("verify"),
        SubstepSpec("unmount", idempotent=False, cleanup_before_rerun=True),
    ),
    StageKind.UNATTEND: (
        SubstepSpec("generate"),
        SubstepSpec("validate"),
        SubstepSpec("mount", idempotent=False, cleanup_before_rerun=True),
        SubstepSpec("inject", idempotent=False, cleanup_before_rerun=True),
        SubstepSpec("unmount", idempotent=False, cleanup_before_rerun=True),
    ),
    StageKind.OUTPUT: (
        SubstepSpec("assemble"),
        SubstepSpec("package"),
        SubstepSpec("verify"),
        SubstepSpec("commit"),  # 已提交则跳过
    ),
}


@dataclass(frozen=True, slots=True)
class PlannedSubstep:
    """计划中的一个子步骤（含自己是否被跳过）。"""

    name: str
    idempotent: bool = True
    cleanup_before_rerun: bool = False
    skip_reason: str | None = None

    @property
    def skipped(self) -> bool:
        return self.skip_reason is not None


@dataclass(frozen=True, slots=True)
class StagePlan:
    """一个阶段的执行计划。

    Attributes:
        stage: 阶段名。
        substeps: 要执行的子步骤（已按入口裁剪）。
        skip_reason: 整段跳过原因；``None`` 表示执行。
        implied: 是否「用户未选但必须执行」（目前只有 `Prepare` 会为真）。
    """

    stage: StageKind
    substeps: tuple[PlannedSubstep, ...] = ()
    skip_reason: str | None = None
    implied: bool = False

    @property
    def skipped(self) -> bool:
        return self.skip_reason is not None

    @property
    def executed_substeps(self) -> tuple[PlannedSubstep, ...]:
        return tuple(item for item in self.substeps if not item.skipped)

    def substep(self, name: str) -> PlannedSubstep | None:
        return next((item for item in self.substeps if item.name == name), None)


@dataclass(frozen=True, slots=True)
class PipelinePlan:
    """整条流水线的计划（`--dry-run` 与编排层共用）。"""

    entry: str
    start_stage: StageKind
    stages: tuple[StagePlan, ...] = field(default_factory=tuple)

    def stage(self, kind: StageKind) -> StagePlan | None:
        return next((item for item in self.stages if item.stage is kind), None)

    @property
    def executed(self) -> tuple[StagePlan, ...]:
        return tuple(item for item in self.stages if not item.skipped)

    @property
    def skipped(self) -> tuple[StagePlan, ...]:
        return tuple(item for item in self.stages if item.skipped)

    def summary_lines(self) -> tuple[str, ...]:
        """每阶段一行（`--dry-run` 直接打印，也便于测试断言）。"""
        lines: list[str] = []
        for item in self.stages:
            substeps = ",".join(sub.name for sub in item.executed_substeps)
            if item.skipped:
                lines.append(f"{item.stage.value:<10} skipped  reason={item.skip_reason}")
            else:
                flag = " (implied)" if item.implied else ""
                lines.append(f"{item.stage.value:<10} run{flag}  substeps={substeps}")
        return tuple(lines)
