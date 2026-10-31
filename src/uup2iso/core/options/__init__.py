"""选项与执行请求 DTO。

两类东西刻意分开（决策 D4）：

* **执行请求**（本包，:mod:`uup2iso.core.options.execution`）：编排层翻译给某个
  能力的窄参数，接口只认这些；
* **配置选项**（`ConvertOptions` / `SlimOptions` / ...）：`TaskConfig` 的组成部分，
  属 WP0-T6，只用于持久化与校验。

本轮只落地前者；后者随 `TaskConfig` 一起在 WP0-T6 建立。
"""

from __future__ import annotations

from uup2iso.core.options.execution import (
    DownloadOptions,
    ExportRequest,
    IsoBuildRequest,
    MountRequest,
    OptimizeRequest,
)

__all__ = [
    "DownloadOptions",
    "ExportRequest",
    "IsoBuildRequest",
    "MountRequest",
    "OptimizeRequest",
]
