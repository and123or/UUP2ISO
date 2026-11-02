"""从路径与文件名推断构建信息（P0-0）。

无清单的 UUP 数据没有 `build` 字段，但**目录名通常带完整构建号**（如 `UUPs26300.9818`），
语言也能从 `professional_zh-cn.esd` 之类的文件名看出。这里只做**推断 + 反查**：

* 构建号来自路径/文件名（数据自己回答）；
* 版本族来自 `VersionCatalog`（`major → family` 反查）；
* 推断不出来就留空并记告警，**绝不编造**。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from uup2iso.core.models.version import VersionResolution
from uup2iso.infrastructure.version import VersionCatalog

__all__ = ["build_from_directory", "language_from_names", "resolve_input_version"]

_BUILD_IN_NAME = re.compile(r"(?<!\d)(\d{5}\.\d{1,6})(?!\d)")
_LANG_IN_NAME = re.compile(r"[_.-]([a-z]{2}-[a-z]{2})(?:[_.-]|$)", re.IGNORECASE)


def build_from_directory(name: str) -> str:
    """从目录名（或文件名）里取构建号，如 `UUPs26300.9818` → `26300.9818`。"""
    match = _BUILD_IN_NAME.search(name)
    return match.group(1) if match else ""


def language_from_names(names: Iterable[str]) -> str:
    """从文件名里取语言标签（如 `professional_zh-cn.esd` → `zh-cn`）。"""
    for name in names:
        match = _LANG_IN_NAME.search(name)
        if match:
            return match.group(1).lower()
    return ""


def resolve_input_version(
    *,
    path: Path,
    catalog: VersionCatalog,
    language_hint: str = "",
    names: Iterable[str] = (),
) -> VersionResolution:
    """综合路径名、文件名与版本目录，给出输入版本判定。

    Args:
        path: 输入文件或目录。
        catalog: 版本目录（提供 `major → family` 反查）。
        language_hint: 配置里已声明的语言，优先于推断。
        names: 目录内文件名（用于推断语言）；不传则不推断。

    Returns:
        :class:`VersionResolution`；推断不到时 `known=False` 并带告警。
    """
    build = build_from_directory(path.name)
    resolution = catalog.resolve_build(build) if build else VersionResolution(source="unknown")

    language = language_hint or language_from_names(names)
    warnings = list(resolution.warnings)
    source = resolution.source

    if build:
        source = f"directory-name+{source}"
    else:
        warnings.append(f"build_unresolved path={path.name}")

    if not language:
        warnings.append("language_unresolved")

    if not resolution.family and resolution.major:
        warnings.append(f"family_unknown major={resolution.major}")

    return VersionResolution(
        family=resolution.family,
        major=resolution.major,
        build=build,
        revision=resolution.revision,
        language=language,
        source=source,
        stale=resolution.stale,
        warnings=tuple(dict.fromkeys(warnings)),
    )
