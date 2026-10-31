"""解码与分行门禁（进程 §4.1、§4.2）。

覆盖：回退链顺序、``\\r`` 重绘分行、64 KB 截断、无换行残留、非法字节的十六进制转储。
"""

from __future__ import annotations

import pytest

from uup2iso.infrastructure.process.decoder import (
    FALLBACK_CHAIN,
    HEX_DUMP_BYTES,
    MAX_LINE_BYTES,
    LineSplitter,
    decode_line,
    hex_dump,
)

# --- 解码回退链 -------------------------------------------------------------


def test_decode_prefers_tool_encoding() -> None:
    """wimlib 是 utf-8，正常中文不应触发回退。"""
    raw = "导出完成".encode()
    decoded = decode_line(raw, "utf-8")
    assert decoded.text == "导出完成"
    assert decoded.encoding == "utf-8"


def test_decode_falls_back_to_cp936_for_gbk_bytes() -> None:
    """DISM/7z/aria2c 输出 GBK；用 utf-8 首选时应回退到 cp936。"""
    raw = "操作成功完成".encode("gbk")
    decoded = decode_line(raw, "utf-8")
    assert decoded.text == "操作成功完成"
    assert decoded.encoding == "cp936"


def test_decode_falls_back_to_latin1_as_last_resort() -> None:
    """两档都失败时用 latin-1 兜底，保证「永不丢行」。"""
    raw = b"\xff\xfe\x80\x00"
    decoded = decode_line(raw, "utf-8")
    assert decoded.encoding == "latin-1"
    assert decoded.raw_size == 4


def test_fallback_chain_matches_spec() -> None:
    """进程 §4.2：utf-8 → cp936 → latin-1。"""
    assert FALLBACK_CHAIN == ("utf-8", "cp936", "latin-1")


def test_hex_dump_keeps_first_256_bytes() -> None:
    """进程 §4.2：错误详情保留首 256 字节的十六进制。"""
    raw = bytes(range(256)) + b"extra"
    dump = hex_dump(raw)
    assert len(dump) == HEX_DUMP_BYTES * 2
    assert dump.startswith("00010203")


# --- 分行 -------------------------------------------------------------------


def test_splitter_handles_crlf_and_lf() -> None:
    splitter = LineSplitter()
    lines = splitter.feed(b"a\r\nb\nc")
    assert lines == [(b"a", False), (b"b", False)]
    assert splitter.flush() == [(b"c", False)]


def test_splitter_treats_lone_cr_as_line_break() -> None:
    """DISM 的进度条靠 ``\\r`` 重绘，不按 \\r 切就永远收不到进度。"""
    splitter = LineSplitter()
    lines = splitter.feed(b"[==== 10% ====]\r[==== 20% ====]\r")
    assert [item[0] for item in lines] == [b"[==== 10% ====]", b"[==== 20% ====]"]


def test_splitter_drops_empty_segments() -> None:
    """连续 \\r 会产生空片段，记进日志只会是噪声。"""
    splitter = LineSplitter()
    assert splitter.feed(b"\r\r\r") == []


def test_splitter_truncates_oversized_line() -> None:
    """进程 §4.1：超 64 KB 的行截断并标记 truncated。"""
    splitter = LineSplitter()
    payload = b"x" * (MAX_LINE_BYTES + 10)
    first = splitter.feed(payload)
    assert first and first[0][1] is True
    assert len(first[0][0]) == MAX_LINE_BYTES


def test_splitter_flushes_partial_tail() -> None:
    """流结束前无换行的残留数据作为最后一行（partial）。"""
    splitter = LineSplitter()
    assert splitter.feed(b"tail-without-newline") == []
    assert splitter.flush() == [(b"tail-without-newline", False)]


def test_splitter_survives_chunk_boundary() -> None:
    """``\\r`` 与 ``\\n`` 被拆到两个 chunk 时不能多切出一行空记录。"""
    splitter = LineSplitter()
    assert splitter.feed(b"line1\r") == [(b"line1", False)]
    assert splitter.feed(b"\nline2\n") == [(b"line2", False)]


@pytest.mark.parametrize("size", [1, 2, 4096])
def test_splitter_handles_odd_chunk_sizes(size: int) -> None:
    payload = b"a" * size + b"\n"
    splitter = LineSplitter()
    out = []
    for index in range(0, len(payload), size):
        out.extend(splitter.feed(payload[index : index + size]))
    assert out == [(b"a" * size, False)]
