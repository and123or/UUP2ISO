"""输入探测门禁（输入 §2.3、§3、§4）。

验收重点：多候选**不猜**（`INPUT_AMBIGUOUS`）、拒绝时给出**具体项**（加密/多卷/分片/非 Windows），
`probe.json` 结构符合 §4。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uup2iso.core.errors import ErrorCode, InputError
from uup2iso.infrastructure.input import (
    HEAD_SIZE,
    ISO_OFFSET,
    ISO_SIGNATURE,
    WIM_MAGIC,
    InputKind,
    InputProber,
    WimEncryptedError,
    WimImage,
    WimInfo,
)


def write_wim(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(WIM_MAGIC + bytes(512))
    return path


def write_iso(path: Path) -> Path:
    head = bytearray(HEAD_SIZE)
    head[ISO_OFFSET : ISO_OFFSET + len(ISO_SIGNATURE)] = ISO_SIGNATURE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(head))
    return path


def wim_info(*names: str, compression: str = "LZX", part_total: int = 1) -> WimInfo:
    return WimInfo(
        images=tuple(
            WimImage(index=index + 1, name=name, description=name, edition="professional")
            for index, name in enumerate(names)
        ),
        compression=compression,
        part_number=1,
        part_total=part_total,
    )


def test_iso_probe_reports_pending_extract(tmp_path: Path) -> None:
    report = InputProber().probe(write_iso(tmp_path / "win11.iso"))

    assert report.type is InputKind.ISO
    assert "images_pending_extract" in report.warnings
    assert report.images == ()


def test_probe_json_matches_spec(tmp_path: Path) -> None:
    report = InputProber(wim_info=lambda _p: wim_info("Windows 11 Pro")).probe(
        write_wim(tmp_path / "install.wim")
    )
    payload = report.to_probe_json()

    assert payload["schemaVersion"] == "1.0"
    assert payload["type"] == "wim"
    assert payload["extensionMismatch"] is False
    assert payload["encrypted"] is False
    assert payload["multiVolume"] is False
    assert payload["fragmented"] is False
    assert payload["readOnlySource"] is True
    assert payload["images"][0]["edition"] == "professional"
    assert payload["images"][0]["index"] == 1
    assert isinstance(payload["warnings"], list)


def test_lzms_wim_is_refined_to_esd(tmp_path: Path) -> None:
    report = InputProber(wim_info=lambda _p: wim_info("Windows 11 Pro", compression="LZMS")).probe(
        write_wim(tmp_path / "install.wim")
    )

    assert report.type is InputKind.ESD
    assert "type_refined detected=esd reason=lzms" in report.warnings


def test_unknown_edition_is_warned(tmp_path: Path) -> None:
    info = WimInfo(images=(WimImage(index=1, name="Windows 11 特别版", edition="unknown"),))
    report = InputProber(wim_info=lambda _p: info).probe(write_wim(tmp_path / "x.wim"))

    assert any("edition_unknown" in warning for warning in report.warnings)


def test_split_esd_is_refused(tmp_path: Path) -> None:
    prober = InputProber(wim_info=lambda _p: wim_info("Windows 11 Pro", part_total=3))

    with pytest.raises(InputError) as caught:
        prober.probe(write_wim(tmp_path / "install.esd"))

    assert caught.value.code is ErrorCode.INPUT_FRAGMENTED
    assert "共 3 片" in caught.value.message


def test_encrypted_esd_is_refused(tmp_path: Path) -> None:
    def _raise(_path: Path) -> WimInfo:
        raise WimEncryptedError("该映像已加密，本工具不解密。请提供未加密的 ESD、WIM 或 ISO。")

    with pytest.raises(InputError) as caught:
        InputProber(wim_info=_raise).probe(write_wim(tmp_path / "install.esd"))

    assert caught.value.code is ErrorCode.INPUT_ENCRYPTED
    assert "不解密" in caught.value.message


def test_multi_volume_swm_is_refused_with_guidance(tmp_path: Path) -> None:
    with pytest.raises(InputError) as caught:
        InputProber().probe(write_wim(tmp_path / "install.swm"))

    assert caught.value.code is ErrorCode.INPUT_MULTI_VOLUME
    assert "DISM /Export-Image" in caught.value.message


def test_non_windows_image_is_refused(tmp_path: Path) -> None:
    info = WimInfo(images=(WimImage(index=1, name="Ubuntu Live", edition="ubuntu"),))
    with pytest.raises(InputError) as caught:
        InputProber(wim_info=lambda _p: info).probe(write_wim(tmp_path / "other.wim"))

    assert caught.value.code is ErrorCode.INPUT_NOT_WINDOWS


def test_ambiguous_directory_lists_candidates(tmp_path: Path) -> None:
    write_iso(tmp_path / "win11.iso")
    write_wim(tmp_path / "install.wim")

    with pytest.raises(InputError) as caught:
        InputProber().probe(tmp_path)

    assert caught.value.code is ErrorCode.INPUT_AMBIGUOUS
    assert "win11.iso" in caught.value.message and "install.wim" in caught.value.message


def test_uup_folder_with_extra_image_is_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "uupdump.mk").write_text("x", encoding="utf-8")
    (tmp_path / "a.cab").write_bytes(b"CAB")
    write_iso(tmp_path / "extra.iso")

    with pytest.raises(InputError) as caught:
        InputProber().probe(tmp_path)
    assert caught.value.code is ErrorCode.INPUT_AMBIGUOUS


def test_uup_folder_is_accepted(tmp_path: Path) -> None:
    folder = tmp_path / "uup"
    folder.mkdir()
    (folder / "uupdump.mk").write_text("x", encoding="utf-8")
    (folder / "a.cab").write_bytes(b"CAB")

    report = InputProber().probe(folder)
    assert report.type is InputKind.UUP_FOLDER
    assert "editions_pending_convert" in report.warnings


def test_missing_path_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(InputError) as caught:
        InputProber().probe(tmp_path / "nope.iso")
    assert caught.value.code is ErrorCode.INPUT_UNREADABLE


def test_unknown_file_is_not_install_media(tmp_path: Path) -> None:
    path = tmp_path / "readme.txt"
    path.write_text("hi", encoding="utf-8")

    with pytest.raises(InputError) as caught:
        InputProber().probe(path)
    assert caught.value.code is ErrorCode.INPUT_NOT_INSTALL_MEDIA


def test_mislabeled_extension_is_reported(tmp_path: Path) -> None:
    report = InputProber(wim_info=lambda _p: wim_info("Windows 11 Pro")).probe(
        write_wim(tmp_path / "actually-wim.iso")
    )

    assert report.type is InputKind.WIM
    assert report.extension_mismatch is True
    assert any("extension_mismatch" in warning for warning in report.warnings)
