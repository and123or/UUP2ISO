"""UAC 自动提权门禁（P1-3）。

不在测试里真弹 UAC：`ensure_admin` 的逻辑用 monkeypatch 验证，
`_command_for` 分别覆盖「console-script/打包 exe」与「源码 python -m」两条启动路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uup2iso.application.elevation import ELEVATION_CANCELLED, _command_for, is_admin
from uup2iso.cli import main as cli_main


def test_is_admin_is_bool() -> None:
    assert isinstance(is_admin(), bool)


def test_command_for_console_script(tmp_path: Path) -> None:
    exe = tmp_path / "uup2iso.exe"
    exe.write_bytes(b"MZ")

    file, params = _command_for("C:/Python/python.exe", str(exe), ["run", "--config", "t.json"])

    assert file == str(exe), "有 exe 就直接用它"
    assert params == "run --config t.json"


def test_command_for_source_run() -> None:
    file, params = _command_for(
        "C:/Python/python.exe", "C:/repo/src/uup2iso/cli/main.py", ["run", "--config", "t.json"]
    )

    assert file == "C:/Python/python.exe"
    assert params.startswith("-m uup2iso.cli.main")
    assert "run --config t.json" in params


def test_command_for_quotes_paths_with_spaces() -> None:
    _file, params = _command_for("python.exe", "main.py", ["run", "--config", "D:/a b/task.json"])
    assert "'D:/a b/task.json'" in params or '"D:/a b/task.json"' in params


# --- ensure_admin -----------------------------------------------------------


def test_dry_run_never_elevates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_main, "is_admin", lambda: False)
    monkeypatch.setattr(cli_main, "relaunch_elevated", lambda: pytest.fail("不该提权"))

    assert cli_main.ensure_admin(dry_run=True) is None


def test_env_flag_skips_elevation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UUP2ISO_NO_ELEVATE", "1")
    monkeypatch.setattr(cli_main, "is_admin", lambda: False)
    monkeypatch.setattr(cli_main, "relaunch_elevated", lambda: pytest.fail("不该提权"))

    assert cli_main.ensure_admin(dry_run=False) is None


def test_admin_runs_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UUP2ISO_NO_ELEVATE", raising=False)
    monkeypatch.setattr(cli_main, "is_admin", lambda: True)
    monkeypatch.setattr(cli_main, "relaunch_elevated", lambda: pytest.fail("已是管理员"))

    assert cli_main.ensure_admin(dry_run=False) is None


def test_non_admin_relaunches_and_passes_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UUP2ISO_NO_ELEVATE", raising=False)
    monkeypatch.setattr(cli_main, "is_admin", lambda: False)
    monkeypatch.setattr(cli_main, "relaunch_elevated", lambda **_kw: 2)

    assert cli_main.ensure_admin(dry_run=False) == 2, "子进程退出码要原样透传"


def test_uac_cancel_maps_to_exit_3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UUP2ISO_NO_ELEVATE", raising=False)
    monkeypatch.setattr(cli_main, "is_admin", lambda: False)
    monkeypatch.setattr(cli_main, "relaunch_elevated", lambda **_kw: ELEVATION_CANCELLED)

    assert cli_main.ensure_admin(dry_run=False) == 3


def test_relaunch_failure_returns_4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UUP2ISO_NO_ELEVATE", raising=False)
    monkeypatch.setattr(cli_main, "is_admin", lambda: False)

    def boom(**_kw: object) -> int:
        raise RuntimeError("提权启动失败")

    monkeypatch.setattr(cli_main, "relaunch_elevated", boom)

    assert cli_main.ensure_admin(dry_run=False) == 4


# --- 命令层：run 会先提权 ------------------------------------------------


def test_run_command_elevates_before_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    calls: list[str] = []

    monkeypatch.setattr(cli_main, "ensure_admin", lambda *, dry_run: (calls.append("elev"), 0)[1])
    monkeypatch.setattr(
        cli_main, "execute_run", lambda _options: pytest.fail("提权后父进程不应继续执行")
    )

    result = CliRunner().invoke(cli_main.app, ["run", "--config", "t.json"])

    assert calls == ["elev"]
    assert result.exit_code == 0


def test_version_command_never_elevates(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    monkeypatch.setattr(
        cli_main, "ensure_admin", lambda *, dry_run: pytest.fail("version 不该提权")
    )

    result = CliRunner().invoke(cli_main.app, ["version"])
    assert result.exit_code == 0


# --- 程序目录解析（不依赖 CWD / 打包可用）----------------------------------


def test_program_dir_dev_mode_is_repo_root() -> None:
    from uup2iso.application.startup import program_dir

    assert (program_dir() / "pyproject.toml").is_file()


def test_program_dir_frozen_is_exe_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from uup2iso.application.startup import program_dir

    exe = tmp_path / "app" / "uup2iso.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    assert program_dir() == exe.parent


def test_tools_fallback_ignores_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`tools` 兜底必须指向程序目录，而不是启动时的当前目录。"""
    from uup2iso.application.pipeline.handlers.convert import _default_tools_dir
    from uup2iso.application.startup import program_dir

    monkeypatch.chdir(tmp_path)
    assert _default_tools_dir() == program_dir() / "tools"


def test_dry_run_dir_ignores_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from uup2iso.application.pipeline.orchestrator import _default_dry_run_dir
    from uup2iso.application.startup import program_dir

    monkeypatch.chdir(tmp_path)
    assert _default_dry_run_dir() == program_dir() / "work" / "dry-run"
