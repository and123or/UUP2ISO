"""映像领域模型 —— 对应 架构 §5 的 `ImageInfo` / `Edition`，字段对齐 输入 §4 与 §5。

`ImageInfo` 是 `Prepare.probe` 的产物在内存中的形状；落盘的 ``probe.json``
由 WP1-T4 负责序列化，两者字段一一对应（含 ``images[]`` ↔ ``editions``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from uup2iso.core.models.enums import Architecture, ImageType

__all__ = ["Edition", "ImageInfo"]

UNKNOWN_EDITION = "unknown"
"""无法归一化的 edition 名的占位值（输入 §5）。"""


@dataclass(frozen=True, slots=True)
class Edition:
    """映像中的一个索引（输入 §4 ``images[]`` / §5 枚举规则）。

    Attributes:
        index: WIM 索引号，从 1 开始；这是 ``TaskConfig.input.image.editions[]`` 的取值。
        name: 映像名，如 ``"Windows 11 Pro"``。
        edition: 归一化后的 edition 标识（小写）；无法归一化为 ``"unknown"``。
        description: 描述，可选。
        arch: 该索引的架构，可选（部分映像未写）。
        language: 该索引的语言标签，可选。
        size_bytes: 未压缩大小，可选。
    """

    index: int
    name: str
    edition: str = UNKNOWN_EDITION
    description: str | None = None
    arch: Architecture | None = None
    language: str | None = None
    size_bytes: int | None = None

    @property
    def is_unknown_edition(self) -> bool:
        """名称未能归一化；仍可按 :attr:`index` 选择，但需记 ``WARN edition_unknown``。"""
        return self.edition == UNKNOWN_EDITION


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """输入映像的探测结果（输入 §4）。

    Attributes:
        path: 映像路径（ISO/WIM/ESD/SWM，或入口 B 的 UUP 文件夹）。
        type: 嗅探得到的真实类型；扩展名仅作辅助（输入 §2.2）。
        editions: 全部索引，**必须完整**，供 GUI 多选（输入 §4）。
        encrypted: 是否加密 ESD；为真时明确拒绝（输入 §3.1）。
        multi_volume: 是否多卷 SWM；为真时拒绝并给合并指引（输入 §3.2）。
        fragmented: 是否分片/非独立 ESD；为真时拒绝（输入 §3.3）。
        extension_mismatch: 扩展名与真实类型不符（输入 §2.2，按真实类型处理）。
        read_only_source: 源是否只读引用（``Prepare`` 永不修改源，输入 §7.2）。
        bootable: 是否可引导；``None`` 表示未探测。
        warnings: 探测期告警（如 ``edition_unknown``），原样透传到日志与 UI。
    """

    path: Path
    type: ImageType
    editions: tuple[Edition, ...] = ()
    encrypted: bool = False
    multi_volume: bool = False
    fragmented: bool = False
    extension_mismatch: bool = False
    read_only_source: bool = True
    bootable: bool | None = None
    warnings: tuple[str, ...] = ()

    @property
    def is_rejected(self) -> bool:
        """是否命中「明确拒绝」规则（输入 §3.1–§3.3）。"""
        return self.encrypted or self.multi_volume or self.fragmented

    @property
    def is_install_media(self) -> bool:
        """是否为可用的安装介质：类型已识别、未被拒绝、至少一个索引。"""
        return self.type is not ImageType.UNKNOWN and not self.is_rejected and bool(self.editions)

    def edition_by_index(self, index: int) -> Edition | None:
        """按索引号取 edition；不存在返回 ``None``（配置语义校验据此判错）。"""
        return next((item for item in self.editions if item.index == index), None)

    def edition_indexes(self) -> tuple[int, ...]:
        """全部可用索引号，升序。"""
        return tuple(sorted(item.index for item in self.editions))
