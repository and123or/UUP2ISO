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
from uup2iso.core.models.version import VersionResolution
from uup2iso.infrastructure.input.probe import ProbeReport
from uup2iso.infrastructure.input.sniff import uup_folder_evidence

__all__ = ["BuiltinPrepareOps", "PrepareOps", "PrepareOutcome", "PrepareStageHandler"]

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
    version: VersionResolution | None = None
    """`VersionResolution`（P0-0）：构建号/版本族/语言判定结果。"""


class PrepareStageHandler:
    """`StageHandler` 的 `Prepare` 实现。"""

    stage = StageKind.PREPARE

    def __init__(self, ops: PrepareOps | None = None) -> None:
        # 未注入时用内置实现：识别与自检不依赖引擎层（只有解包/物化才需要）
        self._ops: PrepareOps | None = ops or BuiltinPrepareOps()
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

        version = self._resolve_version(source)

        if "probe" in executed:
            report = ops.probe(source)
            probe_json = write_probe_json(
                work_dir,
                report,
                extra={"version": version.to_dict()} if version is not None else None,
            )

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
            version=version,
        )
        if version is not None:
            ctx.extras["version"] = version

    # -- dry-run 支持 -----------------------------------------------------

    def describe(self, ctx: StageContext) -> tuple[str, ...]:
        """`--dry-run` 用：列出本阶段将执行的命令（不含真实执行）。"""
        config: TaskConfig = ctx.config
        plan = ctx.plan
        executed = {substep.name for substep in plan.executed_substeps}
        source = resolve_input_path(config)

        # 入口 B 没有单一映像可查：只做目录结构自检（不发外部命令）
        if config.input.mode is EntryPoint.UUP_FOLDER:
            evidence = uup_folder_evidence(source)
            return (
                f"# 结构自检 {source}：{evidence.files_count} 个文件 / "
                f"{evidence.esd_count} 个 .esd / {evidence.cab_count} 个 .cab"
                f"{'（无清单）' if not evidence.manifest_present else ''}",
                f"# 写入 {ctx.work_dir / 'input' / 'probe.json'}",
            )

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

    def file_operations(self, ctx: StageContext) -> tuple[object, ...]:
        """`--dry-run` 用：本阶段会读/写哪些文件（只描述，不执行）。"""
        from uup2iso.application.pipeline.orchestrator import FileOp

        config: TaskConfig = ctx.config
        plan = ctx.plan
        executed = {substep.name for substep in plan.executed_substeps}
        source = resolve_input_path(config)
        work = str(ctx.work_dir)

        ops: list[FileOp] = [FileOp("read", str(source), "输入只读，永不修改")]
        if "materialize" in executed:
            ops.append(FileOp("create", f"{work}\\image\\install.wim", "ESD 物化为 LZX WIM"))
        if "extract" in executed:
            ops.append(FileOp("create", f"{work}\\image\\", "ISO 解包目录"))
        if "probe" in executed:
            ops.append(FileOp("create", f"{work}\\input\\probe.json", "探测结果（只读诊断）"))
        return tuple(ops)

    def reusable_artifacts(self, ctx: StageContext) -> tuple[str, ...]:
        """已存在且与本次一致时可复用产物（`selfcheck` 通过才算数）。"""
        work_dir: Path = ctx.work_dir
        candidates = [_PROBE_RELATIVE.as_posix(), "image/install.wim"]
        return tuple(item for item in candidates if (work_dir / item).is_file())

    # -- 内部 -------------------------------------------------------------

    def _resolve_version(self, source: Path) -> VersionResolution | None:
        """推断构建号与版本族（P0-0）；失败不影响主流程，只记告警。"""
        try:
            from uup2iso.application.input import resolve_input_version
            from uup2iso.infrastructure.version import VersionCatalog

            names: tuple[str, ...] = ()
            if source.is_dir():
                names = tuple(item.name for item in source.iterdir() if item.is_file())
            return resolve_input_version(path=source, catalog=VersionCatalog(), names=names)
        except (OSError, ValueError):
            return None

    def _require_ops(self) -> PrepareOps:
        if self._ops is None:
            raise StageExecutionError(
                "Prepare 缺少底层实现（PrepareOps）：引擎层尚未接线（WS2）",
                code=ErrorCode.INTERNAL_ERROR,
            )
        return self._ops


class BuiltinPrepareOps:
    """不依赖引擎层的 `Prepare` 动作：识别 + 自检。

    * `probe` —— 复用 `InputProber`（内容嗅探 + 可选 wimlib）；
    * `selfcheck` —— 按入口换判据（UUP 目录看组件完整度，映像看文件可读）；
    * `materialize` / `extract` —— 需要 wimlib/7z 真实动作，属引擎层，**明确报未接线**
      （不静默跳过，否则用户会以为已经解包完成）。
    """

    def probe(self, path: Path) -> ProbeReport:
        from uup2iso.infrastructure.input import InputProber

        return InputProber().probe(path)

    def materialize(self, ctx: StageContext, report: ProbeReport) -> Path | None:
        raise StageExecutionError(
            "ESD→WIM 物化需要 wimlib 引擎层（尚未接线）", code=ErrorCode.INTERNAL_ERROR
        )

    def extract(self, ctx: StageContext, report: ProbeReport) -> Path | None:
        raise StageExecutionError(
            "ISO 解包需要 7-Zip 引擎层（尚未接线）", code=ErrorCode.INTERNAL_ERROR
        )

    def selfcheck(self, ctx: StageContext, report: ProbeReport | None) -> tuple[bool, str]:
        config = ctx.config
        entry = config.input.mode

        if entry is EntryPoint.UUP_FOLDER:
            return _selfcheck_uup(config)
        if entry is EntryPoint.IMAGE:
            return _selfcheck_image(config, report)
        # 入口 A：素材由 Fetch 产出，Prepare 阶段只做轻量确认
        return True, "入口 A：素材由 Fetch 产出，Prepare 无需自检"


def _selfcheck_uup(config: TaskConfig) -> tuple[bool, str]:
    path = Path(config.input.uup_folder.path) if config.input.uup_folder else Path()
    if not path.is_dir():
        return False, f"UUP 目录不存在：{path}"

    evidence = uup_folder_evidence(path)
    if evidence.esd_count == 0:
        return False, f"未找到任何 .esd 组件：{path}"
    if evidence.partial_count:
        return False, f"存在 {evidence.partial_count} 个未完成下载残留（.part/.aria2），请重新下载"
    if evidence.files_count == 0:
        return False, f"目录为空：{path}"

    detail = (
        f"{evidence.files_count} 个文件（{evidence.esd_count} 个 .esd / "
        f"{evidence.cab_count} 个 .cab / {evidence.appx_count} 个 appx，"
        f"{evidence.total_bytes / 1e9:.2f} GB）"
    )
    if not evidence.manifest_present:
        detail += "；无清单文件，按结构特征识别"
    if not evidence.has_aggregate_metadata:
        detail += "；缺 AggregatedMetadata.cab（转换器可能仍可运行）"
    return True, detail


def _selfcheck_image(config: TaskConfig, report: ProbeReport | None) -> tuple[bool, str]:
    if config.input.image is None:
        return False, "缺少 image 配置"
    path = Path(config.input.image.path)
    if not path.is_file():
        return False, f"输入文件不存在：{path}"
    if report is not None and report.type.value == "unknown":
        return False, "无法识别输入类型"
    return True, f"{path.name} 可读"


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


def write_probe_json(
    work_dir: Path, report: ProbeReport, *, extra: dict[str, object] | None = None
) -> Path:
    """原子写 `input\\probe.json`（只读诊断产物，不参与输出校验）。"""
    target = work_dir / _PROBE_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_probe_json()
    if extra:
        payload.update(extra)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(target)
    return target
