"""TaskConfig 契约模型（配置 §3，唯一业务契约）。

**这是 GUI 保存、CLI 输入、编排消费的同一份对象**——任何业务选项都必须在这里有字段，
不允许出现「界面能选、配置里没有」的选项（配置 §3.3 对 CV-01…CV-29 的硬要求）。

设计取舍（决策见 CHANGELOG 0.1.13）：

* 数值**范围**不写成 ``Field(ge=..)``，而是放到 :meth:`TaskConfig.check_semantics`：
  配置 §5 规定「超出范围即 `CONFIG_SEMANTIC_INVALID`」，而 pydantic 的约束错误会落成
  Schema 错误（`CONFIG_SCHEMA_INVALID`），错误码对不上文档。
* 未知字段一律 ``extra="allow"``：默认忽略并收集路径（§7），严格模式由调用方判断。
* 路径不做存在性校验（§8.1 明确留到阶段前），也不展开环境变量（§6）。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from uup2iso.core.models.enums import CompressionKind, EntryPoint, ImageType, OutputType

__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "SUPPORTED_MAJOR",
    "AppSection",
    "AppsSection",
    "BootSection",
    "CleanupSection",
    "ConfigIssue",
    "ConvertSection",
    "DriversSection",
    "EsdDecryptSection",
    "ExecutionSection",
    "FeaturesSection",
    "FetchSection",
    "FingerprintContext",
    "InputImageRef",
    "InputSection",
    "InputUupFolder",
    "IntegrateDrivers",
    "IntegrateLanguage",
    "IntegrateRegistry",
    "IntegrateSection",
    "IntegrateUpdates",
    "MetaSection",
    "OptimizeSection",
    "OutputSection",
    "PresetOrigin",
    "SEMANTIC_INVALID",
    "SidecarSection",
    "SlimSection",
    "SourceSection",
    "TaskConfig",
    "UnattendSection",
    "UpdatesSection",
    "ValidationSection",
    "VirtualEditionSection",
    "WinreSection",
    "iter_unknown_fields",
]

CONFIG_SCHEMA_VERSION = "1.0"
"""当前配置格式版本（配置 §3.1）。次版本变化=只加可选字段；主版本变化必须迁移（§9）。"""

SUPPORTED_MAJOR = 1
"""本程序支持的配置主版本。"""

SEMANTIC_INVALID = "CONFIG_SEMANTIC_INVALID"
"""语义校验失败码（配置 §8.1 V2）。"""

_STAGE_ORDER = (
    "Prepare",
    "Fetch",
    "Convert",
    "Slim",
    "Optimize",
    "Integrate",
    "Unattend",
    "Output",
)
"""8 阶段固定顺序（编排 §2），用于「起始阶段不能早于输入支持的最早阶段」。"""

_SELECTABLE_STAGES: tuple[str, ...] = tuple(stage for stage in _STAGE_ORDER if stage != "Fetch")
"""可选起始阶段（配置 §3.2 的枚举，**不含 Fetch**——它是内部下载阶段）。"""

_EARLIEST_STAGE_BY_MODE = {
    # 入口 A：Fetch 不是可选起始阶段（配置 §3.2 的枚举不含它），用户可选的最早业务阶段是 Convert
    EntryPoint.ONLINE: "Convert",
    EntryPoint.UUP_FOLDER: "Convert",
    EntryPoint.IMAGE: "Prepare",
}
"""各入口**可选**的最早起始阶段（输入 §6 + 配置 §3.2 的枚举范围）。

注意 `Prepare` 是任何输入的隐含前置，永不因起始阶段被跳过（编排 §3）；
这里约束的是「用户能选到多早」，不是「实际从哪跑」。
"""


@dataclass(frozen=True, slots=True)
class FingerprintContext:
    """指纹计算时可排除的字段（配置 §8.2：``meta`` / 路径 / 代理）。

    目前排除项固定，保留此对象是为了将来按需扩展（如排除时间戳）。
    """

    exclude_meta: bool = True
    exclude_paths: bool = True
    exclude_proxy: bool = True


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    """一条语义问题（V2）。

    Attributes:
        field: 出错字段路径，如 ``convert.cleanup.resetBase``。
        code: 稳定错误码，目前统一为 ``CONFIG_SEMANTIC_INVALID``。
        message: 面向用户的中文说明。
    """

    field: str
    code: str
    message: str


class _Section(BaseModel):
    """所有配置段的基类：允许未知字段（§7 默认忽略并收集）。"""

    model_config = ConfigDict(
        extra="allow",
        validate_assignment=True,
        populate_by_name=True,
    )


# ---------------------------------------------------------------------------
# meta / input / source / fetch
# ---------------------------------------------------------------------------


class MetaSection(_Section):
    """任务元信息（配置 §3.1）。``id`` 为 8 位十六进制短 id，创建后终身不变。"""

    id: str = ""
    name: str = ""
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    app_version: str = Field(default="", alias="appVersion")
    notes: str = ""


class InputImageRef(_Section):
    """入口 C 的输入映像（配置 §3.2）。"""

    path: str = ""
    type: ImageType = ImageType.ISO
    editions: list[int] = Field(default_factory=list)
    """``probe.json`` 的索引号数组（不是 edition 名称）。"""


class InputUupFolder(_Section):
    """入口 B 的本地 UUP 文件夹。"""

    path: str = ""


class InputSection(_Section):
    """输入与起始阶段（配置 §3.2）。"""

    mode: EntryPoint = EntryPoint.ONLINE
    start_stage: str = Field(default="Convert", alias="startStage")
    image: InputImageRef | None = None
    uup_folder: InputUupFolder | None = Field(default=None, alias="uupFolder")


class SourceSection(_Section):
    """入口 A 的数据源选择（配置 §3.1：入口 A 必填）。"""

    base_url: str = Field(default="", alias="baseUrl")
    build: str = ""
    revision: str = ""
    language: str = ""
    edition: str = ""
    channel: str = ""


class FetchSection(_Section):
    """下载参数（配置 §3.1）。``split`` 与 ``timeout`` 的范围属语义校验。"""

    split: int = 8
    max_overall_download_limit: str = Field(default="", alias="maxOverallDownloadLimit")
    proxy: str = ""
    timeout_seconds: int = Field(default=30, alias="timeoutSeconds")


# ---------------------------------------------------------------------------
# convert（FR3 全部选项）
# ---------------------------------------------------------------------------


class UpdatesSection(_Section):
    """CV-02：更新集成方式。"""

    integrate: bool = True
    mode: Literal["in_image", "external"] = "in_image"


class CleanupSection(_Section):
    """CV-03 / CV-04：组件清理与 ResetBase（后者依赖前者）。"""

    component_cleanup: bool = Field(default=True, alias="componentCleanup")
    reset_base: bool = Field(default=False, alias="resetBase")


class FeaturesSection(_Section):
    """CV-05：功能启用。"""

    netfx3: bool = False


class WinreSection(_Section):
    """CV-10 / CV-11：WinRE 处理。"""

    skip: bool = False
    update: bool = False


class BootSection(_Section):
    """CV-14：引导文件刷新（保守模式下禁用）。"""

    update_boot_files: bool = Field(default=False, alias="updateBootFiles")


class DriversSection(_Section):
    """CV-20 / CV-21：转换阶段交给转换器的驱动处理。

    与 ``integrate.drivers`` 职责不同：两者都配时以 ``integrate`` 为准并记
    ``WARN convert_driver_ignored``（配置 §3.3）。
    """

    enabled: bool = False
    source_dir: str = Field(default="", alias="sourceDir")
    scope: Literal["all", "os", "winpe"] = "all"


class AppsSection(_Section):
    """CV-22…CV-25：转换器自带的 app 处理。"""

    skip: bool = False
    level: int = 0
    stub_full: bool = Field(default=False, alias="stubFull")
    custom_list: str = Field(default="", alias="customList")


class VirtualEditionSection(_Section):
    """CV-06 / CV-P1…P8：实验性，MVP 不开放（``enabled=true`` 即语义失败）。"""

    enabled: bool = False
    editions: list[str] = Field(default_factory=list)


class EsdDecryptSection(_Section):
    """CV-29：实验性 ESD 解密，MVP 不开放。"""

    enabled: bool = False


class ConvertSection(_Section):
    """转换选项（配置 §3.3）。"""

    mode: Literal["wim_only", "full"] = "wim_only"
    compression: CompressionKind = CompressionKind.LZX
    editions: list[str] = Field(default_factory=list)
    keep_intermediate: bool = Field(default=False, alias="keepIntermediate")
    updates: UpdatesSection = Field(default_factory=UpdatesSection)
    cleanup: CleanupSection = Field(default_factory=CleanupSection)
    features: FeaturesSection = Field(default_factory=FeaturesSection)
    winre: WinreSection = Field(default_factory=WinreSection)
    boot: BootSection = Field(default_factory=BootSection)
    drivers: DriversSection = Field(default_factory=DriversSection)
    apps: AppsSection = Field(default_factory=AppsSection)
    volume_label: str = Field(default="", alias="volumeLabel")
    virtual_edition: VirtualEditionSection = Field(
        default_factory=VirtualEditionSection, alias="virtualEdition"
    )
    esd_decrypt: EsdDecryptSection = Field(default_factory=EsdDecryptSection, alias="esdDecrypt")


# ---------------------------------------------------------------------------
# 预设快照（配置 §4.2）
# ---------------------------------------------------------------------------


class PresetOrigin(_Section):
    """预设来源，仅用于追溯与「是否有更新」提示（不自动更新）。"""

    preset_id: str = Field(default="", alias="presetId")
    preset_version: str = Field(default="", alias="presetVersion")
    snapshotted_at: datetime | None = Field(default=None, alias="snapshottedAt")


class SlimSection(_Section):
    """精简预设的内联快照：``origin`` + ``ruleIds`` + ``params``（配置 §4.2）。"""

    origin: PresetOrigin = Field(default_factory=PresetOrigin)
    rule_ids: list[str] = Field(default_factory=list, alias="ruleIds")
    params: dict[str, dict[str, Any]] = Field(default_factory=dict)


class OptimizeSection(SlimSection):
    """优化预设的内联快照，形态与精简一致。"""


# ---------------------------------------------------------------------------
# integrate / unattend 片段（配置类预设，直接展开）
# ---------------------------------------------------------------------------


class IntegrateUpdates(_Section):
    """集成 §3：离线更新。"""

    paths: list[str] = Field(default_factory=list)
    recursive: bool = True
    order: Literal["auto", "name", "mtime"] = "auto"


class IntegrateDrivers(_Section):
    """集成 §4：驱动。"""

    paths: list[str] = Field(default_factory=list)
    recursive: bool = True
    require_signature: bool = Field(default=True, alias="requireSignature")
    conflict: Literal["newerVersion", "firstWins", "ask"] = "newerVersion"


class IntegrateLanguage(_Section):
    """集成 §5：语言包 / LIP / FOD。"""

    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)
    fod: list[str] = Field(default_factory=list)


class IntegrateRegistry(_Section):
    """集成 §6：注册表导入。"""

    imports: list[str] = Field(default_factory=list)
    strict: bool = False


class IntegrateSection(_Section):
    """集成阶段配置（可作为预设片段深拷贝，配置 §4.1）。"""

    updates: IntegrateUpdates = Field(default_factory=IntegrateUpdates)
    drivers: IntegrateDrivers = Field(default_factory=IntegrateDrivers)
    language: IntegrateLanguage = Field(default_factory=IntegrateLanguage)
    registry: IntegrateRegistry = Field(default_factory=IntegrateRegistry)


class AppSection(_Section):
    """无人值守 §4 的应用列表项。"""

    path: str = ""
    args: str = ""


class UnattendSection(_Section):
    """无人值守配置片段（无人值守 §4）。

    具体字段在 WP7-T2 落地；这里保留 ``computerName`` 等核心项与「未知字段放行」，
    使预设互导不会因为新字段而失败。
    """

    enabled: bool = False
    computer_name: str = Field(default="", alias="computerName")
    product_key: str = Field(default="", alias="productKey")
    time_zone: str = Field(default="", alias="timeZone")
    accept_eula: bool = Field(default=True, alias="acceptEula")
    skip_oobe_privacy: bool = Field(default=True, alias="skipOobePrivacy")
    apps: list[AppSection] = Field(default_factory=list)
    scripts: list[str] = Field(default_factory=list)
    inject_into_wim: bool = Field(default=False, alias="injectIntoWim")


# ---------------------------------------------------------------------------
# output / execution
# ---------------------------------------------------------------------------


class SidecarSection(_Section):
    """伴生文件开关（输出 §8）。"""

    sha256: bool = True
    readme: bool = True
    manifest: bool = False


class OutputSection(_Section):
    """输出规则（配置 §3.4）。"""

    dir: str = ""
    name_template: str = Field(default="{edition}_{build}_{arch}_{lang}", alias="nameTemplate")
    type: OutputType = OutputType.ISO
    collision: Literal["suffix", "ask", "overwrite"] = "suffix"
    verify_level: int = Field(default=2, alias="verifyLevel")
    sidecar: SidecarSection = Field(default_factory=SidecarSection)


class ValidationSection(_Section):
    """严格开关（配置 §3.5：唯一归属是 TaskConfig）。"""

    strict_unknown_fields: bool = Field(default=False, alias="strictUnknownFields")


class ExecutionSection(_Section):
    """运行控制（配置 §3.5）。"""

    dry_run: bool = Field(default=False, alias="dryRun")
    resume: bool = True
    hang_warn_minutes: int = Field(default=10, alias="hangWarnMinutes")
    validation: ValidationSection = Field(default_factory=ValidationSection)


# ---------------------------------------------------------------------------
# 顶层
# ---------------------------------------------------------------------------


class TaskConfig(_Section):
    """任务配置（配置 §3.1）——GUI 保存、CLI 输入、编排消费的唯一契约。"""

    schema_version: str = Field(alias="schemaVersion")
    """配置格式版本（配置 §3.1 必填）。"""

    meta: MetaSection
    """任务元信息（必填）。"""

    input: InputSection
    """输入与起始阶段（必填）。"""
    source: SourceSection | None = None
    fetch: FetchSection | None = None
    convert: ConvertSection | None = None
    slim: SlimSection | None = None
    optimize: OptimizeSection | None = None
    integrate: IntegrateSection | None = None
    unattend: UnattendSection | None = None
    output: OutputSection
    """输出规则（必填）。"""
    execution: ExecutionSection | None = None

    # -- 派生信息 ---------------------------------------------------------

    @property
    def entry(self) -> EntryPoint:
        """入口（= ``input.mode``）。"""
        return self.input.mode

    def fingerprint(self, context: FingerprintContext | None = None) -> str:
        """配置指纹（配置 §8.2）：``sha256(canonical_json)``，排除 meta / 路径 / 代理。

        排除路径是为了「同一套业务选项在不同机器上指纹相同」，跨机核对才有意义。

        Returns:
            64 位十六进制摘要。
        """
        ctx = context or FingerprintContext()
        exclude: dict[str, Any] = {}
        if ctx.exclude_meta:
            exclude["meta"] = True
        if ctx.exclude_proxy:
            exclude["fetch"] = {"proxy": True}
        if ctx.exclude_paths:
            exclude["output"] = {"dir": True}

        data = self.model_dump(mode="json", by_alias=True, exclude=exclude)

        if ctx.exclude_paths:
            input_node = data.get("input")
            if isinstance(input_node, dict):
                for key in ("image", "uupFolder"):
                    node = input_node.get(key)
                    if isinstance(node, dict):
                        node.pop("path", None)

        payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # -- 语义校验（V2） ---------------------------------------------------

    def check_semantics(
        self,
        *,
        conservative_mode: bool = False,
        known_edition_indexes: set[int] | None = None,
    ) -> list[ConfigIssue]:
        """执行 V2 语义校验（配置 §8.1）；返回问题列表，空列表即通过。

        Args:
            conservative_mode: 工具版本未知时的保守模式（引擎 §5），影响 LZMS/ESD 判定。
            known_edition_indexes: ``probe.json`` 里实际存在的索引；``None`` 表示未探测，跳过该项。

        Returns:
            :class:`ConfigIssue` 列表。
        """
        issues: list[ConfigIssue] = []
        issues.extend(self._check_input(known_edition_indexes))
        issues.extend(self._check_convert(conservative_mode))
        issues.extend(self._check_output(conservative_mode))
        issues.extend(self._check_ranges())
        issues.extend(self._check_execution())
        return issues

    def _check_input(self, known_indexes: set[int] | None) -> list[ConfigIssue]:
        issues: list[ConfigIssue] = []
        mode = self.input.mode

        if self.input.start_stage not in _SELECTABLE_STAGES:
            issues.append(
                ConfigIssue(
                    "input.startStage",
                    SEMANTIC_INVALID,
                    f"未知起始阶段：{self.input.start_stage}（可选：{'/'.join(_SELECTABLE_STAGES)}）",
                )
            )
        else:
            earliest = _EARLIEST_STAGE_BY_MODE[mode]
            if _STAGE_ORDER.index(self.input.start_stage) < _STAGE_ORDER.index(earliest):
                issues.append(
                    ConfigIssue(
                        "input.startStage",
                        SEMANTIC_INVALID,
                        f"该输入无法从 {self.input.start_stage} 开始，最早为 {earliest}",
                    )
                )

        if mode is EntryPoint.ONLINE and self.source is None:
            issues.append(
                ConfigIssue("source", SEMANTIC_INVALID, "入口 A（online）必须提供 source")
            )

        if mode is EntryPoint.IMAGE:
            image = self.input.image
            if image is None or not image.path:
                issues.append(
                    ConfigIssue("input.image.path", SEMANTIC_INVALID, "入口 C 必须提供映像路径")
                )
            elif not image.editions:
                issues.append(
                    ConfigIssue(
                        "input.image.editions",
                        SEMANTIC_INVALID,
                        "入口 C 至少要选一个索引",
                    )
                )
            elif known_indexes is not None:
                missing = sorted(set(image.editions) - known_indexes)
                if missing:
                    issues.append(
                        ConfigIssue(
                            "input.image.editions",
                            SEMANTIC_INVALID,
                            f"索引不存在于探测结果：{missing}",
                        )
                    )

        if mode is EntryPoint.UUP_FOLDER:
            folder = self.input.uup_folder
            if folder is None or not folder.path:
                issues.append(
                    ConfigIssue(
                        "input.uupFolder.path", SEMANTIC_INVALID, "入口 B 必须提供文件夹路径"
                    )
                )

        return issues

    def _check_convert(self, conservative_mode: bool) -> list[ConfigIssue]:
        issues: list[ConfigIssue] = []
        convert = self.convert
        if convert is None:
            return issues

        if convert.virtual_edition.enabled:
            issues.append(
                ConfigIssue(
                    "convert.virtualEdition.enabled",
                    SEMANTIC_INVALID,
                    "虚拟版本为实验性能力，本期不开放（CV-06）",
                )
            )
        if convert.esd_decrypt.enabled:
            issues.append(
                ConfigIssue(
                    "convert.esdDecrypt.enabled",
                    SEMANTIC_INVALID,
                    "ESD 解密为实验性能力，本期不开放（CV-29）",
                )
            )
        if convert.cleanup.reset_base and not convert.cleanup.component_cleanup:
            issues.append(
                ConfigIssue(
                    "convert.cleanup.resetBase",
                    SEMANTIC_INVALID,
                    "resetBase 依赖 componentCleanup=true（CV-04）",
                )
            )
        if convert.compression is CompressionKind.LZMS and conservative_mode:
            issues.append(
                ConfigIssue(
                    "convert.compression",
                    SEMANTIC_INVALID,
                    "保守模式下不允许 LZMS 压缩（引擎 §5）",
                )
            )
        return issues

    def _check_output(self, conservative_mode: bool) -> list[ConfigIssue]:
        issues: list[ConfigIssue] = []
        output = self.output

        if output.type is OutputType.SWM:
            issues.append(
                ConfigIssue(
                    "output.type",
                    "OUTPUT_TYPE_UNSUPPORTED",
                    "SWM 分卷输出尚未验证，请改用 ISO 或 WIM",
                )
            )
        if output.type is OutputType.ESD and conservative_mode:
            issues.append(
                ConfigIssue(
                    "output.type",
                    "OUTPUT_TYPE_UNSUPPORTED",
                    "保守模式下不允许产出 ESD（引擎 §5）",
                )
            )
        if output.verify_level not in (1, 2, 3):
            issues.append(
                ConfigIssue("output.verifyLevel", SEMANTIC_INVALID, "verifyLevel 只能是 1/2/3")
            )
        return issues

    def _check_ranges(self) -> list[ConfigIssue]:
        issues: list[ConfigIssue] = []

        def check(field: str, value: int, low: int, high: int) -> None:
            if not (low <= value <= high):
                issues.append(
                    ConfigIssue(field, SEMANTIC_INVALID, f"{field} 必须在 {low}–{high} 之间")
                )

        if self.fetch is not None:
            check("fetch.split", self.fetch.split, 1, 16)
            check("fetch.timeoutSeconds", self.fetch.timeout_seconds, 10, 300)
        if self.convert is not None:
            check("convert.apps.level", self.convert.apps.level, 0, 4)
        check("output.verifyLevel", self.output.verify_level, 1, 3)
        return issues

    def _check_execution(self) -> list[ConfigIssue]:
        issues: list[ConfigIssue] = []
        if self.execution is not None and not (1 <= self.execution.hang_warn_minutes <= 120):
            issues.append(
                ConfigIssue(
                    "execution.hangWarnMinutes",
                    SEMANTIC_INVALID,
                    "hangWarnMinutes 必须在 1–120 之间",
                )
            )
        return issues


# ---------------------------------------------------------------------------
# 未知字段收集（配置 §7）
# ---------------------------------------------------------------------------


def iter_unknown_fields(model: BaseModel, prefix: str = "") -> list[str]:
    """递归收集 ``extra="allow"`` 吸收进来的未知字段路径。

    Args:
        model: 任意配置模型。
        prefix: 内部递归用，调用方留空。

    Returns:
        形如 ``["input.foo", "convert.bar"]`` 的路径列表，保持稳定顺序。
    """
    found: list[str] = []
    extra = getattr(model, "model_extra", None) or {}
    for key in sorted(extra):
        found.append(f"{prefix}{key}" if not prefix else f"{prefix}.{key}")

    # 用类属性访问：pydantic 2.11 起实例访问 model_fields 已弃用
    for name in type(model).model_fields:
        path = f"{prefix}.{name}" if prefix else name
        found.extend(_walk_unknown(getattr(model, name), path))
    return found


def _walk_unknown(value: Any, path: str) -> list[str]:
    if isinstance(value, BaseModel):
        return iter_unknown_fields(value, path)
    if isinstance(value, list):
        found: list[str] = []
        for index, item in enumerate(value):
            found.extend(_walk_unknown(item, f"{path}[{index}]"))
        return found
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            found.extend(_walk_unknown(item, f"{path}.{key}"))
        return found
    return []


def new_task_id() -> str:
    """生成 8 位十六进制任务短 id（配置 §2：创建时生成、终身不变）。"""
    return secrets.token_hex(4)


def default_task_config(
    *,
    mode: EntryPoint = EntryPoint.ONLINE,
    task_id: str | None = None,
    name: str = "",
) -> TaskConfig:
    """构造一份最小可用配置（GUI 新建任务时的起点）。"""
    now = datetime.now().astimezone()
    return TaskConfig(
        schemaVersion=CONFIG_SCHEMA_VERSION,
        meta=MetaSection(id=task_id or new_task_id(), name=name, createdAt=now, updatedAt=now),
        input=InputSection(mode=mode),
        output=OutputSection(),
    )


def load_json_text(path: Path) -> str:
    """读取配置文件文本（V0 语法层还会检查 BOM 与编码，见 loader）。"""
    return path.read_text(encoding="utf-8")
