"""全局设置存储（配置 §2 ``config\\settings.json``；GUI-设置 §2）。

三个刻意的取舍：

* **读永不抛错**：键不存在返回默认值，一处拼写错误不该拖垮启动；
* **值统一存字符串**：类型转换（bool/int/枚举）由调用方按 schema 处理，存储层不做猜测；
* **敏感值脱敏**：``as_mapping()`` 默认把含 passwd/token/secret 的值替换成 ``***``，
  供日志与诊断包直接使用（日志 §5.5）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from uup2iso.core.errors import ConfigParseError, ConfigSchemaError

__all__ = ["DEFAULT_SETTINGS", "SettingsStore"]

DEFAULT_SETTINGS: dict[str, str] = {
    "source.baseUrl": "https://uupdump.net/",
    "source.fallbackUrls": "",
    "download.split": "8",
    "download.proxy": "",
    "download.timeoutSeconds": "30",
    "output.dir": "",
    "output.collision": "suffix",
    "ui.language": "zh-CN",
    "ui.theme": "auto",
    "defaults.strictUnknownFields": "false",
    "defaults.strictContext": "false",
    "defaults.strictFail": "false",
    "process.hang_warn_minutes": "10",
    "process.hang_sample_seconds": "30",
    "log.max_total_mb": "200",
    "log.max_days": "30",
    "log.max_files": "100",
    "audit.max_total_mb": "20",
    "audit.max_days": "365",
    "audit.max_files": "200",
    "tools.autoAlign": "false",
}
"""已知设置的默认值（其余键允许存在，读到即返回）。"""

_SENSITIVE = ("passw", "token", "secret", "apikey", "api-key")


class SettingsStore:
    """``ISettings`` 的文件实现。"""

    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self._path = path
        self._data: dict[str, Any] = data or {}

    # -- 构造 -------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> SettingsStore:
        """读取设置文件；文件不存在时返回空存储（默认值由 :meth:`get` 兜底）。"""
        if not path.exists():
            return cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigParseError(f"设置文件无法解析：{exc}", file=path) from exc
        if not isinstance(data, dict):
            raise ConfigSchemaError("设置文件顶层必须是对象", file=path)
        return cls(path, data)

    # -- ISettings --------------------------------------------------------

    def get(self, key: str, default: str | None = None) -> str | None:
        """按点分键读取；缺失时回落到 :data:`DEFAULT_SETTINGS`。"""
        value = self._lookup(key)
        if value is None:
            fallback = DEFAULT_SETTINGS.get(key)
            return fallback if fallback is not None else default
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    def set(self, key: str, value: str) -> None:
        """按点分键写入（只改内存，需 :meth:`save` 落盘）。"""
        parts = [part for part in key.split(".") if part]
        if not parts:
            raise ValueError("键不能为空")
        node = self._data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value

    def as_mapping(self, *, redact: bool = True) -> Mapping[str, str]:
        """扁平视图；``redact=True`` 时敏感值替换为 ``***``。"""
        flat: dict[str, str] = {}
        for key, value in _flatten(self._data):
            if redact and any(marker in key.lower() for marker in _SENSITIVE):
                flat[key] = "***"
            else:
                flat[key] = value
        return flat

    async def save(self) -> None:
        """原子落盘（先写同目录临时文件再 ``os.replace``）。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._data, indent=2, ensure_ascii=False) + "\n"
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        tmp.replace(self._path)

    # -- 辅助 -------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def to_dict(self) -> dict[str, Any]:
        """原始嵌套字典（诊断包用，不含默认值填充）。"""
        return json.loads(json.dumps(self._data))

    def _lookup(self, key: str) -> Any:
        node: Any = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node


def _flatten(data: Mapping[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            items.extend(_flatten(value, path))
        else:
            items.append((path, value if isinstance(value, str) else json.dumps(value)))
    return items
