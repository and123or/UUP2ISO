"""级别判定（日志 §3）。

四种运行方式各有默认级别，优先级：``--debug`` > CLI 固定 INFO > 冻结判定（GUI）。

这里只做「判定」，不做过滤——过滤在 :class:`~.sink.LogSink` 里，且 **WARN/ERROR 不受级别影响**。
"""

from __future__ import annotations

from uup2iso.infrastructure.logging.format import DEBUG, INFO

__all__ = ["LEVEL_SOURCES", "resolve_level"]

LEVEL_SOURCES: dict[str, str] = {
    "debug_flag": "--debug（仅本次）",
    "cli": "CLI 固定 INFO",
    "frozen": "发布版（INFO）",
    "dev": "开发模式（DEBUG）",
}


def resolve_level(*, frozen: bool, entry: str, debug: bool) -> tuple[str, str]:
    """返回 ``(级别, 来源说明)``。

    Args:
        frozen: 是否为 PyInstaller 冻结产物（``sys.frozen``）。
        entry: ``"cli"`` 或 ``"gui"``。
        debug: 是否带 ``--debug``（只影响本次运行）。
    """
    if debug:
        return DEBUG, LEVEL_SOURCES["debug_flag"]
    if entry == "cli":
        return INFO, LEVEL_SOURCES["cli"]
    if frozen:
        return INFO, LEVEL_SOURCES["frozen"]
    return DEBUG, LEVEL_SOURCES["dev"]
