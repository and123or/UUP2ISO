"""CLI 单实例门禁（日志 §6.1）。

验收：CLI 冲突输出 `{"error":"already_running","code":5}` 并以 5 退出，且不建日志。
这里在 `acquire_cli_guard` 层验证（`run` 命令本身属 WP1-T5）。
"""

from __future__ import annotations

import json

import pytest
import typer

from uup2iso.cli import main as cli_main
from uup2iso.cli.main import GUARD_EXEMPT_COMMANDS, acquire_cli_guard


class FakeSession:
    """可注入的会话替身（只需要 release）。"""

    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


def test_version_is_exempt(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """只读命令不参与互斥（有意偏差，已登记在日志 §6.1 落地状态）。"""
    assert "version" in GUARD_EXEMPT_COMMANDS

    def _boom() -> object:  # pragma: no cover - 不应被调用
        raise AssertionError("豁免命令不应尝试抢互斥量")

    monkeypatch.setattr(cli_main, "acquire_instance", _boom)
    assert acquire_cli_guard(command="version") is None
    assert capsys.readouterr().err == ""


def test_conflict_prints_json_and_exits_5(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_main, "acquire_instance", lambda: None)

    with pytest.raises(typer.Exit) as caught:
        acquire_cli_guard(command="run")

    assert caught.value.exit_code == 5
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload == {"error": "already_running", "code": 5}


def test_success_returns_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    monkeypatch.setattr(cli_main, "acquire_instance", lambda: session)

    assert acquire_cli_guard(command="run") is session


def test_no_subcommand_also_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """无子命令（如只敲 `uup2iso`）同样视为需要互斥。"""
    session = FakeSession()
    monkeypatch.setattr(cli_main, "acquire_instance", lambda: session)
    assert acquire_cli_guard(command=None) is session


def test_release_callback_releases_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    released: list[int] = []
    monkeypatch.setattr(cli_main, "release_instance", lambda _s: released.append(1))

    callback = cli_main.release_instance_with(session)
    callback()
    assert released == [1]
