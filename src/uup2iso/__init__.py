"""UUP2ISO —— UUP 转 ISO 构建与精简工具。

包版本由 ``pyproject.toml`` 的 ``[project] version`` 单一驱动，
三处同步点见 ``docs/治理/版本与变更管理.md`` §3：

* ``pyproject.toml`` ``[project] version``
* 本文件的 ``__version__``
* ``tests/test_version.py`` 的断言
"""

from __future__ import annotations

__version__ = "0.1.11"
__all__ = ["__version__"]
