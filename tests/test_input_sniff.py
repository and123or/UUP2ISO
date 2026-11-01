"""输入嗅探门禁（输入 §2.1、§2.2）。

验收：**扩展名误标可识别**（头优先）、UUP 文件夹特征、ISO 的 32769 偏移。
"""

from __future__ import annotations

from pathlib import Path

from uup2iso.infrastructure.input import (
    HEAD_SIZE,
    ISO_OFFSET,
    ISO_SIGNATURE,
    WIM_MAGIC,
    InputKind,
    find_image_files,
    is_uup_folder,
    sniff_file,
    sniff_path,
)


def write_wim(path: Path, *, magic: bytes = WIM_MAGIC) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(magic + bytes(1024))
    return path


def write_iso(path: Path) -> Path:
    head = bytearray(HEAD_SIZE)
    head[ISO_OFFSET : ISO_OFFSET + len(ISO_SIGNATURE)] = ISO_SIGNATURE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(head))
    return path


def test_head_size_covers_iso_signature() -> None:
    """读 32 KB 会差 6 字节，所以必须 ≥ 32774（这里固定 64 KB）。"""
    assert ISO_OFFSET + len(ISO_SIGNATURE) <= HEAD_SIZE


def test_wim_magic_detected(tmp_path: Path) -> None:
    result = sniff_file(write_wim(tmp_path / "install.wim"))
    assert result.kind is InputKind.WIM
    assert result.evidence == "magic@0"
    assert result.extension_mismatch is False


def test_iso_signature_detected(tmp_path: Path) -> None:
    result = sniff_file(write_iso(tmp_path / "win11.iso"))
    assert result.kind is InputKind.ISO
    assert "32769" in result.evidence


def test_mislabeled_iso_is_wim(tmp_path: Path) -> None:
    """`.iso` 实为 WIM：按真实类型处理并记 extension_mismatch。"""
    result = sniff_file(write_wim(tmp_path / "fake.iso"))

    assert result.kind is InputKind.WIM
    assert result.extension_mismatch is True
    assert any("extension_mismatch" in warning for warning in result.warnings)


def test_mislabeled_wim_is_iso(tmp_path: Path) -> None:
    result = sniff_file(write_iso(tmp_path / "fake.wim"))
    assert result.kind is InputKind.ISO
    assert result.extension_mismatch is True


def test_esd_extension_is_provisionally_esd(tmp_path: Path) -> None:
    result = sniff_file(write_wim(tmp_path / "install.esd"))
    assert result.kind is InputKind.ESD
    assert result.extension_mismatch is False


def test_split_swm_detected_by_name(tmp_path: Path) -> None:
    result = sniff_file(write_wim(tmp_path / "install.swm"))
    assert result.kind is InputKind.SWM


def test_split_swm_detected_by_sibling(tmp_path: Path) -> None:
    write_wim(tmp_path / "install2.swm")
    result = sniff_file(write_wim(tmp_path / "disk.swm"))
    assert result.kind is InputKind.SWM


def test_unknown_file(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    assert sniff_file(path).kind is InputKind.UNKNOWN


def test_uup_folder_requires_manifest_and_archive(tmp_path: Path) -> None:
    folder = tmp_path / "uup"
    folder.mkdir()
    (folder / "uupdump.mk").write_text("[INFO]", encoding="utf-8")
    assert is_uup_folder(folder)[0] is False, "只有清单没有归档，不算 UUP"

    (folder / "Microsoft-Windows.a.cab").write_bytes(b"CAB")
    hit, evidence = is_uup_folder(folder)
    assert hit is True
    assert any(item.name == "uupdump.mk" for item in evidence)


def test_uup_folder_without_manifest(tmp_path: Path) -> None:
    folder = tmp_path / "uup2"
    folder.mkdir()
    (folder / "x.esd").write_bytes(b"ESD")
    assert is_uup_folder(folder)[0] is False


def test_uup_folder_supports_uups_subdir(tmp_path: Path) -> None:
    folder = tmp_path / "uup3"
    (folder / "UUPs").mkdir(parents=True)
    (folder / "uupdump.mk").write_text("x", encoding="utf-8")
    (folder / "UUPs" / "a.esd").write_bytes(b"ESD")

    assert is_uup_folder(folder)[0] is True


def test_directory_with_two_images_yields_two_candidates(tmp_path: Path) -> None:
    write_iso(tmp_path / "win11.iso")
    write_wim(tmp_path / "install.wim")

    candidates = sniff_path(tmp_path)
    assert {item.kind for item in candidates} == {InputKind.ISO, InputKind.WIM}
    assert len(candidates) == 2


def test_uup_folder_with_extra_iso_is_ambiguous_candidates(tmp_path: Path) -> None:
    (tmp_path / "uupdump.mk").write_text("x", encoding="utf-8")
    (tmp_path / "a.cab").write_bytes(b"CAB")
    write_iso(tmp_path / "extra.iso")

    kinds = {item.kind for item in sniff_path(tmp_path)}
    assert kinds == {InputKind.UUP_FOLDER, InputKind.ISO}


def test_find_image_files_ignores_nested(tmp_path: Path) -> None:
    write_iso(tmp_path / "a.iso")
    write_iso(tmp_path / "sub" / "b.iso")

    assert [item.name for item in find_image_files(tmp_path)] == ["a.iso"]


def test_missing_path_has_no_candidates(tmp_path: Path) -> None:
    assert sniff_path(tmp_path / "nope.iso") == ()
