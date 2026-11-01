"""重试策略（编排 §7）。

只按**白名单**重试——这一点比"多重试几次"重要得多：

* 可重试：网络超时/断流、文件占用、工具瞬时失败、校验不通过；
* **不可重试**：`CONFIG_*`（用户必须先改配置）、`INPUT_*`（确定性失败）、`DISK_FULL`、
  `OUTPUT_CROSS_VOLUME`、转换器非占用类失败、以及用户取消。

阶段级兜底 2 次（每次 30s 退避）；子步骤级的 3 次重试由各阶段实现（下载器 / 引擎）负责。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from uup2iso.core.errors import ErrorCode, UserCancelledError, Uup2IsoError

__all__ = ["DEFAULT_RETRYABLE_CODES", "RetryPolicy", "is_retryable"]

DEFAULT_RETRYABLE_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.SOURCE_UNAVAILABLE,  # 网络超时/断流
        ErrorCode.SOURCE_RATE_LIMITED,  # 源限流，退避后可再试
        ErrorCode.TOOL_FAILED,  # 工具瞬时失败（非占用类）
    }
)
"""默认可重试的错误码（编排 §7.1 的前三行；校验不通过在子步骤层处理）。"""


def is_retryable(
    error: BaseException, codes: frozenset[ErrorCode] = DEFAULT_RETRYABLE_CODES
) -> bool:
    """该异常是否值得重试。

    用户取消**永不**重试（否则取消就失效了）；未知异常也不重试——盲目重试只会把
    不确定的问题变成"绕圈 3 次再失败"。
    """
    if isinstance(error, UserCancelledError):
        return False
    if isinstance(error, Uup2IsoError):
        return error.code in codes
    return False


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """阶段级重试配置。

    Attributes:
        stage_retries: 阶段最多重试几次（默认 2，编排 §7.1 的"阶段级兜底"）。
        backoff_seconds: 每次重试前的等待；不足时取最后一项。
        retryable_codes: 视为可重试的错误码。
        cleanup_before_retry: 重试前是否调用清理钩子（挂载类阶段要求）。
    """

    stage_retries: int = 2
    backoff_seconds: tuple[float, ...] = (30.0,)
    retryable_codes: frozenset[ErrorCode] = field(default=DEFAULT_RETRYABLE_CODES)
    cleanup_before_retry: bool = True

    def backoff_for(self, attempt: int) -> float:
        """第 ``attempt`` 次重试（1 起）前的等待秒数。"""
        if not self.backoff_seconds:
            return 0.0
        index = min(attempt - 1, len(self.backoff_seconds) - 1)
        return self.backoff_seconds[index]

    def accepts(self, error: BaseException) -> bool:
        return is_retryable(error, self.retryable_codes)
