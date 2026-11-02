"""输入相关的应用服务（P0-0）：从路径/文件名推断构建号与版本族。"""

from __future__ import annotations

from uup2iso.application.input.version_hint import (
    build_from_directory,
    language_from_names,
    resolve_input_version,
)

__all__ = ["build_from_directory", "language_from_names", "resolve_input_version"]
