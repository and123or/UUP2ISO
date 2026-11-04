"""Ctrl+C → 取消令牌（G3）。

规范（进程 §7.1、编排 §8）：取消不是"杀进程"，而是**在安全点检查并退出**，
保留中间产物、退出码 3；清理是显式动作。

用法：

```python
token = SignalCancelToken()
with token.installed():
    await orchestrator.run(..., cancel=token)   # 编排器在阶段/子步骤边界检查
```

第二次 Ctrl+C 会**恢复默认处理**（用户明确要强退时不该被拦住）。
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

__all__ = ["SignalCancelToken"]


class SignalCancelToken:
    """把 SIGINT 转成可轮询的取消标志。"""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._hits = 0
        self._previous: Any = None

    # -- CancelToken 协议 --------------------------------------------------

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    # -- 主动取消（测试/程序内触发）---------------------------------------

    def cancel(self) -> None:
        self._event.set()

    # -- 信号安装 ---------------------------------------------------------

    @contextmanager
    def installed(self) -> Iterator[SignalCancelToken]:
        """在上下文内把 SIGINT 接到 ``cancel()``。"""
        try:
            self._previous = signal.signal(signal.SIGINT, self._handle)
        except (ValueError, OSError):
            # 非主线程（如 GUI 后台线程）：不接信号，仍可用 cancel() 主动取消
            self._previous = None
        try:
            yield self
        finally:
            if self._previous is not None:
                signal.signal(signal.SIGINT, self._previous)
                self._previous = None

    def _handle(self, signum: int, frame: object) -> None:  # noqa: ARG002 - 信号签名固定
        self._hits += 1
        if self._hits == 1:
            self._event.set()
            return
        # 第二次：恢复默认，让 Ctrl+C 真正终止
        if self._previous is not None:
            signal.signal(signum, self._previous)
        raise KeyboardInterrupt
