"""进程适配层（WP0-T5）：外部进程的**唯一**入口。

模块构成：

| 模块 | 职责 |
| --- | --- |
| :mod:`.runner` | `ProcessRunner`：并发闸门、双路读取、取消阶梯、看门狗、错误组装 |
| :mod:`.backend` | 真实启动（asyncio subprocess）与夹具回放（`FakeBackend`） |
| :mod:`.decoder` | 分行（兼容 ``\\r`` 重绘）与编码回退链 |
| :mod:`.classifier` | progress / line / result 分类 |
| :mod:`.profiles` | 工具画像：编码、IO 画像、并发资源 |
| :mod:`.watchdog` | 四信号静默看门狗（只告警不杀） |
| :mod:`.sampler` | ctypes 采 CPU / IO / 网络，不依赖 psutil |
| :mod:`.errors` | 工具失败/启动失败/权限不足的异常与现场组装 |

业务代码**禁止**直接 ``subprocess``（AGENTS.md 架构红线），一律经这里。
"""

from __future__ import annotations

from uup2iso.infrastructure.process.backend import (
    ByteStream,
    FakeBackend,
    ProcessBackend,
    RealBackend,
    RunSpec,
    ScriptedStep,
    SpawnedProcess,
    load_script,
)
from uup2iso.infrastructure.process.classifier import (
    DEFAULT_PATTERN_SOURCE,
    LineClassifier,
    ToolPatterns,
)
from uup2iso.infrastructure.process.decoder import (
    MAX_LINE_BYTES,
    DecodedLine,
    LineSplitter,
    decode_line,
    hex_dump,
)
from uup2iso.infrastructure.process.errors import (
    PermissionDeniedError,
    ToolExecutionError,
    ToolMissingError,
    ToolStartError,
    build_tool_failure,
    redact_command,
)
from uup2iso.infrastructure.process.events import EventKind, ProcessEvent, ProcessResult
from uup2iso.infrastructure.process.profiles import (
    RESOURCE_DOWNLOAD,
    RESOURCE_IMAGE,
    TOOL_PROFILES,
    ToolProfile,
    profile_for,
)
from uup2iso.infrastructure.process.runner import (
    DEFAULT_GATE,
    ConcurrencyGate,
    ProcessRunner,
    run_process,
)
from uup2iso.infrastructure.process.sampler import (
    NullSampler,
    ResourceSampler,
    WindowsSampler,
)
from uup2iso.infrastructure.process.watchdog import (
    FourSignalWatchdog,
    HangEvidence,
    WatchdogConfig,
    WatchdogState,
)

__all__ = [
    "DEFAULT_GATE",
    "DEFAULT_PATTERN_SOURCE",
    "MAX_LINE_BYTES",
    "RESOURCE_DOWNLOAD",
    "RESOURCE_IMAGE",
    "TOOL_PROFILES",
    "ByteStream",
    "ConcurrencyGate",
    "DecodedLine",
    "EventKind",
    "FakeBackend",
    "FourSignalWatchdog",
    "HangEvidence",
    "LineClassifier",
    "LineSplitter",
    "NullSampler",
    "PermissionDeniedError",
    "ProcessBackend",
    "ProcessEvent",
    "ProcessResult",
    "ProcessRunner",
    "RealBackend",
    "ResourceSampler",
    "RunSpec",
    "ScriptedStep",
    "SpawnedProcess",
    "ToolExecutionError",
    "ToolMissingError",
    "ToolPatterns",
    "ToolProfile",
    "ToolStartError",
    "WatchdogConfig",
    "WatchdogState",
    "WindowsSampler",
    "build_tool_failure",
    "decode_line",
    "hex_dump",
    "load_script",
    "profile_for",
    "redact_command",
    "run_process",
]
