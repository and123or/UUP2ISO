"""存储适配（WP0-T6）：任务配置读写、全局设置、预设。

* :mod:`.config_loader` —— V0/V1/V2 三层校验、迁移与备份、配置指纹；
* :mod:`.settings_store` —— ``config\\settings.json``；
* :mod:`.preset_store` —— ``presets\\<kind>\\<id>.json``。

运行时业务只读 ``TaskConfig``（预设快照已内联），不再回读 ``presets\\``（配置 §4.2）。
"""

from __future__ import annotations

from uup2iso.infrastructure.storage.config_loader import (
    CURRENT_VERSION,
    MIGRATORS,
    LoadedConfig,
    default_schema_path,
    dump_task_config,
    load_task_config,
    register_migrator,
    save_task_config,
)
from uup2iso.infrastructure.storage.preset_store import PresetStore
from uup2iso.infrastructure.storage.settings_store import DEFAULT_SETTINGS, SettingsStore

__all__ = [
    "CURRENT_VERSION",
    "DEFAULT_SETTINGS",
    "MIGRATORS",
    "LoadedConfig",
    "PresetStore",
    "SettingsStore",
    "default_schema_path",
    "dump_task_config",
    "load_task_config",
    "register_migrator",
    "save_task_config",
]
