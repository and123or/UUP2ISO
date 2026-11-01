"""最小 CLI（WP1-T5）：`run --config` + 运行开关 + `version`。

边界（配置 §11）：**只接受配置文件与运行开关**，不做字段级参数覆盖——
`--source-url`、`--compression` 这类参数一律不提供，业务选项只能写进 `TaskConfig`。

参数写法统一双横线（日志 §3）：`--config` / `--dry-run` / `--resume` / `--debug`；
单横线（`-debug`）按未知参数报错（Typer 默认行为，退出码 2）。

单实例（WP0-T11）：除只读命令外，每个命令执行前抢 `Local\\UUP2ISO.SingleInstance`；
抢不到输出 `{"error":"already_running","code":5}` 并以 **5** 退出，且**不建日志**。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from uup2iso import __version__
from uup2iso.application.cli_runner import RunOptions, execute_run
from uup2iso.application.startup import (
    ALREADY_RUNNING_PAYLOAD,
    StartupConflictError,
    StartupSession,
    acquire_instance,
    release_instance,
)

app = typer.Typer(
    name="uup2iso",
    help="UUP -> ISO 构建与精简工具（最小 CLI）。",
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

GUARD_EXEMPT_COMMANDS: frozenset[str] = frozenset({"version"})
"""只读命令不参与互斥。

**这是一处有意为之的偏差**（日志 §6.1 原文是「GUI 与 CLI 整体互斥」）：
互斥的目的是「同一时刻只有一个写日志/跑任务的实例」，而 ``version`` 既不写日志也不争资源。
把它也挡掉只会让用户「开着一个窗口就查不了版本」。该偏差已登记在日志规范 §6.1 的落地状态里。
"""


def acquire_cli_guard(
    *,
    command: str | None,
    session: StartupSession | None = None,
) -> StartupSession | None:
    """为命令抢单实例；冲突时输出 JSON 并抛 ``Exit(5)``。

    ``run`` 自己会在 :func:`~uup2iso.application.cli_runner.execute_run` 里抢（它要先建会话再记日志），
    所以根回调对 ``run`` 跳过，避免同一次启动重复抢同一把互斥量。
    """
    if command is not None and command in GUARD_EXEMPT_COMMANDS:
        return None

    active = session or acquire_instance()
    if active is not None:
        return active

    typer.echo(json.dumps(ALREADY_RUNNING_PAYLOAD, ensure_ascii=False), err=True)
    raise typer.Exit(code=StartupConflictError.exit_code)


def release_instance_with(session: StartupSession) -> Callable[[], None]:
    """生成「释放会话」的零参回调（供 ``ctx.call_on_close`` 使用）。"""
    return lambda: release_instance(session)


@app.callback()
def _root(ctx: typer.Context) -> None:
    """UUP2ISO 最小 CLI：业务选项一律写在 TaskConfig，命令行只保留运行开关。

    注意：必须保留此根回调。Typer 在「只有一个命令且无回调」时会把该命令
    提升为根命令，导致 ``uup2iso version`` 被当成多余参数而报错。
    """
    if ctx.invoked_subcommand == "run":
        return
    session = acquire_cli_guard(command=ctx.invoked_subcommand)
    if session is not None:
        ctx.call_on_close(release_instance_with(session))


@app.command()
def version() -> None:
    """打印程序版本号。"""
    typer.echo(__version__)


@app.command()
def run(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            help="任务配置文件（TaskConfig JSON）。业务选项只能写在这个文件里。",
            dir_okay=False,
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="只预览：打印阶段、命令与将复用产物，不改动磁盘。"),
    ] = False,
    resume: Annotated[
        bool,
        typer.Option("--resume", help="从检查点续跑（配置或输入变化时拒绝）。"),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="本次运行输出 DEBUG 级别（不写入配置、不影响下次）。"),
    ] = False,
) -> None:
    """按配置文件执行一次任务。

    输出契约（T6）：**stdout = 单行 JSON**（`ok` / `code` / `task` / `stages` …），
    **stderr = 人读文本**。这样 CI 直接 `json.loads(stdout)` 即可，不需要额外开关。
    """
    outcome = execute_run(
        RunOptions(config_path=config, dry_run=dry_run, resume=resume, debug=debug)
    )

    # CI 消费：stdout 只有一个 JSON 对象；人读信息走 stderr（可用 2>/dev/null 静音）
    payload = {
        "ok": outcome.exit_code in (0, 1),
        **dict(getattr(outcome, "payload", {})),
        "code": outcome.exit_code,
    }
    typer.echo(json.dumps(payload, ensure_ascii=False))
    for line in getattr(outcome, "lines", ()):
        typer.echo(line, err=True)
    raise typer.Exit(code=outcome.exit_code)


def main() -> None:
    """``uup2iso`` 控制台入口。"""
    app()


if __name__ == "__main__":
    main()
