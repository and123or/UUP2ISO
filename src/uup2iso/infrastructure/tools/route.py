"""按操作选择引擎（引擎 §3.2 路由表 R1–R11）。

路由是**静态表**，不是运行时猜的：每个操作有固定的主/备选择，
备用启用必须留下 ``capability_degraded`` 证据，方便事后解释「为什么这次用了 DISM」。

有三条特殊规则不能忘：

* **R3（出 ESD）**：保守模式下**不降级、直接拒绝**——用错版本失败要重跑几十分钟；
* **R4（拆 SWM）**：契约保留但本期不开放；
* **R5（校验）**：只有 wimlib，没有备用，属硬依赖。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from uup2iso.core.models.enums import ToolKind

__all__ = [
    "ROUTES",
    "Operation",
    "Route",
    "RouteDecision",
    "select_route",
]


class Operation(StrEnum):
    """引擎 §3.2 的 11 个操作。"""

    READ_INFO = "R1"
    EXPORT = "R2"
    WRITE_ESD = "R3"
    SPLIT_SWM = "R4"
    VERIFY = "R5"
    MOUNT = "R6"
    PACKAGE_OPS = "R7"
    AGGREGATE = "R8"
    EXTRACT = "R9"
    BUILD_ISO = "R10"
    REGISTRY = "R11"


@dataclass(frozen=True, slots=True)
class Route:
    """一条路由定义。

    Attributes:
        primary: 主工具。
        backup: 备用工具；``None`` 表示无备份（硬依赖）。
        degrade_allowed: 主工具不可用时是否允许用备用。
        note: 给人看的一句话（写进告警/日志）。
    """

    primary: ToolKind
    backup: ToolKind | None = None
    degrade_allowed: bool = True
    note: str = ""


ROUTES: dict[Operation, Route] = {
    Operation.READ_INFO: Route(ToolKind.WIMLIB, ToolKind.DISM, note="读信息主 wimlib（免管理员）"),
    Operation.EXPORT: Route(ToolKind.WIMLIB, ToolKind.DISM, note="导出主 wimlib"),
    Operation.WRITE_ESD: Route(
        ToolKind.WIMLIB,
        ToolKind.DISM,
        degrade_allowed=False,
        note="保守模式下不降级、直接拒绝（引擎 §5）",
    ),
    Operation.SPLIT_SWM: Route(
        ToolKind.DISM,
        ToolKind.WIMLIB,
        note="本期不开放，契约保留（配置 §3.4）",
    ),
    Operation.VERIFY: Route(ToolKind.WIMLIB, None, note="校验无备用，缺 wimlib 即无法校验"),
    Operation.MOUNT: Route(ToolKind.DISM, None, note="wimlib 无挂载语义"),
    Operation.PACKAGE_OPS: Route(ToolKind.DISM, None, note="加删包/功能/驱动的唯一路径"),
    Operation.AGGREGATE: Route(ToolKind.CONVERTER, None, note="路线 A：组件聚合只走转换器"),
    Operation.EXTRACT: Route(ToolKind.SEVEN_ZIP, None, note="CAB/ISO 解包"),
    Operation.BUILD_ISO: Route(ToolKind.OSCDIMG, None, note="缺失时提示安装 ADK"),
    Operation.REGISTRY: Route(ToolKind.REG, None, note="离线注册表读写"),
}


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """一次路由的结果。

    Attributes:
        operation: 操作。
        tool: 最终选用的工具；``None`` 表示不可用。
        degraded: 是否启用了备用（需要记 ``capability_degraded``）。
        reason: 不可用或降级的原因，直接写日志/错误提示。
    """

    operation: Operation
    tool: ToolKind | None
    degraded: bool = False
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.tool is not None


def select_route(
    operation: Operation,
    is_available: Callable[[ToolKind], bool],
) -> RouteDecision:
    """按路由表选择工具。

    Args:
        operation: 要执行的操作。
        is_available: 工具可用性判定（通常由 ``ToolProvider.is_available`` 提供）。

    Returns:
        :class:`RouteDecision`；不可用时 ``tool`` 为 ``None`` 且带原因。
    """
    route = ROUTES[operation]

    if is_available(route.primary):
        return RouteDecision(operation=operation, tool=route.primary)

    if route.backup is not None and route.degrade_allowed and is_available(route.backup):
        return RouteDecision(
            operation=operation,
            tool=route.backup,
            degraded=True,
            reason=(
                f"{route.primary.value} 不可用，降级使用 {route.backup.value}"
                + (f"（{route.note}）" if route.note else "")
            ),
        )

    detail = route.note or "无可用工具"
    if route.backup is not None and not route.degrade_allowed:
        detail = f"不降级：{detail}"
    return RouteDecision(
        operation=operation,
        tool=None,
        reason=f"{operation.value} 无可用工具（{detail}）",
    )
