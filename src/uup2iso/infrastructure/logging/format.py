"""行格式（日志 §2.3）。

固定结构，不多不少：

```
<本地时间 yyyy-MM-dd HH:mm:ss.SSS> [<级别>] [<阶段>] <logger>: <消息> | <k=v 键值对…>
```

三个容易写错的地方，这里一次性钉死：

* **级别固定 5 字符宽**（`[INFO ]`、`[WARN ]`），便于对齐与 grep；
* **阶段**只用编排的 8 个阶段名，无上下文写 `[-]`；
* **单行约束**：消息里的换行必须压平，异常栈由调用方拆行（`EXC` + `  | ` 续行）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import IntEnum

__all__ = [
    "AUDIT",
    "DEBUG",
    "ERROR",
    "INFO",
    "LEVELS",
    "WARN",
    "format_line",
    "level_rank",
    "make_field",
    "no_stage",
    "single_line",
]

DEBUG = "DEBUG"
INFO = "INFO"
WARN = "WARN"
ERROR = "ERROR"
AUDIT = "AUDIT"

LEVELS: tuple[str, ...] = (DEBUG, INFO, WARN, ERROR, AUDIT)
"""五个级别（`AUDIT` 只出现在审计文件）。"""

NO_STAGE = "-"
"""无阶段上下文时的占位（日志 §2.3）。"""


class _Rank(IntEnum):
    DEBUG = 10
    INFO = 20
    WARN = 30
    ERROR = 40


_RANKS = {
    DEBUG: _Rank.DEBUG,
    INFO: _Rank.INFO,
    WARN: _Rank.WARN,
    ERROR: _Rank.ERROR,
}

_REPLACEMENTS = (("\r\n", " "), ("\r", " "), ("\n", " "), ("\t", " "))


def no_stage() -> str:
    return NO_STAGE


def single_line(text: str) -> str:
    """把任意文本压成单行（换行/制表符变空格，连续空格归一）。

    特例：**以两个空格开头时保留该缩进**——异常栈续行的 ``  | `` 前缀依赖它。
    """
    indented = text.startswith("  ")
    for old, new in _REPLACEMENTS:
        text = text.replace(old, new)
    flat = " ".join(text.split())
    return f"  {flat}" if indented else flat


def level_rank(level: str) -> int:
    """级别高低（用于阈值过滤）。``AUDIT`` 不参与比较，给最大秩。"""
    return int(_RANKS.get(level, _Rank.ERROR)) if level != AUDIT else 100


def make_field(value: object) -> str:
    """把 ``k=v`` 的 ``v`` 转成单行文本；``None``/空串返回空串（调用方跳过该键）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return single_line(str(value))


def format_line(
    *,
    timestamp: datetime,
    level: str,
    stage: str | None,
    logger_name: str,
    message: str,
    fields: Mapping[str, object] | None = None,
) -> str:
    """拼一行日志（不含换行符）。

    Args:
        timestamp: 本地时间，格式化为毫秒精度。
        level: 五个级别之一；非法级别按 ERROR 处理（宁可显示得更严重）。
        stage: 阶段名；``None`` 时写 ``-``。
        logger_name: 逻辑模块名（``infra.process`` / ``app.audit`` …）。
        message: 单行消息（内部会压平换行）。
        fields: 追加的 ``k=v`` 对，按插入顺序输出。

    Returns:
        ``<ts> [<LEVEL>] [<stage>] <logger>: <msg> | k=v …``；无字段时不含 ``|``。
    """
    tag = level if level in LEVELS else ERROR
    stamp = timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    head = f"{stamp} [{tag:<5}] [{stage or NO_STAGE}] {logger_name}: {single_line(message)}"

    if not fields:
        return head

    pairs = []
    for key, value in fields.items():
        text = make_field(value)
        if text == "":
            continue
        pairs.append(f"{key}={text}")
    if not pairs:
        return head
    return f"{head} | {' '.join(pairs)}"
