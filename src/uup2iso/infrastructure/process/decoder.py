"""输出解码与分行（进程 §4.1、§4.2）。

两个职责：

1. **分行**：把字节流切成「行」，同时兼容 ``\\n``、``\\r\\n`` 与**只用 ``\\r`` 重绘**的
   进度行（DISM / wimlib 的进度条就是这样）；超长行按 64 KB 截断并标记。
2. **解码**：先按工具默认编码解，失败后按 ``utf-8 → cp936 → latin-1`` 回退；
   实际用的那一档要写进事件（``encoding=``），便于发现编码配置错误。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "FALLBACK_CHAIN",
    "HEX_DUMP_BYTES",
    "MAX_LINE_BYTES",
    "DecodedLine",
    "LineSplitter",
    "decode_line",
    "hex_dump",
]

MAX_LINE_BYTES = 64 * 1024
"""单行上限（进程 §4.1：超过即截断并标 ``truncated=true``）。"""

HEX_DUMP_BYTES = 256
"""错误详情里保留的首段原始字节（进程 §4.2）。"""

FALLBACK_CHAIN: tuple[str, ...] = ("utf-8", "cp936", "latin-1")
"""解码回退链；``latin-1`` 永不失败（可能乱码，但保证不丢行）。"""

_UTF8_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True, slots=True)
class DecodedLine:
    """一行解码结果。

    Attributes:
        text: 解码后的文本。
        encoding: 实际使用的编码名。
        truncated: 是否因超长被截断。
        raw_size: 原始字节数（截断时仍为真实长度）。
    """

    text: str
    encoding: str
    truncated: bool
    raw_size: int


def decode_line(raw: bytes, preferred: str) -> DecodedLine:
    """按「首选 → utf-8 → cp936 → latin-1」解码一行。

    Args:
        raw: 原始字节（不含换行符）。
        preferred: 该工具的默认编码（见 :data:`uup2iso.infrastructure.process.profiles`）。

    Returns:
        解码结果；``encoding`` 即实际生效的那一档。
    """
    candidates = [preferred]
    candidates.extend(item for item in FALLBACK_CHAIN if item != preferred)

    for encoding in candidates:
        try:
            return DecodedLine(raw.decode(encoding), encoding, False, len(raw))
        except (UnicodeDecodeError, LookupError):
            continue

    # 理论上不可达：latin-1 能解码任意字节。保留兜底以满足「永不失败」的契约。
    return DecodedLine(raw.decode("latin-1", errors="replace"), "latin-1", False, len(raw))


def hex_dump(raw: bytes, limit: int = HEX_DUMP_BYTES) -> str:
    """取首段字节的十六进制转储，用于定位非法字节（进程 §4.2）。"""
    return raw[:limit].hex()


class LineSplitter:
    """把字节流切成行，兼容 ``\\r`` 重绘与超长行。

    行结束符按 ``\\r\\n`` > ``\\n`` > ``\\r`` 的优先级处理；空行被丢弃
    （工具常靠连续 ``\\r`` 重绘，产生大量空片段，记进日志只会是噪声）。
    """

    def __init__(self, max_line_bytes: int = MAX_LINE_BYTES) -> None:
        self._buffer = bytearray()
        self._max = max_line_bytes
        self._overflowed = False

    def feed(self, chunk: bytes) -> list[tuple[bytes, bool]]:
        """喂入一段字节，返回本次能切出的完整行。

        Returns:
            ``(行字节, 是否被截断)`` 列表。
        """
        if not chunk:
            return []

        self._buffer.extend(chunk)
        lines: list[tuple[bytes, bool]] = []
        start = 0
        index = 0
        size = len(self._buffer)

        while index < size:
            byte = self._buffer[index]
            if byte not in (0x0A, 0x0D):
                index += 1
                continue

            payload = bytes(self._buffer[start:index])
            # 长度上限对「带换行的长行」同样生效，否则 70 KB 的单行会整段进内存与日志
            truncated = self._overflowed or len(payload) > self._max
            if len(payload) > self._max:
                payload = payload[: self._max]
            self._overflowed = False
            lines.append((payload, truncated))

            # \r\n 视作一个换行
            if byte == 0x0D and index + 1 < size and self._buffer[index + 1] == 0x0A:
                index += 2
            else:
                index += 1
            start = index

        del self._buffer[:start]
        if len(self._buffer) > self._max:
            # 无换行的超长内容（二进制噪声）：先截断吐出一段，避免内存膨胀
            lines.append((bytes(self._buffer[: self._max]), True))
            del self._buffer[: self._max]
            self._overflowed = True

        return [(payload, truncated) for payload, truncated in lines if payload]

    def flush(self) -> list[tuple[bytes, bool]]:
        """流结束时取出残留数据（无换行的最后一行，进程 §4.1 ``partial=true``）。"""
        if not self._buffer:
            return []
        payload = bytes(self._buffer)
        truncated = self._overflowed or len(payload) > self._max
        if len(payload) > self._max:
            payload = payload[: self._max]
        self._buffer.clear()
        self._overflowed = False
        return [(payload, truncated)] if payload else []
