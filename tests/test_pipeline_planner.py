"""StagePlanner 门禁（编排 §2/§2.1/§3、输入 §6、架构 §10）。

验收对应：**阶段名为 8 个固定值**、**三入口映射正确**、**跳过阶段记原因**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uup2iso.application.pipeline import plan_pipeline, stage_index
from uup2iso.core.errors import ConfigError, ErrorCode
from uup2iso.core.models import TaskConfig, default_task_config
from uup2iso.core.models.enums import CompressionKind, EntryPoint, ImageType
from uup2iso.core.models.pipeline import (
    PIPELINE_ORDER,
    SKIP_DRY_RUN,
    SKIP_INPUT_SATISFIED,
    SKIP_MERGED_INTO_FETCH_VERIFY,
    SKIP_NO_OPTIONS,
    SKIP_NOT_ENABLED,
    SKIP_USER_START_STAGE,
    STAGE_SUBSTEPS,
    StageKind,
)

SOURCE = {
    "baseUrl": "https://uupdump.net/",
    "build": "26100.6508",
    "revision": "6508",
    "language": "zh-cn",
    "edition": "professional",
    "channel": "Retail",
}


def make_config(
    mode: EntryPoint,
    *,
    start: str = "Convert",
    image_type: ImageType = ImageType.ISO,
) -> TaskConfig:
    config = default_task_config(mode=mode)
    config.input.start_stage = start
    if mode is EntryPoint.ONLINE:
        config.source = dict(SOURCE)  # type: ignore[assignment]
    if mode is EntryPoint.UUP_FOLDER:
        config.input.uup_folder = {"path": "D:/uup"}  # type: ignore[assignment]
    if mode is EntryPoint.IMAGE:
        config.input.image = {
            "path": "D:/win11.iso",
            "type": image_type.value,
            "editions": [1],
        }  # type: ignore[assignment]
    return config


def reasons(plan: object) -> dict[str, str | None]:
    return {item.stage.value: item.skip_reason for item in plan.stages}  # type: ignore[attr-defined]


# --- 阶段集合本身 -----------------------------------------------------------


def test_exactly_eight_stages_in_fixed_order() -> None:
    """编排 §2.1：8 个阶段名唯一，不允许第 9 个。"""
    assert [kind.value for kind in PIPELINE_ORDER] == [
        "Prepare",
        "Fetch",
        "Convert",
        "Slim",
        "Optimize",
        "Integrate",
        "Unattend",
        "Output",
    ]
    assert len(set(PIPELINE_ORDER)) == 8
    assert all(stage_index(kind) == index for index, kind in enumerate(PIPELINE_ORDER))


def test_plan_always_contains_all_stages() -> None:
    plan = plan_pipeline(make_config(EntryPoint.ONLINE))
    assert [item.stage for item in plan.stages] == list(PIPELINE_ORDER)


def test_historical_names_are_not_stages() -> None:
    """历史名只能当子步骤（编排 §2.1）。"""
    names = {kind.value for kind in StageKind}
    assert names.isdisjoint({"DetectInput", "PrepareImage", "Validate"})

    prepare = {spec.name for spec in STAGE_SUBSTEPS[StageKind.PREPARE]}
    assert {"probe", "materialize", "extract"} <= prepare
    assert "verify" in {spec.name for spec in STAGE_SUBSTEPS[StageKind.OUTPUT]}


# --- 三入口映射 -------------------------------------------------------------


def test_entry_a_runs_fetch_and_convert() -> None:
    """入口 A 默认从 Convert 起步，但 Fetch 是数据前置，必须执行。"""
    plan = plan_pipeline(make_config(EntryPoint.ONLINE))
    table = reasons(plan)

    assert table["Fetch"] is None
    assert table["Convert"] is None
    assert table["Prepare"] is None
    prepare = plan.stage(StageKind.PREPARE)
    assert prepare is not None and prepare.implied is True
    assert [sub.name for sub in prepare.executed_substeps] == ["probe"]
    assert prepare.substep("selfcheck").skip_reason == SKIP_MERGED_INTO_FETCH_VERIFY


def test_entry_b_runs_probe_and_selfcheck() -> None:
    plan = plan_pipeline(make_config(EntryPoint.UUP_FOLDER))
    prepare = plan.stage(StageKind.PREPARE)

    assert prepare is not None
    assert [sub.name for sub in prepare.executed_substeps] == ["probe", "selfcheck"]
    assert reasons(plan)["Fetch"] is None
    assert reasons(plan)["Convert"] is None


def test_entry_c_skips_fetch_and_convert_as_input_satisfied() -> None:
    plan = plan_pipeline(make_config(EntryPoint.IMAGE))
    table = reasons(plan)

    assert table["Fetch"] == SKIP_INPUT_SATISFIED
    assert table["Convert"] == SKIP_INPUT_SATISFIED
    prepare = plan.stage(StageKind.PREPARE)
    assert prepare is not None
    assert [sub.name for sub in prepare.executed_substeps] == ["probe", "extract", "selfcheck"]


def test_entry_c_esd_materializes_instead_of_extracting() -> None:
    plan = plan_pipeline(make_config(EntryPoint.IMAGE, image_type=ImageType.ESD))
    prepare = plan.stage(StageKind.PREPARE)

    assert prepare is not None
    assert [sub.name for sub in prepare.executed_substeps] == ["probe", "materialize", "selfcheck"]
    assert prepare.substep("extract").skip_reason == SKIP_INPUT_SATISFIED


def test_entry_c_wim_is_referenced_directly() -> None:
    plan = plan_pipeline(make_config(EntryPoint.IMAGE, image_type=ImageType.WIM))
    prepare = plan.stage(StageKind.PREPARE)

    assert prepare is not None
    assert [sub.name for sub in prepare.executed_substeps] == ["probe", "selfcheck"]


# --- 起始阶段 ---------------------------------------------------------------


def test_start_stage_beyond_convert_skips_data_stages() -> None:
    """A/B 显式从 Slim 起步 → Fetch/Convert 记 user_start_stage（配置 §3.2）。"""
    plan = plan_pipeline(make_config(EntryPoint.ONLINE, start="Slim"))
    table = reasons(plan)

    assert table["Fetch"] == SKIP_USER_START_STAGE
    assert table["Convert"] == SKIP_USER_START_STAGE
    assert plan.stage(StageKind.PREPARE).implied is True  # type: ignore[union-attr]


def test_start_output_skips_all_business_stages() -> None:
    plan = plan_pipeline(make_config(EntryPoint.IMAGE, start="Output"))
    table = reasons(plan)

    assert table["Slim"] == SKIP_USER_START_STAGE
    assert table["Unattend"] == SKIP_USER_START_STAGE
    assert table["Output"] is None
    assert plan.stage(StageKind.PREPARE).skipped is False  # type: ignore[union-attr]


def test_prepare_is_never_skipped() -> None:
    for mode in EntryPoint:
        for start in ("Convert", "Slim", "Output"):
            plan = plan_pipeline(make_config(mode, start=start))
            prepare = plan.stage(StageKind.PREPARE)
            assert prepare is not None and prepare.skipped is False, f"{mode}/{start}"


def test_stage_too_early_raises() -> None:
    """入口 B 最早 Convert：选 Prepare 应报 STAGE_TOO_EARLY（输入 §6）。"""
    config = make_config(EntryPoint.UUP_FOLDER, start="Prepare")
    with pytest.raises(ConfigError) as caught:
        plan_pipeline(config)
    assert caught.value.code is ErrorCode.STAGE_TOO_EARLY


def test_unknown_stage_name_raises() -> None:
    config = make_config(EntryPoint.ONLINE)
    config.input.start_stage = "Validate"
    with pytest.raises(ConfigError) as caught:
        plan_pipeline(config)
    assert caught.value.code is ErrorCode.CONFIG_SEMANTIC_INVALID


# --- 选项裁剪 ---------------------------------------------------------------


def test_option_stages_run_when_configured(tmp_path: Path) -> None:
    config = make_config(EntryPoint.IMAGE, start="Slim")
    config.slim = {"ruleIds": ["FR4-SL-A01"]}  # type: ignore[assignment]
    config.optimize = {"ruleIds": ["FR5-OP-T60"]}  # type: ignore[assignment]
    config.integrate = {"updates": {"paths": ["D:/Updates"]}}  # type: ignore[assignment]
    config.unattend = {"enabled": True}  # type: ignore[assignment]

    table = reasons(plan_pipeline(config))
    assert table["Slim"] is None
    assert table["Optimize"] is None
    assert table["Integrate"] is None
    assert table["Unattend"] is None


@pytest.mark.parametrize(
    ("stage", "reason"),
    [
        (StageKind.SLIM, SKIP_NO_OPTIONS),
        (StageKind.OPTIMIZE, SKIP_NO_OPTIONS),
        (StageKind.INTEGRATE, SKIP_NO_OPTIONS),
        (StageKind.UNATTEND, SKIP_NOT_ENABLED),
    ],
)
def test_option_stages_skip_with_reason(stage: StageKind, reason: str) -> None:
    plan = plan_pipeline(make_config(EntryPoint.IMAGE))
    table = reasons(plan)
    assert table[stage.value] == reason


def test_empty_option_sections_still_skip() -> None:
    """只给了空壳（没有 paths/ruleIds）等于没配。"""
    config = make_config(EntryPoint.IMAGE)
    config.integrate = {"updates": {"paths": []}}  # type: ignore[assignment]
    config.unattend = {"enabled": False}  # type: ignore[assignment]

    table = reasons(plan_pipeline(config))
    assert table["Integrate"] == SKIP_NO_OPTIONS
    assert table["Unattend"] == SKIP_NOT_ENABLED


def test_dry_run_skips_output() -> None:
    config = make_config(EntryPoint.IMAGE)
    config.execution = {"dryRun": True}  # type: ignore[assignment]
    table = reasons(plan_pipeline(config))
    assert table["Output"] == SKIP_DRY_RUN


# --- 子步骤幂等标注 ---------------------------------------------------------


def test_mount_substeps_are_marked_non_idempotent() -> None:
    """编排 §3：挂载类非幂等，重跑前必须清理残留。"""
    config = make_config(EntryPoint.IMAGE, start="Slim")
    config.slim = {"ruleIds": ["FR4-SL-A01"]}  # type: ignore[assignment]
    plan = plan_pipeline(config)

    slim = plan.stage(StageKind.SLIM)
    assert slim is not None
    mount = slim.substep("mount")
    unmount = slim.substep("unmount")
    assert mount is not None and mount.idempotent is False
    assert mount.cleanup_before_rerun is True
    assert unmount is not None and unmount.idempotent is False
    assert slim.substep("apply").idempotent is True  # type: ignore[union-attr]


def test_aggregate_and_commit_are_idempotent() -> None:
    config = make_config(EntryPoint.ONLINE)
    plan = plan_pipeline(config)
    convert = plan.stage(StageKind.CONVERT)
    output = plan.stage(StageKind.OUTPUT)

    assert convert is not None and convert.substep("aggregate").idempotent is True  # type: ignore[union-attr]
    assert output is not None and output.substep("commit").idempotent is True  # type: ignore[union-attr]


def test_skipped_stage_marks_all_substeps_with_reason() -> None:
    plan = plan_pipeline(make_config(EntryPoint.IMAGE))
    fetch = plan.stage(StageKind.FETCH)

    assert fetch is not None and fetch.skipped
    assert all(sub.skip_reason == SKIP_INPUT_SATISFIED for sub in fetch.substeps)
    assert fetch.executed_substeps == ()


# --- 摘要（--dry-run 的雏形） ------------------------------------------------


def test_summary_lines_are_readable() -> None:
    plan = plan_pipeline(make_config(EntryPoint.IMAGE, start="Slim"))
    lines = plan.summary_lines()

    assert len(lines) == 8
    assert lines[0].startswith("Prepare") and "run" in lines[0]
    assert any("skipped  reason=no_options" in line for line in lines)
    joined = "\n".join(lines)
    assert "substeps=probe,extract,selfcheck" in joined


def test_plan_is_deterministic() -> None:
    config = make_config(EntryPoint.ONLINE)
    first = plan_pipeline(config).summary_lines()
    second = plan_pipeline(config).summary_lines()
    assert first == second


def test_compression_choice_does_not_affect_plan() -> None:
    """压缩类型是 Convert 内部选项，不该改变阶段裁剪。"""
    left = make_config(EntryPoint.ONLINE)
    right = make_config(EntryPoint.ONLINE)
    right.convert = {"compression": CompressionKind.XPRESS.value}  # type: ignore[assignment]

    assert left is not right
    assert plan_pipeline(left).summary_lines() == plan_pipeline(right).summary_lines()
