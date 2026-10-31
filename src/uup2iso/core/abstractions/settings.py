"""全局设置契约 `ISettings`（配置 §5；GUI-设置 §2）。

实现 `SettingsStore` 在 `infrastructure/storage/`，归 WP0-T6。

语义约束：

* 设置是**全局默认值**，任务配置优先于设置（`settings.defaults.*` 只影响新任务初始值）；
* 键名扁平化（``download.proxy``、``log.maxTotalMb``），不在此处固化 schema——
  schema 归 WP0-T6 的 `schemas/` 与 `AppSettings` 模型；
* 读取必须**不抛错**：键不存在返回默认值，避免一处拼写错误拖垮启动。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

__all__ = ["ISettings"]


@runtime_checkable
class ISettings(Protocol):
    """全局设置的读写。"""

    def get(self, key: str, default: str | None = None) -> str | None:
        """读一个设置项。

        Args:
            key: 扁平键名，如 ``"download.proxy"``。
            default: 键不存在时返回的值。

        Returns:
            字符串值或 ``default``。类型转换（int/bool/枚举）由调用方按 schema 处理。
        """
        ...

    def set(self, key: str, value: str) -> None:
        """写一个设置项（只改内存，需 :meth:`save` 才落盘）。"""
        ...

    def as_mapping(self) -> Mapping[str, str]:
        """导出全部设置的只读视图（用于日志脱敏与设置页展示）。"""
        ...

    async def save(self) -> None:
        """落盘。

        Raises:
            SettingsWriteError: 写入失败（磁盘满/占用）——调用方必须提示，不得静默吞掉。
        """
        ...
