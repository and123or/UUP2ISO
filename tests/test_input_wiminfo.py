"""`wimlib info` 解析与 edition 归一化门禁（输入 §4、§5）。"""

from __future__ import annotations

import pytest

from uup2iso.core.errors import ErrorCode
from uup2iso.infrastructure.input import (
    WimEncryptedError,
    WimInfoError,
    classify_wimlib_failure,
    normalize_edition,
    parse_wiminfo,
)

XML_TWO_EDITIONS = """<?xml version="1.0" encoding="UTF-8"?>
<WIM>
  <TOTALBYTES>9000000000</TOTALBYTES>
  <COMPRESSION>LZX</COMPRESSION>
  <IMAGE INDEX="1">
    <NAME>Windows 11 Home</NAME>
    <DESCRIPTION>Windows 11 Home</DESCRIPTION>
    <TOTALBYTES>4500000000</TOTALBYTES>
  </IMAGE>
  <IMAGE INDEX="2">
    <NAME>Windows 11 Pro</NAME>
    <DESCRIPTION>Windows 11 Pro</DESCRIPTION>
    <TOTALBYTES>4500000000</TOTALBYTES>
  </IMAGE>
  <PART NUMBER="1" TOTAL="1"/>
</WIM>
"""

TEXT_INFO = """WIM Information:
----------------
Path:           D:\\ISO\\sources\\install.wim
GUID:           0x1234
Version:        0x10d00
Image Count:    2
Compression:    LZX
Part Number:    1 of 2
Boot Index:     1

Index:                  1
Name:                   Windows 11 Home
Description:            Windows 11 Home
Size:                   4,500,000,000 bytes

Index:                  2
Name:                   Windows 11 Pro
Description:            Windows 11 Pro
Size:                   4,600,000,000 bytes
"""


def test_xml_parses_all_editions() -> None:
    info = parse_wiminfo(XML_TWO_EDITIONS)

    assert info.image_count == 2
    assert [image.index for image in info.images] == [1, 2]
    assert [image.edition for image in info.images] == ["core", "professional"]
    assert info.total_bytes == 9_000_000_000
    assert info.fragmented is False


def test_xml_part_total_marks_fragmented() -> None:
    xml = XML_TWO_EDITIONS.replace('TOTAL="1"', 'TOTAL="3"')
    info = parse_wiminfo(xml)
    assert info.part_total == 3
    assert info.fragmented is True


def test_lzms_is_recognized_as_esd_compression() -> None:
    xml = XML_TWO_EDITIONS.replace(
        "<COMPRESSION>LZX</COMPRESSION>", "<COMPRESSION>LZMS</COMPRESSION>"
    )
    assert parse_wiminfo(xml).is_lzms is True


def test_text_output_parses_images_and_part() -> None:
    info = parse_wiminfo(TEXT_INFO)

    assert info.image_count == 2
    assert info.images[1].name == "Windows 11 Pro"
    assert info.images[1].size_bytes == 4_600_000_000
    assert info.compression == "LZX"
    assert (info.part_number, info.part_total) == (1, 2)
    assert info.fragmented is True
    assert info.boot_index == 1


def test_empty_output_is_rejected() -> None:
    with pytest.raises(WimInfoError) as caught:
        parse_wiminfo("no indexes here")
    assert caught.value.code is ErrorCode.INPUT_DAMAGED


def test_broken_xml_is_rejected() -> None:
    with pytest.raises(WimInfoError):
        parse_wiminfo("<WIM><IMAGE")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Windows 11 Pro", "professional"),
        ("Windows 11 Home", "core"),
        ("Windows 11 Enterprise N", "enterprisen"),
        ("Windows 10 Pro for Workstations", "professionalworkstation"),
        ("  Windows 11 Education  ", "education"),
        ("Some Custom Build", "somecustombuild"),
    ],
)
def test_normalize_edition(name: str, expected: str) -> None:
    assert normalize_edition(name) == expected


def test_encrypted_error_is_classified() -> None:
    error = classify_wimlib_failure("ERROR: The WIM is encrypted")
    assert isinstance(error, WimEncryptedError)
    assert error.code is ErrorCode.INPUT_ENCRYPTED


def test_other_failures_are_damaged() -> None:
    error = classify_wimlib_failure("ERROR: Invalid magic in header")
    assert error.code is ErrorCode.INPUT_DAMAGED
