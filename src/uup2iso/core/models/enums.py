"""领域枚举：全部取值以文档为唯一来源，禁止在代码里新增同义值。

| 枚举 | 权威章节 |
| --- | --- |
| :class:`EntryPoint` | 配置 §3.2（`input.mode`） |
| :class:`ImageType` / :class:`OutputType` | 输入 §2.1、配置 §3.4 |
| :class:`CompressionKind` | 配置 §3.3（`convert.compression`，大写） |
| :class:`VerifyLevel` | 输出 §6 |
| :class:`TaskState` / :class:`StageState` / :class:`SubstepState` / :class:`DownloadFileState` | 术语 §2.2、编排 §4.1、§4.2 |
| :class:`ToolKind` / :class:`EngineKind` | 引擎 §3.1、§3.2 |
| :class:`Architecture` | 下载 §2.1 |

约定：**状态取值一律小写**（术语 §2.2 明令禁止 `success`/`complete`/`paused` 等同义词）；
`CompressionKind` 例外，按配置规范保持大写。
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

__all__ = [
    "Architecture",
    "CompressionKind",
    "DownloadFileState",
    "EngineKind",
    "EntryPoint",
    "ImageType",
    "OutputType",
    "PresetKind",
    "StageState",
    "SubstepState",
    "TaskState",
    "ToolKind",
    "VerifyLevel",
]


class EntryPoint(StrEnum):
    """任务入口（配置 §3.2 `input.mode`）——值即配置文件里写的字符串。"""

    ONLINE = "online"
    """入口 A：在线 UUP 构建（需求 FR0.2）。"""

    UUP_FOLDER = "uupFolder"
    """入口 B：本地 UUP 文件夹，跳过 Fetch 从 Convert 开始（FR0.3）。"""

    IMAGE = "image"
    """入口 C：现成 ISO/WIM/ESD，先 Prepare 再进入定制（FR0.4）。"""


class ImageType(StrEnum):
    """输入映像类型（输入 §2.1）。"""

    ISO = "iso"
    WIM = "wim"
    ESD = "esd"
    SWM = "swm"
    UNKNOWN = "unknown"
    """嗅探未识别；此时不得继续，由调用方按 `INPUT_UNSUPPORTED` 拒绝。"""


class OutputType(StrEnum):
    """产物形态（配置 §3.4 `output.type`）。"""

    ISO = "iso"
    WIM = "wim"
    ESD = "esd"
    SWM = "swm"
    """契约留值但本期拒绝（`OUTPUT_TYPE_UNSUPPORTED`，配置 §3.4、输出 §6.1）。"""


class CompressionKind(StrEnum):
    """WIM 压缩类型（配置 §3.3，取值大写，与 `ConvertConfig.ini` 对齐）。"""

    NONE = "none"
    XPRESS = "XPRESS"
    LZX = "LZX"
    LZMS = "LZMS"
    """ESD 级压缩；保守模式下禁用（引擎 §5）。"""


class VerifyLevel(IntEnum):
    """产物校验分级（输出 §6）。"""

    L1 = 1
    """结构校验，默认开。"""

    L2 = 2
    """内容校验，默认开。"""

    L3 = 3
    """深度校验（`wimlib verify` / `ScanHealth`），默认关。"""


class TaskState(StrEnum):
    """任务状态（术语 §2.2、编排 §4.1）。不存在 `paused` 与「部分成功」。"""

    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class StageState(StrEnum):
    """阶段状态（编排 §4.2）。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class SubstepState(StrEnum):
    """阶段内子步骤状态（编排 §4.2）。注意是 `done` 而不是 `succeeded`。"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class DownloadFileState(StrEnum):
    """单个下载文件的状态（术语 §2.2）。"""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    VERIFIED = "verified"
    FAILED = "failed"


class Architecture(StrEnum):
    """CPU 架构（下载 §2.1 字段）。"""

    X64 = "x64"
    ARM64 = "arm64"
    X86 = "x86"


class ToolKind(StrEnum):
    """外部工具种类（引擎 §3.1、下载 §4）。"""

    ARIA2C = "aria2c"
    SEVEN_ZIP = "7z"
    WIMLIB = "wimlib-imagex"
    DISM = "dism"
    OSCDIMG = "oscdimg"
    CONVERTER = "uup-converter-wimlib"
    REG = "reg"


class EngineKind(StrEnum):
    """映像引擎实现（引擎 §3.1 路由表的主/备角色）。"""

    WIMLIB = "wimlib"
    DISM = "dism"


class PresetKind(StrEnum):
    """预设类别（配置 §4.1–§4.2：四类预设，各自独立目录）。

    取值与 ``presets\\`` 下的子目录同名，便于直接映射存储路径。
    """

    SLIM = "slim"
    OPTIMIZE = "optimize"
    INTEGRATE = "integrate"
    UNATTEND = "unattend"
