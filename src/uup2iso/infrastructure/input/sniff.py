"""输入嗅探：**文件头优先于扩展名**（输入 §2.2）。

三条实现要点，都是踩过的坑：

1. **ISO 的 `CD001` 在偏移 32769**，读 32 KB 会正好差 6 字节而不命中，所以固定读 **64 KB**；
2. 扩展名只做「辅助与告警」——`.iso` 实为 WIM 时按 WIM 处理并记 `extension_mismatch`；
3. UUP 文件夹靠「清单文件 + 至少一个 `.cab/.esd/.msu`」判定，缺一不可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

__all__ = [
    "HEAD_SIZE",
    "ISO_OFFSET",
    "ISO_SIGNATURE",
    "UUP_MANIFEST_NAMES",
    "WIM_MAGIC",
    "InputKind",
    "SniffResult",
    "find_image_files",
    "is_uup_folder",
    "sniff_file",
    "sniff_path",
]

WIM_MAGIC = b"MSWIM\x00\x00\x00"
ISO_SIGNATURE = b"CD001"
ISO_OFFSET = 32769
HEAD_SIZE = 64 * 1024
"""固定读 64 KB（输入 §2.2：必须 ≥ 32774 字节）。"""

UUP_MANIFEST_NAMES: tuple[str, ...] = (
    "uupdump.mk",
    "files.txt",
    "uup-info.json",
    "ConvertConfig.ini",
)
"""UUP 文件夹的清单特征（命中任一即可，配合目录内的 `.cab/.esd/.msu`）。"""

_IMAGE_SUFFIXES = (".iso", ".wim", ".esd", ".swm")
_ARCHIVE_SUFFIXES = (".cab", ".esd", ".msu")

_EXT_EXPECT: dict[str, str] = {
    ".iso": "iso",
    ".wim": "wim",
    ".esd": "esd",
    ".swm": "swm",
}


class InputKind(StrEnum):
    """输入类型（输入 §2.1）。"""

    UUP_FOLDER = "uup_folder"
    ISO = "iso"
    WIM = "wim"
    ESD = "esd"
    SWM = "swm"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SniffResult:
    """一个候选输入。

    Attributes:
        kind: 判定出的类型。
        path: 输入路径。
        evidence: 判定依据（写日志用，例如 ``magic@0`` / ``iso@32769``）。
        extension_mismatch: 扩展名与真实类型不符。
        size_bytes: 文件大小（目录为 0）。
        head_read: 是否成功读到文件头。
    """

    kind: InputKind
    path: Path
    evidence: str = ""
    extension_mismatch: bool = False
    size_bytes: int = 0
    head_read: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)


def sniff_file(path: Path) -> SniffResult:
    """读文件头判定单文件类型（不做 edtion/加密判定，那些需要 wimlib）。"""
    suffix = path.suffix.lower()
    size = path.stat().st_size if path.is_file() else 0
    head = _read_head(path)
    mismatched = False

    if head.startswith(WIM_MAGIC):
        kind = InputKind.SWM if _looks_like_swm(path) else _wim_family_kind(suffix)
        evidence = "magic@0"
    elif head[ISO_OFFSET : ISO_OFFSET + len(ISO_SIGNATURE)] == ISO_SIGNATURE:
        kind = InputKind.ISO
        evidence = f"iso@${ISO_OFFSET}"
    else:
        kind = InputKind.UNKNOWN
        evidence = "no-signature"

    expected = _EXT_EXPECT.get(suffix)
    if expected and kind is not InputKind.UNKNOWN:
        # `.esd` 与 `wim` 同族：扩展名 .esd 不强求 kind=esd（LZMS 判定需要 wimlib）
        mismatched = expected != kind.value and not (
            expected in ("wim", "esd") and kind.value in ("wim", "esd")
        )

    warnings: list[str] = []
    if mismatched:
        warnings.append(f"extension_mismatch detected=true ext={suffix}")

    return SniffResult(
        kind=kind,
        path=path,
        evidence=evidence,
        extension_mismatch=mismatched,
        size_bytes=size,
        head_read=bool(head),
        warnings=tuple(warnings),
    )


def is_uup_folder(path: Path) -> tuple[bool, tuple[Path, ...]]:
    """目录是否为 UUP 文件夹；返回 ``(命中, 命中的证据文件)``。"""
    if not path.is_dir():
        return False, ()

    manifests = [path / name for name in UUP_MANIFEST_NAMES if (path / name).is_file()]
    if not manifests:
        manifests = sorted(path.glob("*.cmd"))[:1]
    archives = [
        item
        for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in _ARCHIVE_SUFFIXES
    ]
    if not archives:
        uups = path / "UUPs"
        if uups.is_dir():
            archives = [
                item
                for item in uups.iterdir()
                if item.is_file() and item.suffix.lower() in _ARCHIVE_SUFFIXES
            ]
    if manifests and archives:
        return True, (*manifests, archives[0])
    return False, ()


def find_image_files(directory: Path) -> tuple[Path, ...]:
    """目录顶层的映像文件（不递归，避免把解包树里的东西当成输入候选）。"""
    if not directory.is_dir():
        return ()
    return tuple(
        sorted(
            item
            for item in directory.iterdir()
            if item.is_file() and item.suffix.lower() in _IMAGE_SUFFIXES
        )
    )


def sniff_path(path: Path) -> tuple[SniffResult, ...]:
    """给任意路径返回**全部候选**（输入 §2.3：多候选不猜，交给上层报歧义）。"""
    if path.is_file():
        return (sniff_file(path),)

    if not path.is_dir():
        return ()

    candidates: list[SniffResult] = []
    uup, evidence_files = is_uup_folder(path)
    if uup:
        candidates.append(
            SniffResult(
                kind=InputKind.UUP_FOLDER,
                path=path,
                evidence="+".join(item.name for item in evidence_files[:2]),
                size_bytes=sum(item.stat().st_size for item in path.iterdir() if item.is_file()),
            )
        )
    for item in find_image_files(path):
        candidates.append(sniff_file(item))
    return tuple(candidates)


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _read_head(path: Path, size: int = HEAD_SIZE) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(size)
    except OSError:
        return b""


def _looks_like_swm(path: Path) -> bool:
    """多卷 SWM：文件名 `install*.swm`，或同目录存在编号兄弟分卷。"""
    if path.suffix.lower() != ".swm":
        return False
    if path.suffix.lower() == ".swm" and path.stem.lower().startswith("install"):
        return True
    parent = path.parent
    return any(parent.glob("install[0-9]*.swm"))


def _wim_family_kind(suffix: str) -> InputKind:
    """WIM 家族细分：`.esd` 先按 ESD 记，LZMS 的最终判定留给 wimlib（输入 §2.2 ③）。"""
    return InputKind.ESD if suffix == ".esd" else InputKind.WIM
