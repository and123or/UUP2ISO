"""执行请求（execution request）DTO —— 接口层唯一接受的参数形态。

**与 `TaskConfig` 的分工**（决策 D4）：

* `TaskConfig` 是**用户意图的持久化契约**（配置 §2–§3），只在编排层流动；
* 本模块的 DTO 是**编排层翻译给某个能力的具体请求**，字段只保留该能力真正需要的部分。

这样 `core.abstractions` 不必反向依赖配置模型，替换实现也更容易（ISP）。
字段取值与范围以注释标注的规范章节为准。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from uup2iso.core.models.enums import CompressionKind

__all__ = [
    "DownloadOptions",
    "ExportRequest",
    "IsoBuildRequest",
    "MountRequest",
    "OptimizeRequest",
]


@dataclass(frozen=True, slots=True)
class DownloadOptions:
    """aria2c 下载参数（下载 §4.2 参数契约）。

    并发数不开放配置：固定单文件并发 1（进程 §8），这里保留字段只为与
    aria2c 命令行一一对应，调用方不应改写。
    """

    max_tries: int = 3
    retry_wait_seconds: int = 2
    max_concurrent_downloads: int = 1
    connections_per_server: int = 8
    continue_partial: bool = True
    proxy: str | None = None
    speed_limit_kbps: int | None = None
    timeout_seconds: int = 60


@dataclass(frozen=True, slots=True)
class ExportRequest:
    """导出/转换请求（引擎 §3.2 R2/R3：``wimlib export`` / ``DISM /Export-Image``）。

    Attributes:
        source: 源映像路径（WIM/ESD）。
        target: 目标映像路径。
        index: 要导出的索引号；``None`` 表示全部索引。
        compression: 目标压缩类型；``LZMS`` 在保守模式下必须被调用方拦下（引擎 §5）。
        verify_after: 导出后是否立即做全文件校验（引擎 R5）。
    """

    source: Path
    target: Path
    index: int | None = None
    compression: CompressionKind = CompressionKind.LZX
    verify_after: bool = False


@dataclass(frozen=True, slots=True)
class MountRequest:
    """挂载请求（引擎 §3.2 R6：仅 DISM 具备挂载语义）。

    Attributes:
        image: 映像路径。
        mount_dir: 挂载点目录，须为空目录。
        index: 索引号。
        read_only: 是否只读挂载；需要改包/清理时必须为 ``False``。
    """

    image: Path
    mount_dir: Path
    index: int = 1
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class IsoBuildRequest:
    """ISO 封装请求（引擎 §3.2 R10：``oscdimg``）。

    Attributes:
        media_root: 媒体骨架根目录（含 ``sources``、``boot`` 等）。
        output: 输出 ISO 路径。
        volume_label: 卷标；``None`` 表示用默认值。
        boot_mode: 引导模式，``"dual"``（默认，UEFI+BIOS）/ ``"uefi"`` / ``"bios"``（详设 CV-28）。
        boot_files: 引导文件相对路径；``None`` 表示由实现按 ``boot_mode`` 取默认组合。
    """

    media_root: Path
    output: Path
    volume_label: str | None = None
    boot_mode: str = "dual"
    boot_files: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class OptimizeRequest:
    """精简/优化规则执行请求（规则 §5）。

    Attributes:
        rule_ids: 要执行的规则 id，**有序**；调用方保证已按 `order` + 依赖拓扑排好（规则 §3）。
        params: 规则参数覆盖（``ruleId → 参数``）；缺省表示用规则自带默认值。
        strict_context: 上下文不满足时是否判失败（规则 §5；默认只跳过并记录）。
    """

    rule_ids: tuple[str, ...]
    params: dict[str, dict[str, object]] = field(default_factory=dict)
    strict_context: bool = False
