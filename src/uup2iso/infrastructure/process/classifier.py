"""行分类器：progress / line / result（进程 §5.2、§5.3）。

判定顺序（**顺序本身是契约**，不得调整）：

1. 命中结果正则 → ``result``
2. 命中进度正则 → ``progress``
3. 都未命中且工具版本**已登记** → ``line``
4. 都未命中且版本**未登记** → 严格兜底：同时满足「含 ``%`` 或含回车重绘」
   **且**「不含 ERROR / Failed / 失败 / 错误」才判 ``progress``，否则 ``line``

第 4 步必须严格：宁可把进度当普通行写进日志，也不能把错误行当进度丢掉。

> 正则来源：规范要求放在 ``rules/process_patterns.yaml``，但 YAML 解析需要额外依赖，
> 本项目依赖清单（WP0-T2）未包含。本轮把 §5.3 的示例正则内置为
> :data:`DEFAULT_PATTERN_SOURCE`，并保留 :meth:`LineClassifier.from_mapping` 以便
> 后续换成从规则文件加载（届时再决定是否引入 YAML 依赖）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from uup2iso.core.models.enums import ToolKind
from uup2iso.infrastructure.process.events import EventKind

__all__ = [
    "DEFAULT_PATTERN_SOURCE",
    "LineClassifier",
    "ToolPatterns",
]

#: 严格兜底时用于排除「其实是错误」的行（进程 §5.2 第 4 步）。
_ERROR_MARKERS = ("ERROR", "Failed", "FAILED", "failure", "失败", "错误")


@dataclass(frozen=True, slots=True)
class ToolPatterns:
    """某工具的结果/进度正则集合。"""

    progress: tuple[re.Pattern[str], ...] = ()
    result: tuple[re.Pattern[str], ...] = ()

    @classmethod
    def from_source(
        cls,
        progress: Iterable[str] = (),
        result: Iterable[str] = (),
        *,
        compiler: Callable[[str], re.Pattern[str]] = re.compile,
    ) -> ToolPatterns:
        """由正则字符串构造；编译失败会直接抛错（规则写错不该静默跳过）。"""
        return cls(
            progress=tuple(compiler(item) for item in progress),
            result=tuple(compiler(item) for item in result),
        )

    def matches_progress(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self.progress)

    def matches_result(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self.result)


#: 进程 §5.3 的内置正则（版本区间忽略：``"*"`` 语义）。
DEFAULT_PATTERN_SOURCE: dict[ToolKind, dict[str, tuple[str, ...]]] = {
    ToolKind.ARIA2C: {
        "progress": (
            r"^\[#\w{6}\s+[\d.]+[KMG]?iB/[\d.]+[KMG]?iB\(\d+%\)",
            r"\[DL:[\d.]+[KMG]?iB\]",
        ),
        "result": (r"Download Results:", r"Status Legend:"),
    },
    ToolKind.DISM: {
        "progress": (r"^\[\s*=+\s*\d+(\.\d+)?%\s*\]", r"^\s*\d+(\.\d+)?%\s*$"),
        "result": (r"The operation completed successfully", r"操作成功完成"),
    },
    ToolKind.WIMLIB: {
        "progress": (r"^(Writing|Scanning|Archiving|Extracting)\b.*\b\d+%",),
        "result": (r"^Successfully (exported|applied|captured)",),
    },
    ToolKind.OSCDIMG: {
        "progress": (r"^\s*\d+%",),
        "result": (r"OSCDIMG 2\.\d+",),
    },
}


class LineClassifier:
    """按工具把一行文本归类。"""

    def __init__(self, patterns: Mapping[ToolKind, ToolPatterns] | None = None) -> None:
        self._patterns: dict[ToolKind, ToolPatterns] = dict(patterns or self.default_patterns())

    @staticmethod
    def default_patterns() -> dict[ToolKind, ToolPatterns]:
        """把内置正则编译成 :class:`ToolPatterns`。"""
        return {
            tool: ToolPatterns.from_source(
                progress=source.get("progress", ()),
                result=source.get("result", ()),
            )
            for tool, source in DEFAULT_PATTERN_SOURCE.items()
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Mapping[str, Iterable[str]]]) -> LineClassifier:
        """从「工具名 → {progress: [...], result: [...]}」构造。

        供将来从 ``rules/process_patterns.yaml`` 加载时复用（工具名用枚举值匹配）。
        """
        by_value = {tool.value: tool for tool in ToolKind}
        patterns: dict[ToolKind, ToolPatterns] = {}
        for name, source in mapping.items():
            tool = by_value.get(name)
            if tool is None:
                continue
            patterns[tool] = ToolPatterns.from_source(
                progress=source.get("progress", ()),
                result=source.get("result", ()),
            )
        return cls(patterns)

    def patterns_for(self, tool: ToolKind) -> ToolPatterns:
        return self._patterns.get(tool, ToolPatterns())

    def classify(self, tool: ToolKind, text: str, *, version_known: bool = True) -> EventKind:
        """判定一行的类别（进程 §5.2）。

        Args:
            tool: 产生该行的工具。
            text: 已解码文本（可含 ``\\r``）。
            version_known: 工具版本是否已登记；``False`` 时启用严格兜底。

        Returns:
            :class:`EventKind` 之一。
        """
        patterns = self.patterns_for(tool)
        if patterns.matches_result(text):
            return EventKind.RESULT
        if patterns.matches_progress(text):
            return EventKind.PROGRESS
        if version_known:
            return EventKind.LINE
        return EventKind.PROGRESS if _looks_like_progress(text) else EventKind.LINE


def _looks_like_progress(text: str) -> bool:
    """严格兜底：有百分比/重绘特征且不含错误关键词才算进度（进程 §5.2）。"""
    has_progress_marker = "%" in text or "\r" in text
    if not has_progress_marker:
        return False
    lowered = text.lower()
    return not any(marker.lower() in lowered for marker in _ERROR_MARKERS)
