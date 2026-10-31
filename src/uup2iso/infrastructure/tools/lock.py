"""工具锁定清单：唯一信任根（引擎 §9.1）。

信任模型的一句话：**哈希只证明「文件与期望值一致」，期望值必须来自与文件不同的通道**。
因此期望值只来自随包发布的 ``tools\\tools.lock.json``，不联网取清单、不读发布页哈希、
不用上游 ``SHA256SUMS``。代价是工具新版本只能随程序发版进入（§9.3）。

清单自校验的关键是 §9.1 的**自指定义**：
``identitySha256`` = 「把该字段置为空串 → 规范化序列化（键排序、无多余空白）→ SHA256」。
不能用「含当前值的原文」求哈希，否则写入即改变内容，永远不相等。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "EMPTY_LOCK",
    "LockedTool",
    "ToolLock",
    "compute_identity",
    "load_lock",
    "write_lock",
]

LOCK_SCHEMA_VERSION = "1.0"


class LockedTool(BaseModel):
    """清单中的一个工具条目（引擎 §9.1）。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str
    """工具短名，与 :class:`~uup2iso.core.models.enums.ToolKind` 的取值一致。"""

    channel: str = "official"
    url: str = ""
    sha256: str = ""
    version: str = ""
    license: str = ""
    required: bool = True
    managed: bool = True
    """转换器标记 ``False``：永不参与「对齐」自动替换（引擎 §9.4）。"""


class ToolLock(BaseModel):
    """``tools\\tools.lock.json``。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_version: str = Field(default=LOCK_SCHEMA_VERSION, alias="schemaVersion")
    generated_at: str = Field(default="", alias="generatedAt")
    identity_sha256: str = Field(default="", alias="identitySha256")
    tools: list[LockedTool] = Field(default_factory=list)

    def entry_for(self, name: str) -> LockedTool | None:
        """按工具短名查条目；未登记返回 ``None``（表示「不管理该工具」）。"""
        return next((item for item in self.tools if item.name == name), None)

    def managed_names(self) -> frozenset[str]:
        return frozenset(item.name for item in self.tools if item.managed)


EMPTY_LOCK = ToolLock()
"""内置只读副本：清单缺失或被判定篡改时回退到它（引擎 §9.1）。"""


def compute_identity(lock: ToolLock) -> str:
    """按 §9.1 的自指定义计算清单身份哈希。"""
    payload: dict[str, Any] = lock.model_dump(mode="json", by_alias=True)
    payload["identitySha256"] = ""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LockLoadResult:
    """清单加载结果。

    Attributes:
        lock: 生效的清单（篡改时是内置空清单）。
        tampered: 是否检出被手工改动。
        warnings: 需要写进日志的告警（如 ``manifest_tampered``）。
        source: ``file`` 或 ``builtin``。
    """

    lock: ToolLock
    tampered: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)
    source: str = "file"


def load_lock(path: Path, *, expected_identity: str | None = None) -> LockLoadResult:
    """读取并校验清单。

    Args:
        path: ``tools\\tools.lock.json``。
        expected_identity: 编译期写入程序常量的期望值；``None`` 表示不校验（开发期）。

    Returns:
        加载结果；文件不存在或哈希不符时回退到 :data:`EMPTY_LOCK` 并附告警。
    """
    if not path.is_file():
        return LockLoadResult(
            lock=EMPTY_LOCK,
            warnings=("manifest_missing",),
            source="builtin",
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        lock = ToolLock.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return LockLoadResult(
            lock=EMPTY_LOCK,
            tampered=True,
            warnings=(f"manifest_tampered reason=unreadable detail={exc}",),
            source="builtin",
        )

    if expected_identity is None:
        return LockLoadResult(lock=lock)

    actual = compute_identity(lock)
    if actual != expected_identity:
        return LockLoadResult(
            lock=EMPTY_LOCK,
            tampered=True,
            warnings=("manifest_tampered reason=identity_mismatch",),
            source="builtin",
        )
    return LockLoadResult(lock=lock)


def write_lock(path: Path, tools: list[LockedTool]) -> ToolLock:
    """写入清单并自动回填 ``identitySha256``（供发版脚本用）。"""
    lock = ToolLock(tools=tools)
    lock.identity_sha256 = compute_identity(lock)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        lock.model_dump(mode="json", by_alias=True), indent=2, ensure_ascii=False
    )
    path.write_text(text + "\n", encoding="utf-8", newline="\n")
    return lock
