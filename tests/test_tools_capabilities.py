"""能力推导门禁（引擎 §4、§4.1、§5）。

核心取向：**不确定即不支持**。版本未知时宁可只用 LZX，也不要在跑到一半时失败。
"""

from __future__ import annotations

import pytest

from uup2iso.core.models.enums import CompressionKind, EngineKind
from uup2iso.infrastructure.tools import capabilities as caps


def test_parse_version_variants() -> None:
    assert caps.parse_version("aria2 version 1.37.0") == (1, 37, 0)
    assert caps.parse_version("wimlib-imagex 1.14.4") == (1, 14, 4)
    assert caps.parse_version("Version: 10.0.26100.1") == (10, 0, 26100, 1)
    assert caps.parse_version("7-Zip 24.09 (x64)") == (24, 9)
    assert caps.parse_version(None) is None
    assert caps.parse_version("no digits here") is None


def test_version_comparison() -> None:
    assert caps.is_version_at_least((1, 14, 4), (1, 13, 0))
    assert caps.is_version_at_least((1, 13), (1, 13, 0))
    assert not caps.is_version_at_least((1, 12, 9), (1, 13, 0))
    assert not caps.is_version_at_least(None, (1, 0, 0)), "未知版本一律按「不满足」"


def test_wimlib_full_capabilities() -> None:
    result = caps.capabilities_for(EngineKind.WIMLIB, (1, 14, 4))
    assert result.version_known
    assert result.can_verify
    assert result.can_write_esd
    assert result.supports_ref_export
    assert not result.can_mount, "wimlib 没有挂载语义"
    assert CompressionKind.LZMS in result.compressions


def test_wimlib_old_version_lacks_esd_and_ref() -> None:
    """低于阈值：仍可用（LZX），但 ESD 与 --ref 要禁用并记降级。"""
    result = caps.capabilities_for(EngineKind.WIMLIB, (1, 12, 0))
    assert result.version_known
    assert not result.can_write_esd
    assert not result.supports_ref_export
    assert CompressionKind.LZMS not in result.compressions
    assert CompressionKind.LZX in result.compressions
    assert result.can_verify


def test_unknown_version_is_conservative() -> None:
    result = caps.capabilities_for(EngineKind.WIMLIB, None)
    assert not result.version_known
    assert result.is_conservative
    assert not result.can_write_esd
    assert not result.supports_ref_export


def test_dism_capabilities() -> None:
    known = caps.capabilities_for(EngineKind.DISM, (10, 0, 26100, 1))
    assert known.can_mount
    assert known.can_write_esd
    assert not known.can_verify
    assert not known.supports_ref_export

    unknown = caps.capabilities_for(EngineKind.DISM, None)
    assert unknown.is_conservative
    assert unknown.can_mount, "版本未知不影响挂载能力（DISM 有挂载语义）"


@pytest.mark.parametrize("engine", [EngineKind.WIMLIB, EngineKind.DISM])
def test_all_engines_support_basic_compressions(engine: EngineKind) -> None:
    result = caps.capabilities_for(engine, (1, 0, 0))
    assert {
        CompressionKind.NONE,
        CompressionKind.XPRESS,
        CompressionKind.LZX,
    } <= result.compressions
