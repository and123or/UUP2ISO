"""领域模型统一出口（WP0-T3）。

分层约定：`core` 不依赖任何第三方库（GUI框架 §4.2），因此这里全部是
标准库 ``dataclasses`` + ``enum`` 的冻结值对象，可直接用 `==` 比较、
可安全跨层传递，且**不可变**（避免编排层状态被下游偷偷改写）。

未在本轮落地的模型：`TaskConfig` 及其选项对象属 WP0-T6（配置契约），
`PipelineContext`/`Stage` 属 WP1-T1（阶段模型），见 CHANGELOG 0.1.9 已知限制。
"""

from __future__ import annotations

from uup2iso.core.models.build import BuildInfo
from uup2iso.core.models.config import (
    CONFIG_SCHEMA_VERSION,
    ConfigIssue,
    ConvertSection,
    ExecutionSection,
    FingerprintContext,
    InputSection,
    MetaSection,
    OutputSection,
    TaskConfig,
    default_task_config,
    iter_unknown_fields,
    new_task_id,
)
from uup2iso.core.models.download import DownloadedFile, DownloadFailure, DownloadReport
from uup2iso.core.models.enums import (
    Architecture,
    CompressionKind,
    DownloadFileState,
    EngineKind,
    EntryPoint,
    ImageType,
    OutputType,
    PresetKind,
    StageState,
    SubstepState,
    TaskState,
    ToolKind,
    VerifyLevel,
)
from uup2iso.core.models.image import UNKNOWN_EDITION, Edition, ImageInfo
from uup2iso.core.models.manifest import SCHEMA_VERSION, Manifest, ManifestSource, UupFile
from uup2iso.core.models.preset import PresetDocument, PresetIssue, PresetRuleItems
from uup2iso.core.models.rules import RuleOutcome, RuleReport
from uup2iso.core.models.tools import EngineCapabilities, ToolInfo, ToolSource
from uup2iso.core.models.verification import VerifyFinding, VerifyReport, VerifySeverity

__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "ConfigIssue",
    "ConvertSection",
    "ExecutionSection",
    "FingerprintContext",
    "InputSection",
    "MetaSection",
    "OutputSection",
    "PresetDocument",
    "PresetIssue",
    "PresetRuleItems",
    "TaskConfig",
    "default_task_config",
    "iter_unknown_fields",
    "new_task_id",
    "UNKNOWN_EDITION",
    "Architecture",
    "BuildInfo",
    "CompressionKind",
    "DownloadFailure",
    "DownloadFileState",
    "DownloadReport",
    "DownloadedFile",
    "Edition",
    "EngineCapabilities",
    "EngineKind",
    "EntryPoint",
    "ImageInfo",
    "ImageType",
    "Manifest",
    "ManifestSource",
    "OutputType",
    "PresetKind",
    "RuleOutcome",
    "RuleReport",
    "StageState",
    "SubstepState",
    "TaskState",
    "ToolInfo",
    "ToolKind",
    "ToolSource",
    "UupFile",
    "VerifyFinding",
    "VerifyLevel",
    "VerifyReport",
    "VerifySeverity",
]
