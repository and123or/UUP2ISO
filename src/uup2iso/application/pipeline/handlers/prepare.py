"""`Prepare` 阶段处理器（WP1-T4）：探测 → 物化/解包 → 自检。

编排层只管「跑不跑、跑几次」，本处理器负责把 `Prepare` 的四个子步骤串起来，
并把 `probe.json` 落到 `work\\<task_id>\\input\\probe.json`（输入 §4）。

具体动作（调 wimlib / 7z / 挂载）通过 :class:`PrepareOps` 注入：
* 真实实现依赖引擎层（WS2）；本层保证**顺序、产物、错误分类**正确；
* 单测用假 ops 即可覆盖全部分支，不需要真实镜像。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from uup2iso.application.pipeline.handlers import StageContext
from uup2iso.core.errors import ErrorCode, InputError, StageExecutionError
from uup2iso.core.models.config import TaskConfig
from uup2iso.core.models.enums import EntryPoint
from uup2iso.core.models.pipeline import StageKind
from uup2iso.infrastructure.input.probe import ProbeReport

__all__ = ["PrepareOps", "PrepareOutcome", "PrepareStageHandler"]

_PROBE_RELATIVE = Path("input") / "probe.json"


@runtime_checkable
class PrepareOps(Protocol):
    """`Prepare` 需要的底层动作（由引擎层实现）。"""

    def probe(self, path: Path) -> ProbeReport:
        """识别输入（嗅探 + `wimlib info`）。"""
        ...

    def materialize(self, ctx: StageContext, report: ProbeReport) -> Path | None:
        """ESD → LZW WIM（子步骤 `materialize`）。"""
        ...

    def extract(self, ctx: StageContext, report: ProbeReport) -> Path | None:
        """ISO 解包到 `image\\`（子步骤 `extract`）。"""
        ...

    def selfcheck(self, ctx: StageContext, report: ProbeReport) -> tuple[bool, str]:
        """只读自检：关键文件存在、映像可读。"""
        ...


@dataclass(frozen=True, slots=True)
class PrepareOutcome:
    """`Prepare` 的产物摘要（写检查点与 dry-run 展示用）。"""

    probe_json: Path | None = None
    materialized: Path | None = None
    extracted: Path | None = None
    type: str = ""


class PrepareStageHandler:
    """`StageHandler` 的 `Prepare` 实现。"""

    stage = StageKind.PREPARE

    def __init__(self, ops: PrepareOps | None = None) -> None:
        self._ops = ops
        self.outcome: PrepareOutcome | None = None

    # -- StageHandler -----------------------------------------------------

    async def run(self, ctx: StageContext) -> None:
        """按计划里**未被跳过**的子步骤顺序执行。"""
        plan = ctx.plan
        work_dir: Path = ctx.work_dir
        config: TaskConfig = ctx.config
        executed = {substep.name for substep in plan.executed_substeps}

        source = resolve_input_path(config)
        ops = self._require_ops()

        report: ProbeReport | None = None
        probe_json: Path | None = None

        if "probe" in executed:
            report = ops.probe(source)
            probe_json = write_probe_json(work_dir, report)

        if report is None:
            # 计划里没有 probe 子步骤时，后续动作无输入可用——属实现缺陷，不是用户问题
            raise StageExecutionError(
                "Prepare 缺少 probe 结果：计划裁剪异常",
                code=ErrorCode.INTERNAL_ERROR,
            )

        materialized = None
        if "materialize" in executed:
            materialized = ops.materialize(ctx, report)

        extracted = None
        if "extract" in executed:
            extracted = ops.extract(ctx, report)

        if "selfcheck" in executed:
            ok, detail = ops.selfcheck(ctx, report)
            if not ok:
                raise InputError(
                    f"输入自检未通过：{detail}",
                    code=ErrorCode.INPUT_DAMAGED,
                )

        self.outcome = PrepareOutcome(
            probe_json=probe_json,
            materialized=materialized,
            extracted=extracted,
            type=report.type.value if report else "",
        )

    # -- dry-run 支持 -----------------------------------------------------

    def describe(self, ctx: StageContext) -> tuple[str, ...]:
        """`--dry-run` 用：列出本阶段将执行的命令（不含真实执行）。"""
        config: TaskConfig = ctx.config
        plan = ctx.plan
        executed = {substep.name for substep in plan.executed_substeps}
        source = resolve_input_path(config)
        commands: list[str] = []

        if "probe" in executed:
            commands.append(f"wimlib-imagex info --xml {source}")
        if "materialize" in executed:
            commands.append(f"wimlib-imagex export {source} all image/install.wim --compress=LZX")
        if "extract" in executed:
            commands.append(f"7z x {source} -oimage -y")
        if "selfcheck" in executed:
            commands.append(f"wimlib-imagex info image/install.wim  # 自检 {source}")
        return tuple(commands)

    def reusable_artifacts(self, ctx: StageContext) -> tuple[str, ...]:
        """已存在且与本次一致时可复用产物（`selfcheck` 通过才算数）。"""
        work_dir: Path = ctx.work_dir
        candidates = [_PROBE_RELATIVE.as_posix(), "image/install.wim"]
        return tuple(item for item in candidates if (work_dir / item).is_file())

    # -- 内部 -------------------------------------------------------------

    def _require_ops(self) -> PrepareOps:
        if self._ops is None:
            raise StageExecutionError(
                "Prepare 缺少底层实现（PrepareOps）：引擎层尚未接线（WS2）",
                code=ErrorCode.INTERNAL_ERROR,
            )
        return self._ops


def resolve_input_path(config: TaskConfig) -> Path:
    """从配置取输入路径；入口 A 没有本地路径 → 明确报错。"""
    entry = config.input.mode
    if entry is EntryPoint.IMAGE and config.input.image is not None:
        return Path(config.input.image.path)
    if entry is EntryPoint.UUP_FOLDER and config.input.uup_folder is not None:
        return Path(config.input.uup_folder.path)
    raise InputError(
        "入口 A（在线构建）在 Prepare 阶段没有本地输入路径，需要先 Fetch",
        code=ErrorCode.INPUT_UNREADABLE,
    )


def write_probe_json(work_dir: Path, report: ProbeReport) -> Path:
    """原子写 `input\\probe.json`（只读诊断产物，不参与输出校验）。"""
    target = work_dir / _PROBE_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_probe_json()
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(target)
    return target
