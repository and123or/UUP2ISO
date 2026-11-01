"""输入识别（WP1-T4）：内容嗅探、`wimlib info` 解析、输入探测与拒绝。

| 模块 | 职责 |
| --- | --- |
| :mod:`.sniff` | 64 KB 读头判类型（WIM 魔数 / ISO `CD001`@32769 / UUP 文件夹特征） |
| :mod:`.wiminfo` | 解析 `wimlib info`（XML 优先、文本兜底），edition 归一化 |
| :mod:`.probe` | 把候选收敛成一个输入，或按 `INPUT_*` 明确拒绝（多候选不猜） |
"""

from __future__ import annotations

from uup2iso.infrastructure.input.probe import (
    SWM_MERGE_HINT,
    InputCandidate,
    InputProber,
    ProbeReport,
)
from uup2iso.infrastructure.input.sniff import (
    HEAD_SIZE,
    ISO_OFFSET,
    ISO_SIGNATURE,
    WIM_MAGIC,
    InputKind,
    SniffResult,
    find_image_files,
    is_uup_folder,
    sniff_file,
    sniff_path,
)
from uup2iso.infrastructure.input.wiminfo import (
    WimEncryptedError,
    WimImage,
    WimInfo,
    WimInfoError,
    classify_wimlib_failure,
    normalize_edition,
    parse_wiminfo,
)

__all__ = [
    "HEAD_SIZE",
    "ISO_OFFSET",
    "ISO_SIGNATURE",
    "SWM_MERGE_HINT",
    "WIM_MAGIC",
    "InputCandidate",
    "InputKind",
    "InputProber",
    "ProbeReport",
    "SniffResult",
    "WimEncryptedError",
    "WimImage",
    "WimInfo",
    "WimInfoError",
    "classify_wimlib_failure",
    "find_image_files",
    "is_uup_folder",
    "normalize_edition",
    "parse_wiminfo",
    "sniff_file",
    "sniff_path",
]
