"""工具供给（WP0-T7）：锁定清单、能力推导、路由选择、自检。

* :mod:`.lock` —— ``tools\\tools.lock.json`` 解析与自校验（唯一信任根，引擎 §9.1）；
* :mod:`.capabilities` —— 版本 → 能力推导，不确定即保守（引擎 §4）；
* :mod:`.route` —— 按操作 R1–R11 选主/备引擎（引擎 §3.2）；
* :mod:`.provider` —— ``ToolProvider``：定位、哈希校验、版本探测、自检报告。

自动获取（缺失时下载并落位）属 WP0-T8，本包只负责「找到、验过、说清楚」。
"""

from __future__ import annotations

from uup2iso.infrastructure.tools.capabilities import (
    THRESHOLDS,
    VersionThresholds,
    capabilities_for,
    is_version_at_least,
    parse_version,
)
from uup2iso.infrastructure.tools.lock import (
    EMPTY_LOCK,
    LockedTool,
    LockLoadResult,
    ToolLock,
    compute_identity,
    load_lock,
    write_lock,
)
from uup2iso.infrastructure.tools.provider import (
    TOOL_EXECUTABLES,
    VERSION_ARGS,
    CapabilityDegraded,
    ToolCheck,
    ToolProvider,
    ToolReport,
    describe_missing,
)
from uup2iso.infrastructure.tools.route import (
    ROUTES,
    Operation,
    Route,
    RouteDecision,
    select_route,
)

__all__ = [
    "EMPTY_LOCK",
    "ROUTES",
    "THRESHOLDS",
    "TOOL_EXECUTABLES",
    "VERSION_ARGS",
    "CapabilityDegraded",
    "LockLoadResult",
    "LockedTool",
    "Operation",
    "Route",
    "RouteDecision",
    "ToolCheck",
    "ToolLock",
    "ToolProvider",
    "ToolReport",
    "VersionThresholds",
    "capabilities_for",
    "compute_identity",
    "describe_missing",
    "is_version_at_least",
    "load_lock",
    "parse_version",
    "select_route",
    "write_lock",
]
