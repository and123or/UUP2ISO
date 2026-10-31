"""脚本健壮性门禁：环境自检必须能在非 UTF-8 控制台下运行。

对应 CHANGELOG 0.1.8 的 CI 失败：GitHub Windows 运行器的 stdout 是 cp1252，
`scripts/env_check.py` 打印中文（「结果: OK」）时抛
`UnicodeEncodeError: 'charmap' codec can't encode characters`，把一条全 PASS 的自检
变成构建失败。这里用 `PYTHONIOENCODING=cp1252` 复现该控制台环境。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT = WORKSPACE / "scripts" / "env_check.py"


def test_env_check_survives_cp1252_console() -> None:
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    # 子进程仍使用 .venv 解释器，脚本的解释器身份检查才能通过
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=WORKSPACE,
        env=env,
        capture_output=True,
        timeout=180,
        check=False,
    )
    stderr = result.stderr.decode("utf-8", "replace")
    stdout = result.stdout.decode("utf-8", "replace")

    assert result.returncode == 0, f"cp1252 控制台下自检失败：\n{stderr}"
    assert "UnicodeEncodeError" not in stderr
    assert "结果: OK" in stdout
