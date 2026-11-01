"""CLI `run` 门禁（WP1-T5）。

验收：只接受配置文件与运行开关；`--debug` 仅本次生效；冲突退 5 且不建日志；
`--dry-run` 不改动磁盘；配置错误退 4。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uup2iso.application.cli_runner import RunOptions, execute_run
from uup2iso.application.startup import acquire_instance
from uup2iso.application.task_session import TaskSession
from uup2iso.cli.main import app
from uup2iso.core.models import default_task_config
from uup2iso.core.models.enums import EntryPoint
from uup2iso.infrastructure.storage import dump_task_config


def unique_mutex() -> str:
    return rf"Local\UUP2ISO.Test.Cli.{uuid.uuid4().hex}"


def write_config(tmp_path: Path, *, start: str = "Convert") -> Path:
    config = default_task_config(mode=EntryPoint.IMAGE)
    config.input.start_stage = start
    config.input.image = {  # type: ignore[assignment]
        "path": str(tmp_path / "win11.iso"),
        "type": "iso",
        "editions": [1],
    }
    path = tmp_path / "task.json"
    path.write_text(dump_task_config(config), encoding="utf-8")
    return path


def task_id_of(path: Path) -> str:
    return str(json.loads(path.read_text(encoding="utf-8"))["meta"]["id"])


# --- 正常路径 ---------------------------------------------------------------


def test_dry_run_prints_plan_and_creates_nothing(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    work = tmp_path / "work"
    logs = tmp_path / "logs"

    outcome = execute_run(
        RunOptions(config_path=config_path, dry_run=True),
        log_dir=logs,
        work_dir=work,
        mutex_name=unique_mutex(),
    )

    assert outcome.exit_code == 0
    assert outcome.payload["dryRun"] is True
    assert "Prepare" in " ".join(outcome.lines)
    assert any("[skip]" in line for line in outcome.lines), "跳过阶段要出现在预览里"
    assert not work.exists(), "dry-run 不得创建 work 目录"
    assert logs.is_dir(), "dry-run 仍应留下运行日志"


def test_dry_run_payload_has_stage_details_and_files(tmp_path: Path) -> None:
    """T7：预览要能给 CI 结构化的「阶段 + 命令 + 文件操作」。"""
    config_path = write_config(tmp_path)

    outcome = execute_run(
        RunOptions(config_path=config_path, dry_run=True),
        log_dir=tmp_path / "logs",
        work_dir=tmp_path / "work",
        mutex_name=unique_mutex(),
    )

    stages = outcome.payload["stages"]
    assert isinstance(stages, list) and stages
    prepare = next(item for item in stages if item["stage"] == "Prepare")
    assert prepare["action"] == "run"
    assert "probe" in prepare["substeps"]
    assert prepare["commands"], "Prepare 应给出命令"
    assert any(item["action"] == "skip" for item in stages), "跳过阶段也要在 JSON 里"

    files = outcome.payload["files"]
    assert any(item["path"].endswith("probe.json") for item in files)
    assert all(item["action"] in {"read", "create", "delete"} for item in files)
    assert not (tmp_path / "work").exists(), "预览不得创建目录"


def test_debug_flag_only_affects_this_run(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    before = config_path.read_bytes()

    normal = execute_run(
        RunOptions(config_path=config_path, dry_run=True),
        log_dir=tmp_path / "logs1",
        work_dir=tmp_path / "w1",
        mutex_name=unique_mutex(),
    )
    debug = execute_run(
        RunOptions(config_path=config_path, dry_run=True, debug=True),
        log_dir=tmp_path / "logs2",
        work_dir=tmp_path / "w2",
        mutex_name=unique_mutex(),
    )

    assert normal.payload["logLevel"] == "INFO", "CLI 默认 INFO"
    assert debug.payload["logLevel"] == "DEBUG", "--debug 仅本次升到 DEBUG"
    assert config_path.read_bytes() == before, "--debug 不写回配置（日志 §3）"


# --- 配置错误 ---------------------------------------------------------------


def test_broken_json_exits_4(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")

    outcome = execute_run(
        RunOptions(config_path=path, dry_run=True),
        log_dir=tmp_path / "logs",
        mutex_name=unique_mutex(),
    )

    assert outcome.exit_code == 4
    assert outcome.payload["error"] == "CONFIG_PARSE_ERROR"
    assert not (tmp_path / "logs").exists(), "配置都没读通，不该建日志"


def test_semantic_error_exits_4(tmp_path: Path) -> None:
    config = default_task_config(mode=EntryPoint.IMAGE)
    config.input.image = {"path": str(tmp_path / "a.iso"), "type": "iso"}  # type: ignore[assignment]
    path = tmp_path / "task.json"
    path.write_text(dump_task_config(config), encoding="utf-8")

    outcome = execute_run(
        RunOptions(config_path=path, dry_run=True),
        log_dir=tmp_path / "logs",
        mutex_name=unique_mutex(),
    )

    assert outcome.exit_code == 4
    assert outcome.payload["error"] == "CONFIG_SEMANTIC_INVALID"
    assert "editions" in str(outcome.payload["message"])


# --- 单实例 -----------------------------------------------------------------


def test_conflict_exits_5_and_creates_no_logs(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    name = unique_mutex()
    holder = acquire_instance(name)
    assert holder is not None

    logs = tmp_path / "logs"
    try:
        outcome = execute_run(
            RunOptions(config_path=config_path, dry_run=True),
            log_dir=logs,
            mutex_name=name,
        )
    finally:
        holder.guard.release()

    assert outcome.exit_code == 5
    assert outcome.payload == {"error": "already_running", "code": 5}
    assert not logs.exists(), "冲突实例不建日志、不清理（日志 §6.1）"


# --- 续跑 -------------------------------------------------------------------


def test_resume_with_changed_config_is_rejected(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    work = tmp_path / "work"
    session = TaskSession(
        task_id=task_id_of(config_path),
        work_dir=work,
        config_fingerprint="完全不同的一套配置",
    )
    session.start()

    outcome = execute_run(
        RunOptions(config_path=config_path, resume=True, dry_run=True),
        log_dir=tmp_path / "logs",
        work_dir=work,
        mutex_name=unique_mutex(),
    )

    assert outcome.exit_code == 4
    assert outcome.payload["error"] == "CONFIG_CHANGED"
    assert outcome.payload["decision"] == "reject_config_changed"


def test_resume_without_checkpoint_is_new_task(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    outcome = execute_run(
        RunOptions(config_path=config_path, resume=True, dry_run=True),
        log_dir=tmp_path / "logs",
        work_dir=tmp_path / "work",
        mutex_name=unique_mutex(),
    )

    assert outcome.exit_code == 0
    assert outcome.payload["decision"] == "new_task"


# --- 非 dry-run -------------------------------------------------------------


def test_run_without_handlers_reports_not_implemented(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    outcome = execute_run(
        RunOptions(config_path=config_path),
        log_dir=tmp_path / "logs",
        work_dir=tmp_path / "work",
        mutex_name=unique_mutex(),
    )

    assert outcome.exit_code == 4
    assert outcome.payload["error"] == "not_implemented"
    assert outcome.payload["missingStages"], "必须列出缺哪些阶段"
    assert "Output" in outcome.payload["missingStages"]


# --- 参数解析（Typer 层） ---------------------------------------------------


def test_cli_requires_config_option(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 2, "缺 --config 属用法错误"


def test_cli_rejects_single_dash_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """日志 §3：参数只支持双横线，`-debug` 按未知参数报错。"""
    from uup2iso.cli import main as cli_main

    monkeypatch.setattr(
        cli_main,
        "execute_run",
        lambda _options: type("O", (), {"exit_code": 0, "lines": ()})(),
    )
    result = CliRunner().invoke(app, ["run", "--config", str(tmp_path / "x.json"), "-debug"])
    assert result.exit_code == 2


def test_cli_passes_flags_to_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from uup2iso.cli import main as cli_main

    captured: dict[str, object] = {}

    def fake_run(options: RunOptions) -> object:
        captured.update(
            {
                "config": options.config_path,
                "dry_run": options.dry_run,
                "resume": options.resume,
                "debug": options.debug,
            }
        )
        return type("O", (), {"exit_code": 0, "lines": ("ok",)})()

    monkeypatch.setattr(cli_main, "execute_run", fake_run)
    config = tmp_path / "task.json"
    config.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["run", "--config", str(config), "--dry-run", "--resume", "--debug"]
    )

    assert result.exit_code == 0
    assert captured == {
        "config": config,
        "dry_run": True,
        "resume": True,
        "debug": True,
    }


def test_stdout_is_single_json_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """T6：CI 直接 json.loads(stdout) 就能消费。"""
    from uup2iso.cli import main as cli_main

    def fake_run(_options: RunOptions) -> object:
        return type(
            "O",
            (),
            {
                "exit_code": 0,
                "payload": {"task": "abcd1234", "dryRun": True},
                "lines": ("人读的一行",),
            },
        )()

    monkeypatch.setattr(cli_main, "execute_run", fake_run)
    result = CliRunner().invoke(app, ["run", "--config", "task.json", "--dry-run"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload == {"ok": True, "task": "abcd1234", "dryRun": True, "code": 0}
    assert "人读的一行" in result.stderr, "人读信息走 stderr"


def test_failed_run_reports_code_in_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from uup2iso.cli import main as cli_main

    def fake_run(_options: RunOptions) -> object:
        return type(
            "O",
            (),
            {
                "exit_code": 4,
                "payload": {"error": "CONFIG_CHANGED", "message": "配置已变更"},
                "lines": (),
            },
        )()

    monkeypatch.setattr(cli_main, "execute_run", fake_run)
    result = CliRunner().invoke(app, ["run", "--config", "task.json", "--resume"])

    assert result.exit_code == 4
    payload = json.loads(result.stdout.strip())
    assert payload["ok"] is False
    assert payload["code"] == 4
    assert payload["error"] == "CONFIG_CHANGED"


def test_version_still_works() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()
