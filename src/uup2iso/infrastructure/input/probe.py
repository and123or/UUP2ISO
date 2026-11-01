"""输入探测：把候选收敛成一个可用输入，或明确拒绝（输入 §2.3、§3）。

原则：**多候选不猜**（`INPUT_AMBIGUOUS` 列出全部候选）、**能拒绝就早拒绝**
（加密 ESD / 多卷 SWM / 分片 ESD 一律不尝试、不产半成品）。

`probe.json` 的结构见输入 §4；本模块只产出数据，落盘由 `Prepare` 阶段负责。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from uup2iso.core.errors import ErrorCode, InputError
from uup2iso.infrastructure.input.sniff import (
    InputKind,
    SniffResult,
    sniff_path,
)
from uup2iso.infrastructure.input.wiminfo import WimImage, WimInfo

__all__ = [
    "InputCandidate",
    "InputProber",
    "ProbeReport",
    "SWM_MERGE_HINT",
]

SWM_MERGE_HINT = (
    "该输入为多卷 SWM（分卷映像），请先合并为单一 WIM：\n"
    "  DISM /Export-Image /SourceImageFile:install.swm /SourceIndex:1 "
    "/DestinationImageFile:install.wim /Compress:max\n"
    "（若为 set 的后续分卷，需按 /SWMFile 参数逐个提供）"
)
"""多卷 SWM 的合并指引（输入 §3.2，原文照搬）。"""

_PROBE_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class InputCandidate:
    """候选输入（歧义时列给用户看）。"""

    kind: InputKind
    path: Path
    size_bytes: int = 0
    evidence: str = ""
    images: tuple[WimImage, ...] = ()

    def describe(self) -> str:
        edition = f"，{len(self.images)} 个 edition" if self.images else ""
        return f"{self.kind.value}  {self.path}{edition}"


@dataclass(frozen=True, slots=True)
class ProbeReport:
    """探测结果（可序列化为 `probe.json`）。"""

    type: InputKind
    path: Path
    candidates: tuple[InputCandidate, ...] = ()
    images: tuple[WimImage, ...] = ()
    extension_mismatch: bool = False
    encrypted: bool = False
    multi_volume: bool = False
    fragmented: bool = False
    bootable: bool = False
    read_only_source: bool = True
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_probe_json(self, *, detected_at: datetime | None = None) -> dict[str, object]:
        """按输入 §4 的结构输出。"""
        return {
            "schemaVersion": _PROBE_SCHEMA_VERSION,
            "detectedAt": (detected_at or datetime.now().astimezone()).isoformat(),
            "type": self.type.value,
            "path": str(self.path),
            "extensionMismatch": self.extension_mismatch,
            "encrypted": self.encrypted,
            "multiVolume": self.multi_volume,
            "fragmented": self.fragmented,
            "readOnlySource": self.read_only_source,
            "images": [
                {
                    "index": image.index,
                    "name": image.name,
                    "description": image.description,
                    "edition": image.edition,
                    "arch": image.arch,
                    "lang": image.lang,
                    "sizeBytes": image.size_bytes,
                }
                for image in self.images
            ],
            "bootable": self.bootable,
            "warnings": list(self.warnings),
        }


class InputProber:
    """输入探测器。

    Args:
        wim_info: 读取 WIM/ESD 信息的函数（通常包装 `wimlib-imagex info`）；
            ``None`` 表示不判定加密/分片/edition，只做头嗅探（测试与降级场景）。
    """

    def __init__(self, *, wim_info: Callable[[Path], WimInfo] | None = None) -> None:
        self._wim_info = wim_info

    def probe(self, path: Path) -> ProbeReport:
        """探测输入。

        Raises:
            InputError: 具体码见 :mod:`uup2iso.core.errors` 的 ``INPUT_*``。
        """
        candidates = sniff_path(path)
        if not candidates:
            if not path.exists():
                raise InputError(f"输入路径不存在：{path}", code=ErrorCode.INPUT_UNREADABLE)
            raise InputError(
                f"未在 {path} 中识别到可用的 Windows 映像（需要 ISO/WIM/ESD 或 UUP 文件夹）",
                code=ErrorCode.INPUT_NOT_INSTALL_MEDIA,
            )

        if len(candidates) > 1:
            raise InputError(
                "该路径下存在多个候选输入，请指定具体文件或目录：\n  "
                + "\n  ".join(item.path.name for item in candidates),
                code=ErrorCode.INPUT_AMBIGUOUS,
            )

        return self._resolve(candidates[0], candidates)

    # -- 内部 -------------------------------------------------------------

    def _resolve(
        self, candidate: SniffResult, all_candidates: tuple[SniffResult, ...]
    ) -> ProbeReport:
        kind = candidate.kind
        warnings = list(candidate.warnings)

        if kind is InputKind.UNKNOWN:
            raise InputError(
                f"无法识别输入类型：{candidate.path}（文件头既不是 WIM 也不是 ISO9660）",
                code=ErrorCode.INPUT_NOT_INSTALL_MEDIA,
            )

        # 多卷 SWM：拒绝 + 合并指引（输入 §3.2）
        if kind is InputKind.SWM:
            raise InputError(SWM_MERGE_HINT, code=ErrorCode.INPUT_MULTI_VOLUME)

        images: tuple[WimImage, ...] = ()
        encrypted = False
        fragmented = False

        if kind in (InputKind.WIM, InputKind.ESD) and self._wim_info is not None:
            info = self._wim_info(candidate.path)  # 加密/损坏在这里抛 InputError
            images = info.images
            fragmented = info.fragmented
            if fragmented:
                raise InputError(
                    f"该 ESD 为非独立分片映像（第 {info.part_number} 片 / 共 {info.part_total} 片），"
                    "无法单独导出；请改用完整 ISO 或组件聚合路径。",
                    code=ErrorCode.INPUT_FRAGMENTED,
                )
            if info.is_lzms and kind is InputKind.WIM:
                kind = InputKind.ESD  # LZMS 即 ESD（输入 §2.2 ③）
                warnings.append("type_refined detected=esd reason=lzms")
            _ensure_windows_images(images)
            for image in images:
                if image.edition == "unknown":
                    warnings.append(f"edition_unknown index={image.index} name={image.name}")
        elif kind is InputKind.ISO:
            warnings.append("images_pending_extract")
            warnings.append("bootable_unknown")
        elif kind is InputKind.UUP_FOLDER:
            warnings.append("editions_pending_convert")

        return ProbeReport(
            type=kind,
            path=candidate.path,
            candidates=tuple(_as_candidate(item) for item in all_candidates),
            images=images,
            extension_mismatch=candidate.extension_mismatch,
            encrypted=encrypted,
            multi_volume=False,
            fragmented=fragmented,
            bootable=False if kind is InputKind.ISO else bool(images),
            warnings=tuple(dict.fromkeys(warnings)),
        )


def _as_candidate(sniffed: SniffResult) -> InputCandidate:
    return InputCandidate(
        kind=sniffed.kind,
        path=sniffed.path,
        size_bytes=sniffed.size_bytes,
        evidence=sniffed.evidence,
    )


def _ensure_windows_images(images: tuple[WimImage, ...]) -> None:
    """非 Windows 映像（输入 §3.4）。"""
    if not images:
        return
    if all("windows" not in image.name.lower() for image in images):
        raise InputError(
            "该映像不是 Windows 安装映像（索引名称中未出现 Windows）",
            code=ErrorCode.INPUT_NOT_WINDOWS,
        )
