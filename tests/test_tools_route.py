"""路由表门禁（引擎 §3.2）。

三条特殊规则要单独守住：R3 不降级、R5 无备用、R6/R7 只有 DISM。
"""

from __future__ import annotations

from uup2iso.core.models.enums import ToolKind
from uup2iso.infrastructure.tools.route import ROUTES, Operation, select_route


def availability(*available: ToolKind) -> dict[ToolKind, bool]:
    return {kind: kind in available for kind in ToolKind}


def test_route_table_covers_all_eleven_operations() -> None:
    assert len(ROUTES) == 11
    assert set(ROUTES) == set(Operation)


def test_primary_preferred_when_available() -> None:
    decision = select_route(
        Operation.READ_INFO,
        lambda kind: kind in {ToolKind.WIMLIB, ToolKind.DISM},
    )
    assert decision.tool is ToolKind.WIMLIB
    assert decision.degraded is False
    assert decision.reason is None


def test_backup_used_and_marked_degraded() -> None:
    decision = select_route(
        Operation.READ_INFO,
        lambda kind: kind is ToolKind.DISM,
    )
    assert decision.tool is ToolKind.DISM
    assert decision.degraded is True
    assert "降级" in (decision.reason or "")


def test_esd_output_refuses_to_degrade() -> None:
    """保守模式下出 ESD 不降级，直接拒绝（引擎 §5）。"""
    decision = select_route(
        Operation.WRITE_ESD,
        lambda kind: kind is ToolKind.DISM,
    )
    assert decision.tool is None
    assert decision.available is False
    assert "不降级" in (decision.reason or "")


def test_verify_has_no_backup() -> None:
    decision = select_route(Operation.VERIFY, lambda kind: kind is ToolKind.DISM)
    assert decision.tool is None
    assert "无可用工具" in (decision.reason or "")


def test_mount_only_dism() -> None:
    assert select_route(Operation.MOUNT, lambda kind: True).tool is ToolKind.DISM
    missing = select_route(Operation.MOUNT, lambda kind: kind is ToolKind.WIMLIB)
    assert missing.tool is None


def test_aggregate_requires_converter() -> None:
    assert select_route(Operation.AGGREGATE, lambda kind: True).tool is ToolKind.CONVERTER
    assert (
        select_route(Operation.AGGREGATE, lambda kind: kind is not ToolKind.CONVERTER).tool is None
    )


def test_every_route_has_a_valid_primary() -> None:
    for operation, route in ROUTES.items():
        assert isinstance(route.primary, ToolKind)
        assert route.primary is not ToolKind.CONVERTER or operation is Operation.AGGREGATE
