"""接口契约（`typing.Protocol`）统一出口（WP0-T3）。

八个能力接口对应 `架构 §6`（比项目计划的 7 个多一个 `IToolProvider`，后者是
WP0-T7 的落点）：

| 接口 | 实现（infrastructure） | 落地任务 |
| --- | --- | --- |
| :class:`IUupSource` | `UupDumpSource` | WP2-T1 |
| :class:`IDownloader` | `Aria2Downloader` | WP2-T6 |
| :class:`IToolProvider` | `ToolProvider` | WP0-T7 |
| :class:`IImageEngine` | `WimlibImageEngine` / `DismImageEngine` | WP3-T1 |
| :class:`IOptimizer` | `SystemOptimizer` | WP5-T1 |
| :class:`IIsoBuilder` | `OscdimgIsoBuilder` | WP3-T8 |
| :class:`IPresetStore` | `PresetStore` | WP0-T6 |
| :class:`ISettings` | `SettingsStore` | WP0-T6 |

全部标注 `@runtime_checkable`，便于测试用「鸭子类型替身」验证契约，
也便于组合根在启动时做一次「实现是否满足接口」的自检。
"""

from __future__ import annotations

from uup2iso.core.abstractions.downloader import IDownloader
from uup2iso.core.abstractions.image_engine import IImageEngine
from uup2iso.core.abstractions.iso_builder import IIsoBuilder
from uup2iso.core.abstractions.optimizer import IOptimizer
from uup2iso.core.abstractions.preset_store import IPresetStore
from uup2iso.core.abstractions.runtime import CancelToken, ProgressSink
from uup2iso.core.abstractions.settings import ISettings
from uup2iso.core.abstractions.source import BuildQuery, IUupSource
from uup2iso.core.abstractions.tool_provider import IToolProvider

__all__ = [
    "BuildQuery",
    "CancelToken",
    "IDownloader",
    "IImageEngine",
    "IIsoBuilder",
    "IOptimizer",
    "IPresetStore",
    "ISettings",
    "IToolProvider",
    "IUupSource",
    "ProgressSink",
]
