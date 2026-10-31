"""下载契约 `IDownloader`（下载 §4–§5；需求 §7.3）。

实现 `Aria2Downloader` 在 `infrastructure/download/`，归 WP2-T6/T7。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from uup2iso.core.abstractions.runtime import CancelToken, ProgressSink
from uup2iso.core.models.download import DownloadReport
from uup2iso.core.models.manifest import Manifest
from uup2iso.core.options.execution import DownloadOptions

__all__ = ["IDownloader"]


@runtime_checkable
class IDownloader(Protocol):
    """按清单下载并校验文件。

    行为约束：

    * 只走 ``aria2c``，不自行实现 HTTP 下载（下载 §4.1）；
    * 校验顺序为「先比大小，再流式算 SHA1」（下载 §5.1，快速失败）；
    * SHA1 不符时删文件重下，**最多 2 次**；仍不符即失败并列出期望/实际哈希，
      **绝不忽略哈希**（下载 §5.2）；
    * 进度只经 :class:`~uup2iso.core.abstractions.runtime.ProgressSink` 上报，
      不进日志（日志 §5.2）。
    """

    async def download(
        self,
        manifest: Manifest,
        options: DownloadOptions,
        *,
        target_dir: Path,
        progress: ProgressSink | None = None,
        cancel: CancelToken | None = None,
    ) -> DownloadReport:
        """下载清单中的全部文件并逐个校验。

        Args:
            manifest: 已通过自校验的清单（下载 §3.2）。
            options: aria2c 参数。
            target_dir: 落盘目录（``work\\<task_id>\\uup\\``）。
            progress: 进度接收端；``None`` 表示不关心进度。
            cancel: 取消令牌；``None`` 表示不可取消（仅测试或短任务）。

        Returns:
            每个文件的结局；调用方据 :attr:`DownloadReport.is_complete` 判定是否继续。

        Raises:
            ToolMissingError: aria2c 不可用且自动获取失败（``TOOL_MISSING``）。
            DownloadCancelled: 被取消（退出码 3，保留已下载部分）。
        """
        ...
