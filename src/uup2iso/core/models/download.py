"""下载结果模型 —— 对应 下载 §5（校验）、§5.4（状态文件）、§7（失败清单）。

只描述「一次下载跑完之后的事实」；执行细节（aria2c 参数、重试）在 WP2-T6/T7。
"""

from __future__ import annotations

from dataclasses import dataclass

from uup2iso.core.models.enums import DownloadFileState

__all__ = ["DownloadFailure", "DownloadReport", "DownloadedFile"]


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    """单个文件的下载与校验结局。

    Attributes:
        name: 文件名。
        state: 状态，取值见 术语 §2.2。
        size_bytes: 实际落盘大小；失败时为 ``None``。
        expected_sha1: 清单里的期望 SHA1。
        actual_sha1: 实际算出的 SHA1；未算或文件缺失时为 ``None``。
        error: 失败原因（中性描述，供日志与 UI 展示）。
    """

    name: str
    state: DownloadFileState
    size_bytes: int | None = None
    expected_sha1: str | None = None
    actual_sha1: str | None = None
    error: str | None = None

    @property
    def is_ok(self) -> bool:
        """是否已通过校验（只有 ``verified`` 才能被续跑复用）。"""
        return self.state is DownloadFileState.VERIFIED


@dataclass(frozen=True, slots=True)
class DownloadFailure:
    """失败清单中的一项（下载 §7）。

    Attributes:
        name: 文件名。
        reason: 失败原因分类，如 ``hash_mismatch`` / ``missing`` / ``download_error``。
        expected: 期望值（通常是 SHA1）。
        actual: 实际值；缺失时为 ``None``。
    """

    name: str
    reason: str
    expected: str | None = None
    actual: str | None = None


@dataclass(frozen=True, slots=True)
class DownloadReport:
    """一次下载任务的汇总结果（写入 ``download.state.json`` 的对外视图）。

    Attributes:
        files: 每个文件的结局，顺序与清单一致。
        retry_rounds: 因哈希不符触发的重新下载轮数（下载 §5.2，上限 2 次）。
        elapsed_seconds: 总耗时；未知为 ``None``。
    """

    files: tuple[DownloadedFile, ...] = ()
    retry_rounds: int = 0
    elapsed_seconds: float | None = None

    @property
    def verified_files(self) -> tuple[DownloadedFile, ...]:
        """已通过校验的文件。"""
        return tuple(item for item in self.files if item.is_ok)

    @property
    def failures(self) -> tuple[DownloadFailure, ...]:
        """失败清单；按文件顺序，含期望/实际哈希以便定位（下载 §7）。"""
        return tuple(
            DownloadFailure(
                name=item.name,
                reason=item.error or "download_error",
                expected=item.expected_sha1,
                actual=item.actual_sha1,
            )
            for item in self.files
            if not item.is_ok
        )

    @property
    def is_complete(self) -> bool:
        """是否全部通过校验。"""
        return bool(self.files) and all(item.is_ok for item in self.files)

    @property
    def total_bytes(self) -> int:
        """已落盘字节数合计。"""
        return sum(item.size_bytes or 0 for item in self.files)
