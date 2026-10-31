"""领域模型门禁（WP0-T3 验收：模型可用 + 取值与文档一致）。

三类断言：

1. **枚举取值必须逐字等于规范里的取值集合**——文档改了取值而代码没改，这里先红；
2. 冻结值对象**不可变**且可按值比较（编排层传参安全）；
3. 派生属性（清单总字节、edition 查找、能力判定）行为正确。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from uup2iso.core.models import (
    Architecture,
    BuildInfo,
    CompressionKind,
    DownloadedFile,
    DownloadFileState,
    DownloadReport,
    Edition,
    EngineCapabilities,
    EngineKind,
    EntryPoint,
    ImageInfo,
    ImageType,
    Manifest,
    ManifestSource,
    OutputType,
    PresetKind,
    StageState,
    SubstepState,
    TaskState,
    ToolKind,
    UupFile,
    VerifyFinding,
    VerifyLevel,
    VerifyReport,
    VerifySeverity,
)

# --- 1. 枚举取值与文档一致 ---------------------------------------------------


def test_entry_point_matches_config_mode() -> None:
    """配置 §3.2 `input.mode`：online / uupFolder / image。"""
    assert {item.value for item in EntryPoint} == {"online", "uupFolder", "image"}


def test_image_and_output_types_match_config() -> None:
    """输入 §2.1 与 配置 §3.4。"""
    assert {item.value for item in ImageType} == {"iso", "wim", "esd", "swm", "unknown"}
    assert {item.value for item in OutputType} == {"iso", "wim", "esd", "swm"}


def test_compression_values_are_uppercase_per_config() -> None:
    """配置 §3.3：none / XPRESS / LZX / LZMS（大写，与 ConvertConfig 对齐）。"""
    assert {item.value for item in CompressionKind} == {"none", "XPRESS", "LZX", "LZMS"}


def test_task_state_matches_terminology() -> None:
    """术语 §2.2 / 编排 §4.1：7 个取值，且不存在 paused / 部分成功。"""
    values = {item.value for item in TaskState}
    assert values == {
        "draft",
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
    }
    assert "paused" not in values


def test_stage_and_substep_states_match_orchestration() -> None:
    """编排 §4.2：阶段用 succeeded，子步骤用 done（不能混用）。"""
    assert {item.value for item in StageState} == {
        "pending",
        "running",
        "succeeded",
        "failed",
        "skipped",
        "cancelled",
    }
    assert {item.value for item in SubstepState} == {
        "pending",
        "running",
        "done",
        "failed",
        "skipped",
    }


def test_download_file_state_matches_terminology() -> None:
    """术语 §2.2。"""
    assert {item.value for item in DownloadFileState} == {
        "pending",
        "downloading",
        "downloaded",
        "verified",
        "failed",
    }


def test_verify_level_is_ordered() -> None:
    """输出 §6：L1 < L2 < L3，且可比较大小（用于「最高执行级别」判断）。"""
    assert VerifyLevel.L1 < VerifyLevel.L2 < VerifyLevel.L3
    assert VerifyLevel.L3.value == 3


def test_tool_kinds_cover_engine_and_download_tools() -> None:
    """引擎 §3.1 + 下载 §4：六类外部工具齐备。"""
    values = {item.value for item in ToolKind}
    assert {"aria2c", "7z", "wimlib-imagex", "dism", "oscdimg", "reg"} <= values


def test_preset_kinds_cover_four_categories() -> None:
    """配置 §4.1：精简 / 优化 / 集成 / 无人值守。"""
    assert {item.value for item in PresetKind} == {"slim", "optimize", "integrate", "unattend"}


# --- 2. 冻结与相等性 ---------------------------------------------------------


def test_models_are_frozen() -> None:
    """值对象必须不可变：下游改状态属设计事故。"""
    build = BuildInfo(
        build="26100.6508", revision="6508", arch=Architecture.X64, languages=("zh-cn",)
    )
    with pytest.raises(FrozenInstanceError):
        build.build = "26100.9999"  # type: ignore[misc]


def test_models_compare_by_value() -> None:
    """同一组字段构造的两个实例应相等（便于测试与去重）。"""
    left = Edition(index=1, name="Windows 11 Pro", edition="professional")
    right = Edition(index=1, name="Windows 11 Pro", edition="professional")
    assert left == right


# --- 3. 派生属性与判定 -------------------------------------------------------


def _manifest(total: int = 30, sizes: tuple[int, ...] = (10, 20)) -> Manifest:
    files = tuple(
        UupFile(name=f"f{i}.cab", size=size, sha1="a" * 40, url="https://uupdump.net/x")
        for i, size in enumerate(sizes)
    )
    return Manifest(
        build="26100.6508",
        revision="6508",
        language="zh-cn",
        edition="professional",
        files=files,
        source=ManifestSource(base_url="https://uupdump.net/", fetched_at=datetime.now(UTC)),
        total_bytes=total,
    )


def test_manifest_total_consistency() -> None:
    """下载 §3.2-3：totalBytes 必须等于各文件大小之和。"""
    assert _manifest().is_total_consistent
    assert not _manifest(total=99).is_total_consistent
    assert _manifest().computed_total_bytes == 30


def test_manifest_duplicate_detection() -> None:
    """下载 §3.2-2：文件名必须唯一。"""
    dup = Manifest(
        build="26100.6508",
        revision="6508",
        language="zh-cn",
        edition="professional",
        files=(
            UupFile(name="same.cab", size=1, sha1="a" * 40, url="https://uupdump.net/a"),
            UupFile(name="same.cab", size=1, sha1="b" * 40, url="https://uupdump.net/b"),
        ),
        source=ManifestSource(base_url="https://uupdump.net/", fetched_at=datetime.now(UTC)),
        total_bytes=2,
    )
    assert dup.has_duplicate_names


def test_manifest_required_files_filtered() -> None:
    """可选文件不计入必需集合。"""
    manifest = _manifest()
    assert len(manifest.required_files) == 2


def test_build_number_and_display_name() -> None:
    """无人值守 §3.1 用主构建号查组件版本。"""
    info = BuildInfo(
        build="26100.6508", revision="6508", arch=Architecture.X64, languages=("zh-cn",)
    )
    assert info.build_number == "26100"
    assert "26100.6508" in info.display_name


def test_image_info_rejection_and_lookup() -> None:
    """输入 §3：加密/多卷/分片任一命中即拒绝；§5 按索引取 edition。"""
    editions = (Edition(index=1, name="Pro", edition="professional"), Edition(index=2, name="Home"))
    ok = ImageInfo(path=Path("a.iso"), type=ImageType.ISO, editions=editions)
    assert ok.is_install_media
    assert not ok.is_rejected
    assert ok.edition_by_index(2) == editions[1]
    assert ok.edition_by_index(9) is None
    assert ok.edition_indexes() == (1, 2)
    assert editions[1].is_unknown_edition

    rejected = ImageInfo(path=Path("a.esd"), type=ImageType.ESD, encrypted=True, editions=editions)
    assert rejected.is_rejected
    assert not rejected.is_install_media


def test_engine_capabilities_conservative_mode() -> None:
    """引擎 §4/§5：版本未知或压缩能力缺失即保守模式。"""
    unknown = EngineCapabilities(engine=EngineKind.WIMLIB, version_known=False)
    assert unknown.is_conservative
    assert not unknown.supports(CompressionKind.LZX)

    normal = EngineCapabilities(
        engine=EngineKind.WIMLIB,
        version_known=True,
        compressions=frozenset({CompressionKind.LZX, CompressionKind.LZMS}),
        can_verify=True,
    )
    assert not normal.is_conservative
    assert normal.supports(CompressionKind.LZMS)
    assert not normal.can_mount


def test_download_report_aggregations() -> None:
    """下载 §7：失败清单要带期望/实际哈希。"""
    report = DownloadReport(
        files=(
            DownloadedFile(
                name="ok.cab",
                state=DownloadFileState.VERIFIED,
                size_bytes=10,
                expected_sha1="a" * 40,
                actual_sha1="a" * 40,
            ),
            DownloadedFile(
                name="bad.cab",
                state=DownloadFileState.FAILED,
                expected_sha1="b" * 40,
                actual_sha1="c" * 40,
                error="hash_mismatch",
            ),
        )
    )
    assert not report.is_complete
    assert len(report.verified_files) == 1
    failures = report.failures
    assert len(failures) == 1
    assert failures[0].name == "bad.cab"
    assert failures[0].expected == "b" * 40
    assert failures[0].actual == "c" * 40
    assert report.total_bytes == 10


def test_verify_report_fatal_semantics() -> None:
    """输出 §6：任一 ERROR 即判该级别失败。"""
    ok_report = VerifyReport(
        level=VerifyLevel.L1,
        findings=(VerifyFinding(code="L1_OK", message="结构正常", severity=VerifySeverity.INFO),),
    )
    assert ok_report.ok
    assert not ok_report.errors

    bad_report = VerifyReport(
        level=VerifyLevel.L2,
        findings=(VerifyFinding(code="L2_INDEX_EMPTY", message="索引为空", level=VerifyLevel.L2),),
    )
    assert not bad_report.ok
    assert bad_report.errors[0].code == "L2_INDEX_EMPTY"
