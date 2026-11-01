"""最小 CLI 入口。

WP0-T1 只落地可验证的 ``version`` 命令；完整最小命令集
（``run --config`` / ``--dry-run`` / ``--resume`` / ``--debug``）由 WP1-T5 实现，
业务选项一律写进 ``TaskConfig``，不提供字段级参数覆盖。

单实例（WP0-T11）：除只读命令外，每个命令执行前抢 ``Local\\UUP2ISO.SingleInstance``；
抢不到就输出 ``{"error":"already_running","code":5}`` 并以 **5** 退出，且**不建日志**。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import typer

from uup2iso import __version__
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

    Args:
        command: 当前子命令名；``None``（无子命令）视为需要互斥。
        session: 可注入的会话（测试用）。

    Returns:
        抢到的会话；豁免命令返回 ``None``。

    Raises:
        typer.Exit: 已有实例在运行（退出码 5）。
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
    session = acquire_cli_guard(command=ctx.invoked_subcommand)
    if session is not None:
        ctx.call_on_close(release_instance_with(session))


@app.command()
def version() -> None:
    """打印程序版本号。"""
    typer.echo(__version__)


def main() -> None:
    """``uup2iso`` 控制台入口。"""
    app()


if __name__ == "__main__":
    main()
