"""预设存储（配置 §4；``presets\\<kind>\\<id>.json``）。

关键行为（配置 §4.2）：

* 未知 ``ruleId`` **整份拒绝**并列出缺失项，不做部分导入；
* ``integrate`` / ``unattend`` 不做 ruleId 校验，但必须能通过片段模型校验；
* 运行时只读 ``TaskConfig``，本模块只在「选预设 / 存预设」时被调用。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from uup2iso.core.errors import ConfigSchemaError
from uup2iso.core.models.enums import PresetKind
from uup2iso.core.models.preset import PresetDocument, check_preset

__all__ = ["PresetStore"]


class PresetStore:
    """``IPresetStore`` 的文件实现。

    Args:
        root: ``presets`` 目录。
        known_rule_ids: 规则库里的 ruleId 集合；``None`` 表示暂不校验（规则库未加载）。
    """

    def __init__(self, root: Path, *, known_rule_ids: set[str] | None = None) -> None:
        self._root = root
        self._known_rule_ids = known_rule_ids

    # -- 查询 -------------------------------------------------------------

    def kind_dir(self, kind: PresetKind) -> Path:
        return self._root / kind.value

    async def list(self, kind: PresetKind) -> tuple[str, ...]:
        """列出某类预设 id（按名称升序）；目录不存在返回空元组。"""
        directory = self.kind_dir(kind)
        if not directory.is_dir():
            return ()
        return tuple(sorted(path.stem for path in directory.glob("*.json")))

    async def load(self, kind: PresetKind, name: str) -> Mapping[str, object]:
        """读取并校验一个预设；结构非法或含未知 ruleId 时抛错。"""
        document = self._read(kind, name)
        self._ensure_valid(document)
        return document.model_dump(mode="json", by_alias=True, exclude_none=True)

    # -- 写入 -------------------------------------------------------------

    async def save(self, kind: PresetKind, name: str, data: Mapping[str, object]) -> Path:
        """保存预设（同 id 覆盖），写入前必须通过校验。"""
        document = self._build(kind, name, data)
        self._ensure_valid(document)
        target = self.kind_dir(kind) / f"{document.id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, document.model_dump(mode="json", by_alias=True, exclude_none=True))
        return target

    async def export_(self, kind: PresetKind, name: str, target: Path) -> Path:
        """导出到指定文件（跨机携带）；导出前先校验，避免带出坏数据。"""
        document = self._read(kind, name)
        self._ensure_valid(document)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, document.model_dump(mode="json", by_alias=True, exclude_none=True))
        return target

    async def import_(self, kind: PresetKind, source: Path) -> str:
        """从文件导入预设，返回其 id。整份校验通过才落盘。"""
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigSchemaError(f"预设文件无法解析：{exc}", file=source) from exc
        document = self._build(kind, str(payload.get("id", source.stem)), payload)
        self._ensure_valid(document)
        target = self.kind_dir(kind) / f"{document.id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, document.model_dump(mode="json", by_alias=True, exclude_none=True))
        return document.id

    # -- 内部 -------------------------------------------------------------

    def _read(self, kind: PresetKind, name: str) -> PresetDocument:
        path = self.kind_dir(kind) / f"{name}.json"
        if not path.is_file():
            raise FileNotFoundError(f"预设不存在：{path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigSchemaError(f"预设文件无法解析：{exc}", file=path) from exc
        return self._build(kind, name, payload)

    def _build(self, kind: PresetKind, name: str, data: Mapping[str, object]) -> PresetDocument:
        payload: dict[str, Any] = dict(data)
        payload.setdefault("id", name)
        payload.setdefault("kind", kind.value)
        try:
            document = PresetDocument.model_validate(payload)
        except ValidationError as exc:
            raise ConfigSchemaError(
                f"预设结构不合法：{exc}",
            ) from exc
        if document.kind is not kind:
            raise ConfigSchemaError(
                f"预设类别不匹配：期望 {kind.value}，实际 {document.kind.value}"
            )
        return document

    def _ensure_valid(self, document: PresetDocument) -> None:
        issues = check_preset(document, known_rule_ids=self._known_rule_ids)
        if issues:
            detail = "；".join(f"{issue.field}: {issue.message}" for issue in issues)
            raise ConfigSchemaError(f"预设校验失败：{detail}")


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
