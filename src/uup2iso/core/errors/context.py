"""错误现场信息（进程 §9.2「必须保留的现场信息」）。

一次失败要能被 GUI 错误卡片、CLI JSON 与诊断包三处复用，所以字段固定为：

``stage`` / ``substep`` / ``file`` / ``tool`` / ``returncode`` / ``command`` /
``channel`` / ``hresult`` / ``duration_seconds`` / ``raw_excerpt``

其中 ``hresult`` 与 ``raw_excerpt`` 由本模块的两个小工具从工具输出里提取，
避免各调用点各写一遍正则。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import Path

__all__ = [
    "MAX_EXCERPT_LINES",
    "ErrorContext",
    "extract_hresult",
    "tail_lines",
]

MAX_EXCERPT_LINES = 20
"""保留的输出行数上限（进程 §9.2：最后 20 条 ``line``/``result``）。"""

_HRESULT = re.compile(r"0x[0-9A-Fa-f]{8}")


def tail_lines(source: str | Iterable[str], limit: int = MAX_EXCERPT_LINES) -> tuple[str, ...]:
    """取最后 ``limit`` 行作为现场片段。

    Args:
        source: 原始输出文本，或已切好的行序列。
        limit: 保留行数，默认 20（进程 §9.2）。

    Returns:
        行元组（不含换行符）；空输入返回空元组。
    """
    lines = source.splitlines() if isinstance(source, str) else list(source)
    return tuple(lines[-limit:]) if lines else ()


def extract_hresult(text: str | Iterable[str]) -> str | None:
    """从工具输出中提取 HRESULT（如 ``0x800f081f``）。

    取**最后一个**匹配：出错时的 HRESULT 通常在输出末尾，前面的可能是无关值。

    Args:
        text: 输出文本或行序列。

    Returns:
        形如 ``0x800f081f`` 的字符串；未找到返回 ``None``。
    """
    joined = text if isinstance(text, str) else "\n".join(text)
    matches = _HRESULT.findall(joined)
    return matches[-1] if matches else None


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """失败的现场信息；全部可选，未采集到的字段保持 ``None``。

    Attributes:
        stage: 8 阶段名（``Prepare``…``Output``，编排 §2）。
        substep: 阶段内子步骤（如 ``aggregate``、``mount``）。
        file: 涉及的文件或路径。
        tool: 外部工具名（``wimlib-imagex`` / ``dism`` / ``aria2c`` …）。
        returncode: 工具的**原始**返回码（不是进程退出码）。
        command: 已脱敏的命令行。
        channel: 输出通道（``stdout`` / ``stderr``），见进程 §5。
        hresult: 提取到的 HRESULT。
        duration_seconds: 该步骤耗时。
        raw_excerpt: 最后若干行输出（默认上限 20 行）。
    """

    stage: str | None = None
    substep: str | None = None
    file: Path | None = None
    tool: str | None = None
    returncode: int | None = None
    command: str | None = None
    channel: str | None = None
    hresult: str | None = None
    duration_seconds: float | None = None
    raw_excerpt: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """转成可直接 JSON 序列化的字典；``None`` 字段被省略以免噪声。"""
        payload: dict[str, object] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if value is None or value == () or value == "":
                continue
            payload[field.name] = str(value) if isinstance(value, Path) else value
        return payload
