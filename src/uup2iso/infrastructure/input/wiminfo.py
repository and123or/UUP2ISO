"""`wimlib info` 输出解析与 edition 归一化（输入 §4、§5）。

两种输入形态都吃：

* **XML**（`wimlib-imagex info --xml`）——结构稳定，首选；
* **文本**（普通 `info`）——当没有 XML 时的兜底，按 `Index:/Name:/Size:` 分块解析。

分片判定用 `<PART NUMBER=.. TOTAL=..>` 或文本里的 `Part Number: 1 of 2`；
加密映像 `wimlib` 会直接失败，错误信息里带 ``encrypted``，由 :func:`classify_wimlib_failure` 归类。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from uup2iso.core.errors import ErrorCode, InputError

__all__ = [
    "WimEncryptedError",
    "WimImage",
    "WimInfo",
    "WimInfoError",
    "classify_wimlib_failure",
    "normalize_edition",
    "parse_wiminfo",
]

_EDITION_ALIASES: dict[str, str] = {
    "windows 11 pro": "professional",
    "windows 11 home": "core",
    "windows 11 education": "education",
    "windows 11 enterprise": "enterprise",
    "windows 10 pro": "professional",
    "windows 10 home": "core",
    "windows 10 education": "education",
    "windows 10 enterprise": "enterprise",
    "windows 11 pro n": "professionaln",
    "windows 11 enterprise n": "enterprisen",
    "windows 11 pro education": "professionaleducation",
    "windows 11 pro for workstations": "professionalworkstation",
}
"""已知 edition 名称映射；未命中时退化为「去前缀 + 去空格」的小写形式。"""


_POST_PREFIX_ALIASES: dict[str, str] = {
    "pro": "professional",
    "home": "core",
    "education": "education",
    "enterprise": "enterprise",
    "pro n": "professionaln",
    "enterprise n": "enterprisen",
    "pro for workstations": "professionalworkstation",
    "pro education": "professionaleducation",
    "home single language": "coresinglelanguage",
}
"""去掉 ``Windows 10/11`` 前缀后的常见写法（比整名映射更耐版本变化）。"""


class WimInfoError(InputError):
    """`wimlib info` 输出无法解析 / 映像已损坏。"""

    default_code = ErrorCode.INPUT_DAMAGED


class WimEncryptedError(WimInfoError):
    """加密 ESD（输入 §3.1：明确拒绝，不解密）。"""

    default_code = ErrorCode.INPUT_ENCRYPTED


@dataclass(frozen=True, slots=True)
class WimImage:
    """一个索引（edition）。"""

    index: int
    name: str
    description: str = ""
    edition: str = "unknown"
    arch: str = ""
    lang: str = ""
    size_bytes: int = 0


@dataclass(frozen=True, slots=True)
class WimInfo:
    """整份 `wimlib info` 的摘要。"""

    images: tuple[WimImage, ...] = field(default_factory=tuple)
    total_bytes: int = 0
    compression: str = ""
    part_number: int = 1
    part_total: int = 1
    boot_index: int = 0

    @property
    def image_count(self) -> int:
        return len(self.images)

    @property
    def fragmented(self) -> bool:
        """非独立分片（输入 §3.3）。"""
        return self.part_total > 1

    @property
    def is_lzms(self) -> bool:
        return self.compression.upper().startswith("LZMS")


def parse_wiminfo(text: str) -> WimInfo:
    """解析 `wimlib info` 输出（XML 或文本）。

    Raises:
        WimInfoError: 两种形态都解析不出任何索引。
    """
    stripped = text.lstrip()
    if stripped.startswith("<"):
        return _parse_xml(stripped)
    return _parse_text(text)


def classify_wimlib_failure(message: str) -> InputError:
    """把 wimlib 的失败信息归类成具体的输入错误（输入 §3.1/§3.3）。"""
    lowered = message.lower()
    if "encrypt" in lowered or "0x8007177a" in lowered:
        return WimEncryptedError(
            "该映像已加密，本工具不解密。请提供未加密的 ESD、WIM 或 ISO。",
        )
    if "part" in lowered and "of" in lowered:
        return WimInfoError(
            "该 ESD 为非独立分片映像，无法单独导出。", code=ErrorCode.INPUT_FRAGMENTED
        )
    return WimInfoError(f"无法读取映像信息：{message}")


def normalize_edition(name: str) -> str:
    """把 `Name` 归一化成 edition 标识（小写、去前缀、去空格）。"""
    key = re.sub(r"\s+", " ", name.strip().lower())
    if key in _EDITION_ALIASES:
        return _EDITION_ALIASES[key]

    trimmed = re.sub(r"^windows (?:10|11|server)\s+", "", key)
    if trimmed in _POST_PREFIX_ALIASES:
        return _POST_PREFIX_ALIASES[trimmed]
    compact = trimmed.replace(" ", "")
    return compact or "unknown"


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------


def _parse_xml(text: str) -> WimInfo:
    try:
        root = etree.fromstring(text.encode("utf-8"))
    except etree.XMLSyntaxError as exc:
        raise WimInfoError(f"wimlib info XML 解析失败：{exc}") from exc

    images: list[WimImage] = []
    for element in root.iter("IMAGE"):
        index = _safe_int(element.get("INDEX")) or len(images) + 1
        name = _child_text(element, "NAME") or _child_text(element, "DESCRIPTION")
        images.append(
            WimImage(
                index=index,
                name=name,
                description=_child_text(element, "DESCRIPTION"),
                edition=normalize_edition(name),
                size_bytes=_safe_int(_child_text(element, "TOTALBYTES")),
            )
        )

    if not images:
        raise WimInfoError("wimlib info 输出里没有任何索引")

    part = root.find("PART")
    return WimInfo(
        images=tuple(images),
        total_bytes=_safe_int(_child_text(root, "TOTALBYTES")),
        compression=_child_text(root, "COMPRESSION") or _child_text(root, "COMPRESSIONTYPE"),
        part_number=_safe_int(part.get("NUMBER")) if part is not None else 1,
        part_total=_safe_int(part.get("TOTAL")) if part is not None else 1,
        boot_index=_safe_int(_child_text(root, "BOOTINDEX")),
    )


def _child_text(element: etree._Element, tag: str) -> str:
    child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _safe_int(value: object) -> int:
    if value is None:
        return 0
    text = str(value).replace(",", "").strip()
    return int(text) if text.lstrip("-").isdigit() else 0


# ---------------------------------------------------------------------------
# 文本
# ---------------------------------------------------------------------------

_INDEX_LINE = re.compile(r"^Index:\s*(\d+)", re.MULTILINE)
_FIELD = re.compile(r"^(?P<key>[A-Za-z][A-Za-z ]*?):\s+(?P<value>.*)$")


def _parse_text(text: str) -> WimInfo:
    blocks = _INDEX_LINE.split(text)
    images: list[WimImage] = []

    # split 后形如 ['前言', '1', '块内容', '2', '块内容', ...]
    for offset in range(1, len(blocks) - 1, 2):
        index = int(blocks[offset])
        body = blocks[offset + 1]
        fields: dict[str, str] = {}
        for line in body.splitlines():
            match = _FIELD.match(line.strip())
            if match:
                fields[match.group("key").lower()] = match.group("value").strip()
        name = fields.get("name", "") or fields.get("description", "")
        images.append(
            WimImage(
                index=index,
                name=name,
                description=fields.get("description", ""),
                edition=normalize_edition(name),
                size_bytes=_safe_int(fields.get("size", "").split(" bytes")[0]),
            )
        )

    if not images:
        raise WimInfoError("wimlib info 输出里没有任何索引")

    part_number, part_total = _parse_part(text)
    compression = _parse_label(text, "Compression")
    return WimInfo(
        images=tuple(images),
        compression=compression,
        part_number=part_number,
        part_total=part_total,
        boot_index=_safe_int(_parse_label(text, "Boot Index")),
    )


def _parse_part(text: str) -> tuple[int, int]:
    match = re.search(r"Part Number:\s*(\d+)\s*of\s*(\d+)", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 1, 1


def _parse_label(text: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def load_wiminfo_xml(path: Path) -> WimInfo:  # pragma: no cover - 需真实工具
    """读取预先生成的 XML 文件（供调试与联调用）。"""
    return parse_wiminfo(path.read_text(encoding="utf-8", errors="replace"))
