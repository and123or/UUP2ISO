"""StagePlanner：把 `TaskConfig` 裁剪成一条确定的阶段计划（编排 §2、输入 §6）。

三条规则决定了整个裁剪逻辑：

1. **8 个阶段始终都在计划里**，只是有的被标 `skipped`（附原因）——UI 阶段串因此稳定；
2. `startStage` 只影响 `Fetch` / `Convert` 这些**前置业务阶段**，`Prepare` 永远执行（可能被标 `implied`）；
3. 各阶段自有的「无选项」判定（未选精简项/优化项/集成项/无人值守）走 `reason=no_options`。

计划是纯函数产物：同样的 `TaskConfig` 必得同样的计划，便于 `--dry-run` 与测试。
"""

from __future__ import annotations

from uup2iso.core.errors import ConfigError, ErrorCode
from uup2iso.core.models.config import TaskConfig
from uup2iso.core.models.enums import EntryPoint, ImageType
from uup2iso.core.models.pipeline import (
    PIPELINE_ORDER,
    SKIP_DRY_RUN,
    SKIP_INPUT_SATISFIED,
    SKIP_MERGED_INTO_FETCH_VERIFY,
    SKIP_NO_OPTIONS,
    SKIP_NOT_ENABLED,
    SKIP_USER_START_STAGE,
    STAGE_SUBSTEPS,
    PipelinePlan,
    PlannedSubstep,
    StageKind,
    StagePlan,
    SubstepSpec,
)

__all__ = ["plan_pipeline", "stage_index"]

_STAGE_INDEX: dict[StageKind, int] = {kind: index for index, kind in enumerate(PIPELINE_ORDER)}

_EARLIEST_STAGE: dict[EntryPoint, StageKind] = {
    EntryPoint.ONLINE: StageKind.CONVERT,
    EntryPoint.UUP_FOLDER: StageKind.CONVERT,
    EntryPoint.IMAGE: StageKind.PREPARE,
}
"""各入口**可选**的最早阶段（输入 §6；与配置 V2 的判定保持一致）。"""


def stage_index(kind: StageKind) -> int:
    """阶段在固定顺序中的位置（0..7）。"""
    return _STAGE_INDEX[kind]


def plan_pipeline(config: TaskConfig) -> PipelinePlan:
    """按入口与选项裁剪阶段计划。

    Args:
        config: 已通过 V1/V2 校验的任务配置。

    Returns:
        :class:`PipelinePlan`；8 个阶段齐全，跳过的带 `reason`。

    Raises:
        ConfigError: 起始阶段非法或早于该输入的最早阶段（``STAGE_TOO_EARLY``）。
    """
    start = _parse_start(config.input.start_stage)
    earliest = _EARLIEST_STAGE[config.entry]
    if _STAGE_INDEX[start] < _STAGE_INDEX[earliest]:
        raise ConfigError(
            f"该输入无法从 {start.value} 阶段开始，最早为 {earliest.value}",
            code=ErrorCode.STAGE_TOO_EARLY,
        )

    stages = tuple(
        _plan_stage(stage, config, start_index=_STAGE_INDEX[start]) for stage in PIPELINE_ORDER
    )
    return PipelinePlan(entry=config.entry.value, start_stage=start, stages=stages)


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _parse_start(raw: str) -> StageKind:
    try:
        return StageKind(raw)
    except ValueError as exc:
        raise ConfigError(
            f"未知起始阶段：{raw}",
            code=ErrorCode.CONFIG_SEMANTIC_INVALID,
        ) from exc


def _plan_stage(stage: StageKind, config: TaskConfig, *, start_index: int) -> StagePlan:
    if stage is StageKind.PREPARE:
        # Prepare 永不整段跳过；用户在更后阶段起步时标 implied（编排 §2、输入 §6）
        substeps = _prepare_substeps(config)
        return StagePlan(
            stage=stage,
            substeps=substeps,
            skip_reason=None,
            implied=start_index > 0,
        )

    reason = _skip_reason(stage, config, start_index)
    specs = STAGE_SUBSTEPS[stage]
    substeps = _stub_substeps(specs, reason) if reason else _stub_substeps(specs, None)
    return StagePlan(stage=stage, substeps=substeps, skip_reason=reason)


def _skip_reason(stage: StageKind, config: TaskConfig, start_index: int) -> str | None:
    """判定跳过原因。

    顺序有讲究：**入口自身决定的跳过优先于「起始阶段」**。
    入口 C 的 `Fetch`/`Convert` 是「根本没这回事」（`input_satisfied`），
    而入口 A/B 才是「用户从更后面的阶段起步」（`user_start_stage`）。
    """
    if stage in (StageKind.FETCH, StageKind.CONVERT):
        # 入口 C：根本没有「下载/组件聚合」这回事
        if config.entry is EntryPoint.IMAGE:
            return SKIP_INPUT_SATISFIED
        # 入口 A/B：只有用户显式声明「从 Convert 之后起步」才跳过数据前置；
        # startStage=Convert 时 Fetch 仍需执行——否则入口 A 根本没有素材可用。
        return SKIP_USER_START_STAGE if start_index > _STAGE_INDEX[StageKind.CONVERT] else None

    if _STAGE_INDEX[stage] < start_index:
        return SKIP_USER_START_STAGE

    if stage is StageKind.SLIM:
        return None if _has_slim(config) else SKIP_NO_OPTIONS

    if stage is StageKind.OPTIMIZE:
        return None if _has_optimize(config) else SKIP_NO_OPTIONS

    if stage is StageKind.INTEGRATE:
        return None if _has_integrate(config) else SKIP_NO_OPTIONS

    if stage is StageKind.UNATTEND:
        return None if _has_unattend(config) else SKIP_NOT_ENABLED

    if stage is StageKind.OUTPUT:
        dry_run = bool(config.execution and config.execution.dry_run)
        return SKIP_DRY_RUN if dry_run else None

    return None


def _stub_substeps(
    specs: tuple[SubstepSpec, ...], stage_reason: str | None
) -> tuple[PlannedSubstep, ...]:
    return tuple(
        PlannedSubstep(
            name=spec.name,
            idempotent=spec.idempotent,
            cleanup_before_rerun=spec.cleanup_before_rerun,
            skip_reason=stage_reason,
        )
        for spec in specs
    )


def _prepare_substeps(config: TaskConfig) -> tuple[PlannedSubstep, ...]:
    """按入口裁剪 `Prepare` 的子步骤（编排 §2 P0、输入 §6、§7）。"""
    executed: set[str]
    reasons: dict[str, str] = {}

    if config.entry is EntryPoint.ONLINE:
        # 入口 A：只做 uup\ 就绪探测；selfcheck 并入 Fetch.verify
        executed = {"probe"}
        reasons["materialize"] = SKIP_INPUT_SATISFIED
        reasons["extract"] = SKIP_INPUT_SATISFIED
        reasons["selfcheck"] = SKIP_MERGED_INTO_FETCH_VERIFY
    elif config.entry is EntryPoint.UUP_FOLDER:
        executed = {"probe", "selfcheck"}
        reasons["materialize"] = SKIP_INPUT_SATISFIED
        reasons["extract"] = SKIP_INPUT_SATISFIED
    else:
        image_type = config.input.image.type if config.input.image else ImageType.UNKNOWN
        if image_type is ImageType.ISO:
            executed = {"probe", "extract", "selfcheck"}
            reasons["materialize"] = SKIP_INPUT_SATISFIED
        elif image_type is ImageType.ESD:
            executed = {"probe", "materialize", "selfcheck"}
            reasons["extract"] = SKIP_INPUT_SATISFIED
        else:  # WIM / SWM / unknown：直接引用，不复制不物化
            executed = {"probe", "selfcheck"}
            reasons["materialize"] = SKIP_INPUT_SATISFIED
            reasons["extract"] = SKIP_INPUT_SATISFIED

    return tuple(
        PlannedSubstep(
            name=spec.name,
            idempotent=spec.idempotent,
            cleanup_before_rerun=spec.cleanup_before_rerun,
            skip_reason=None
            if spec.name in executed
            else reasons.get(spec.name, SKIP_INPUT_SATISFIED),
        )
        for spec in STAGE_SUBSTEPS[StageKind.PREPARE]
    )


def _has_slim(config: TaskConfig) -> bool:
    return bool(config.slim and config.slim.rule_ids)


def _has_optimize(config: TaskConfig) -> bool:
    return bool(config.optimize and config.optimize.rule_ids)


def _has_integrate(config: TaskConfig) -> bool:
    section = config.integrate
    if section is None:
        return False
    return any(
        (
            section.updates.paths,
            section.drivers.paths,
            section.language.add,
            section.language.fod,
            section.registry.imports,
        )
    )


def _has_unattend(config: TaskConfig) -> bool:
    return bool(config.unattend and config.unattend.enabled)
