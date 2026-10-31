"""工具画像：编码、IO 画像、并发资源（进程 §2.2、§4.2、§8）。

三个信息都来自同一张表，集中在此避免散落：

* ``encoding``：该工具输出的**默认**编码（解码失败再走回退链）；
* ``io_profile``：看门狗判定用（``network`` / ``disk`` / ``cpu``）；
* ``resource``：并发锁的资源名（镜像类共享 ``image``，下载独占 ``download``）。
"""

from __future__ import annotations

from dataclasses import dataclass

from uup2iso.core.models.enums import ToolKind

__all__ = [
    "RESOURCE_DOWNLOAD",
    "RESOURCE_IMAGE",
    "TOOL_PROFILES",
    "ToolProfile",
    "profile_for",
]

RESOURCE_IMAGE = "image"
"""镜像类资源名：`DISM` / `wimlib` / `7z` / `oscdimg` 共用，固定并发 1（进程 §8）。"""

RESOURCE_DOWNLOAD = "download"
"""下载资源名：`aria2c` 独占，固定并发 1。"""


@dataclass(frozen=True, slots=True)
class ToolProfile:
    """单个工具的运行画像。

    Attributes:
        kind: 工具种类。
        encoding: 默认输出编码（进程 §4.2：DISM/7z/aria2c/oscdimg → cp936，wimlib → utf-8）。
        io_profile: IO 画像集合，元素取自 ``network`` / ``disk`` / ``cpu``。
        resource: 并发锁资源名。
    """

    kind: ToolKind
    encoding: str
    io_profile: frozenset[str]
    resource: str

    @property
    def has_network(self) -> bool:
        """是否含网络画像；为真时看门狗才校验网络信号（进程 §7.2）。"""
        return "network" in self.io_profile


_DISK = frozenset({"disk"})
_DISK_CPU = frozenset({"disk", "cpu"})
_NET_DISK = frozenset({"network", "disk"})

TOOL_PROFILES: dict[ToolKind, ToolProfile] = {
    ToolKind.ARIA2C: ToolProfile(ToolKind.ARIA2C, "cp936", _NET_DISK, RESOURCE_DOWNLOAD),
    ToolKind.SEVEN_ZIP: ToolProfile(ToolKind.SEVEN_ZIP, "cp936", _DISK, RESOURCE_IMAGE),
    ToolKind.WIMLIB: ToolProfile(ToolKind.WIMLIB, "utf-8", _DISK, RESOURCE_IMAGE),
    ToolKind.DISM: ToolProfile(ToolKind.DISM, "cp936", _DISK_CPU, RESOURCE_IMAGE),
    ToolKind.OSCDIMG: ToolProfile(ToolKind.OSCDIMG, "cp936", _DISK_CPU, RESOURCE_IMAGE),
    ToolKind.CONVERTER: ToolProfile(ToolKind.CONVERTER, "cp936", _DISK_CPU, RESOURCE_IMAGE),
    ToolKind.REG: ToolProfile(ToolKind.REG, "cp936", _DISK, RESOURCE_IMAGE),
}

_DEFAULT = ToolProfile(ToolKind.DISM, "cp936", _DISK, RESOURCE_IMAGE)


def profile_for(kind: ToolKind) -> ToolProfile:
    """取工具画像；未登记的工具按「cp936 + disk + 镜像资源」保守处理。

    保守处理的含义：并发闸门仍生效（宁串行）、看门狗不校网络（宁漏报）。
    """
    return TOOL_PROFILES.get(kind, _DEFAULT)
