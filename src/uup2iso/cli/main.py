"""最小 CLI 入口。

WP0-T1 只落地可验证的 ``version`` 命令；完整最小命令集
（``run --config`` / ``--dry-run`` / ``--resume`` / ``--debug``）由 WP1-T5 实现，
业务选项一律写进 ``TaskConfig``，不提供字段级参数覆盖。
"""

from __future__ import annotations

import typer

from uup2iso import __version__

app = typer.Typer(
    name="uup2iso",
    help="UUP -> ISO 构建与精简工具（最小 CLI）。",
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


@app.callback()
def _root() -> None:
    """UUP2ISO 最小 CLI：业务选项一律写在 TaskConfig，命令行只保留运行开关。

    注意：必须保留此根回调。Typer 在「只有一个命令且无回调」时会把该命令
    提升为根命令，导致 ``uup2iso version`` 被当成多余参数而报错。
    """


@app.command()
def version() -> None:
    """打印程序版本号。"""
    typer.echo(__version__)


def main() -> None:
    """``uup2iso`` 控制台入口。"""
    app()


if __name__ == "__main__":
    main()
