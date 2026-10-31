"""引擎能力推导与保守模式（引擎 §4、§4.1、§5）。

原则：**不确定即不支持**。版本解析失败 → ``version_known=False`` → 上层进保守模式，
禁用 ``--ref`` 与 LZMS/ESD 输出，宁可用 LZX 慢一点，也不要在跑到一半时失败重跑几十分钟。

版本阈值表标注「待实测校准」：本仓库没有真实工具样本，阈值取自规范说明，
首次实测后在此校准并同步 CHANGELOG（引擎 §4.1 的原文口径）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from uup2iso.core.models.enums import CompressionKind, EngineKind
from uup2iso.core.models.tools import EngineCapabilities

__all__ = [
    "VersionThresholds",
    "capabilities_for",
    "is_version_at_least",
    "parse_version",
]

_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?")


@dataclass(frozen=True, slots=True)
class VersionThresholds:
    """能力阈值（引擎 §4.1；**待实测校准**）。"""

    wimlib_lzms: tuple[int, ...] = (1, 13, 0)
    """wimlib-imagex 写 LZMS 的最低版本（保守取值，实测后校准）。"""

    wimlib_ref_export: tuple[int, ...] = (1, 14, 0)
    """``wimlib export --ref`` 分片引用导出的最低版本。"""

    wimlib_verify: tuple[int, ...] = (1, 0, 0)
    """``wimlib verify`` 自 1.0 起可用；缺失 wimlib 即无法校验（硬依赖）。"""


THRESHOLDS = VersionThresholds()


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """从版本输出里抽出数字版本号；抽不到返回 ``None``（→ 保守模式）。"""
    if not text:
        return None
    match = _VERSION.search(text)
    if match is None:
        return None
    parts = [int(part) for part in match.groups() if part is not None]
    return tuple(parts) or None


def is_version_at_least(version: tuple[int, ...] | None, threshold: tuple[int, ...]) -> bool:
    """版本比较（按段补齐后逐段比）；``version`` 为 ``None`` 时保守返回 ``False``。"""
    if version is None:
        return False
    width = max(len(version), len(threshold))
    left = version + (0,) * (width - len(version))
    right = threshold + (0,) * (width - len(threshold))
    return left >= right


def capabilities_for(
    engine: EngineKind,
    version: tuple[int, ...] | None,
    *,
    version_known: bool | None = None,
) -> EngineCapabilities:
    """推导某引擎的能力集合。

    Args:
        engine: 引擎种类。
        version: 解析出的版本号元组；``None`` 表示未知。
        version_known: 覆盖「版本是否已登记」；默认由 ``version`` 推断。

    Returns:
        :class:`EngineCapabilities`；未知版本时为全保守值。
    """
    known = bool(version) if version_known is None else version_known
    base_compressions = {CompressionKind.NONE, CompressionKind.XPRESS, CompressionKind.LZX}

    if engine is EngineKind.WIMLIB:
        compressions = set(base_compressions)
        can_write_esd = known and is_version_at_least(version, THRESHOLDS.wimlib_lzms)
        if can_write_esd:
            compressions.add(CompressionKind.LZMS)
        return EngineCapabilities(
            engine=engine,
            version_known=known,
            compressions=frozenset(compressions),
            can_write_esd=can_write_esd,
            can_verify=known and is_version_at_least(version, THRESHOLDS.wimlib_verify),
            can_mount=False,  # wimlib 无挂载语义（引擎 §3.2 R6）
            supports_ref_export=known
            and is_version_at_least(version, THRESHOLDS.wimlib_ref_export),
        )

    # DISM：挂载/加删包唯一路径；LZMS 走 /Compress:recovery，但版本未知时仍按保守处理
    compressions = set(base_compressions)
    if known:
        compressions.add(CompressionKind.LZMS)
    return EngineCapabilities(
        engine=EngineKind.DISM,
        version_known=known,
        compressions=frozenset(compressions),
        can_write_esd=known,
        can_verify=False,  # 校验是 wimlib 的硬依赖（引擎 §4.1）
        can_mount=True,
        supports_ref_export=False,  # DISM 无 --ref
    )
