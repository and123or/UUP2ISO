"""行分类门禁（进程 §5.2、§5.3）。

最关键的一条：**版本未登记时的严格兜底绝不能把错误行当进度丢掉**（FR9.3 可定位性）。
"""

from __future__ import annotations

from uup2iso.core.models.enums import ToolKind
from uup2iso.infrastructure.process.classifier import LineClassifier
from uup2iso.infrastructure.process.events import EventKind

_classifier = LineClassifier()


def classify(tool: ToolKind, text: str, *, version_known: bool = True) -> EventKind:
    return _classifier.classify(tool, text, version_known=version_known)


def test_dism_progress_recognized() -> None:
    assert classify(ToolKind.DISM, "[==== 12.5%]") is EventKind.PROGRESS
    assert classify(ToolKind.DISM, "  45.0%  ") is EventKind.PROGRESS


def test_dism_result_recognized() -> None:
    assert classify(ToolKind.DISM, "The operation completed successfully.") is EventKind.RESULT
    assert classify(ToolKind.DISM, "操作成功完成。") is EventKind.RESULT


def test_result_wins_over_progress() -> None:
    """一行里既有百分比又有完成语，按判定顺序应算 result。"""
    assert classify(ToolKind.DISM, "[==== 100%] The operation completed successfully") is (
        EventKind.RESULT
    )


def test_wimlib_and_aria2c_patterns() -> None:
    assert classify(ToolKind.WIMLIB, 'Scanning "install.wim" 25%') is EventKind.PROGRESS
    assert classify(ToolKind.WIMLIB, "Successfully exported 1 image(s)") is EventKind.RESULT
    assert classify(ToolKind.ARIA2C, "[#abc123 1.2MiB/10MiB(12%) CN:1]") is EventKind.PROGRESS
    assert classify(ToolKind.ARIA2C, "Download Results:") is EventKind.RESULT


def test_unknown_line_is_plain_when_version_known() -> None:
    assert classify(ToolKind.DISM, "Deployment Image Servicing and Management tool") is (
        EventKind.LINE
    )


def test_unregistered_version_uses_strict_fallback_for_progress() -> None:
    """版本未登记：带 % 且无错误词 → 进度。"""
    assert classify(ToolKind.SEVEN_ZIP, "Extracting 45%", version_known=False) is (
        EventKind.PROGRESS
    )


def test_unregistered_version_keeps_error_lines_as_plain() -> None:
    """版本未登记：即使带 %，含 ERROR/Failed/失败 的一律不当进度丢掉。"""
    for text in [
        "ERROR: 45% of files failed",
        "Failed at 12%",
        "解压失败 30%",
        "错误：进度 90%",
    ]:
        assert classify(ToolKind.SEVEN_ZIP, text, version_known=False) is EventKind.LINE


def test_registered_tool_does_not_apply_strict_fallback() -> None:
    """已登记版本、但没命中正则的带百分号行，按规范第 3 步判 line。"""
    assert classify(ToolKind.DISM, "Something 45% odd") is EventKind.LINE


def test_unregistered_tool_without_marker_is_plain() -> None:
    assert classify(ToolKind.SEVEN_ZIP, "Extracting archive", version_known=False) is (
        EventKind.LINE
    )


def test_carriage_return_counts_as_redraw_marker() -> None:
    """未登记版本下，光标回车重绘（无 %）也算进度特征。"""
    assert classify(ToolKind.SEVEN_ZIP, "working\r", version_known=False) is EventKind.PROGRESS


def test_classifier_can_load_from_mapping() -> None:
    """为将来从 ``rules/process_patterns.yaml`` 加载预留的入口。"""
    classifier = LineClassifier.from_mapping(
        {"7z": {"progress": [r"^Extracting\b.*\d+%"], "result": [r"Everything is Ok"]}}
    )
    assert classifier.classify(ToolKind.SEVEN_ZIP, "Extracting 10%") is EventKind.PROGRESS
    assert classifier.classify(ToolKind.SEVEN_ZIP, "Everything is Ok") is EventKind.RESULT
    # 未登记的工具不受影响
    assert classifier.classify(ToolKind.DISM, "The operation completed successfully") is (
        EventKind.LINE
    )
