"""pytest 全局夹具与运行环境门禁。

门禁（对齐 项目计划 WP0-T2a）：测试必须在工作空间 ``.venv`` 内执行。
如确需在其它解释器中临时运行，显式设置 ``UUP2ISO_SKIP_VENV_GUARD=1``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
VENV_PYTHON = WORKSPACE / ".venv" / "Scripts" / "python.exe"

# GUI 相关测试在无人值守环境（CI / 自动化代理）下必须离屏运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pytest_configure(config: object) -> None:
    """校验当前解释器就是工作空间 ``.venv`` 的解释器。"""
    if os.environ.get("UUP2ISO_SKIP_VENV_GUARD") == "1":
        return

    import pytest

    if not VENV_PYTHON.exists():
        raise pytest.UsageError(
            "未找到工作空间虚拟环境 .venv。请先执行: powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1"
        )

    if Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        raise pytest.UsageError(
            f"测试不允许在系统 Python 主环境运行。\n"
            f"  当前解释器: {sys.executable}\n"
            f"  期望解释器: {VENV_PYTHON}\n"
            f"请改用: .venv\\Scripts\\python -m pytest"
        )
