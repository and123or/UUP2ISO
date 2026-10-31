"""进程后端（进程 §3、§11）：真实启动 + 夹具回放。

把「怎么起进程」与「怎么编排进程」分开，才能在不依赖真实镜像/工具的前提下做回归：
:class:`RealBackend` 用 ``asyncio.create_subprocess_exec`` 起真进程，
:class:`FakeBackend` 按 `.jsonl` 夹具回放 stdout/stderr（进程 §11 要求的 ``FakeProcessBackend``）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from uup2iso.core.models.enums import ToolKind

__all__ = [
    "ByteStream",
    "FakeBackend",
    "ProcessBackend",
    "RealBackend",
    "RunSpec",
    "ScriptedStep",
    "SpawnedProcess",
    "load_script",
]

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200


@dataclass(frozen=True, slots=True)
class RunSpec:
    """一次外部工具调用。

    Attributes:
        tool: 工具种类（决定编码、IO 画像、并发资源）。
        args: 完整命令行参数，**不经过 shell**（进程 §3）。
        cwd: 工作目录，必须是任务工作目录，禁止用程序目录（防工具写垃圾）。
        env: 覆盖的环境变量；``None`` 表示继承。敏感值走参数不走 env（进程 §3）。
        stage: 所属阶段名，仅用于错误对象与日志。
        version_known: 工具版本是否已登记，影响行分类的兜底策略（进程 §5.2）。
    """

    tool: ToolKind
    args: tuple[str, ...]
    cwd: Path
    env: dict[str, str] | None = None
    stage: str | None = None
    version_known: bool = True


@runtime_checkable
class ByteStream(Protocol):
    """只读字节流（``stdout`` / ``stderr``）。"""

    async def read(self, size: int) -> bytes:
        """读至多 ``size`` 字节；EOF 返回 ``b""``。"""
        ...


@runtime_checkable
class SpawnedProcess(Protocol):
    """已启动的进程句柄。"""

    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    @property
    def stdout(self) -> ByteStream | None: ...

    @property
    def stderr(self) -> ByteStream | None: ...

    async def wait(self) -> int:
        """等待退出并返回原始返回码。"""
        ...

    def terminate(self) -> None:
        """请求终止（对应 ``TerminateProcess``）。"""
        ...

    def kill(self) -> None:
        """强杀。"""
        ...

    def send_ctrl_break(self) -> bool:
        """向进程组投递 Ctrl-Break；不可用时返回 ``False``（进程 §7.1 第一步）。"""
        ...


@runtime_checkable
class ProcessBackend(Protocol):
    """进程启动后端。"""

    async def spawn(self, spec: RunSpec) -> SpawnedProcess:
        """启动进程；创建失败应抛 ``OSError`` 由上层归类。"""
        ...

    async def kill_tree(self, pid: int) -> None:
        """强制终止进程树（含孙进程，进程 §7.1）。"""
        ...

    def has_console(self) -> bool:
        """当前进程是否挂在控制台上；决定取消流程能否用 Ctrl-Break（进程 §7.1）。"""
        ...


# --------------------------------------------------------------------------
# 真实后端
# --------------------------------------------------------------------------


class _AsyncioSpawned:
    """``asyncio.subprocess.Process`` 的薄适配。"""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    @property
    def stdout(self) -> ByteStream | None:
        return self._process.stdout

    @property
    def stderr(self) -> ByteStream | None:
        return self._process.stderr

    async def wait(self) -> int:
        return await self._process.wait()

    def terminate(self) -> None:
        if self._process.returncode is None:
            self._process.terminate()

    def kill(self) -> None:
        if self._process.returncode is None:
            self._process.kill()

    def send_ctrl_break(self) -> bool:
        try:
            self._process.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError, AttributeError):
            return False
        return True


class RealBackend:
    """用 ``asyncio.create_subprocess_exec`` 起真实进程（进程 §3 启动契约）。"""

    def __init__(self) -> None:
        self._console = _has_console()

    def has_console(self) -> bool:
        return self._console

    async def spawn(self, spec: RunSpec) -> SpawnedProcess:
        creationflags = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        process = await asyncio.create_subprocess_exec(
            *spec.args,
            cwd=str(spec.cwd),
            env=spec.env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
        return _AsyncioSpawned(process)

    async def kill_tree(self, pid: int) -> None:
        """``taskkill /T /F`` 杀整棵树。

        这是进程层自己的清理动作（不是被编排的业务工具），因此不进 ProcessRunner，
        避免自我递归。
        """
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/T",
                "/F",
                "/PID",
                str(pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
            )
            await killer.wait()
        except OSError:
            return


def _has_console() -> bool:
    """Windows 下用 ``GetConsoleWindow`` 判断是否挂在控制台。

    GUI（pythonw / 窗口子系统）没有控制台，此时 Ctrl-Break 必然失败，
    取消流程必须直接从 terminate 开始（进程 §7.1「控制台前提」）。
    """
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except (AttributeError, OSError):
        return False


# --------------------------------------------------------------------------
# 夹具回放后端
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScriptedStep:
    """夹具中的一条事件：某通道输出一段字节。"""

    channel: str
    data: bytes
    delay_seconds: float = 0.0


def load_script(path: Path) -> tuple[list[ScriptedStep], int]:
    """读取 ``.jsonl`` 夹具，返回 (事件序列, 退出码)。

    每行支持三种写法：

    * ``{"channel":"stdout","bytes":"48656c6c6f"}`` —— ``bytes`` 为十六进制原始字节
      （用于编码/非法字节用例）；
    * ``{"channel":"stderr","text":"失败"}`` —— UTF-8 文本（书写方便）；
    * ``{"delay_ms":120}`` —— 给**下一条**事件加延迟；``{"exit":87}`` 指定退出码。
    """
    steps: list[ScriptedStep] = []
    exit_code = 0
    pending_delay = 0.0

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        record = json.loads(line)
        if "delay_ms" in record:
            # delay_ms 是**修饰符**：同一行仍可携带 channel/text，不能整条丢弃
            pending_delay += float(record["delay_ms"]) / 1000
            if not any(key in record for key in ("bytes", "text")):
                continue
        if "exit" in record:
            exit_code = int(record["exit"])
            continue
        channel = str(record.get("channel", "stdout"))
        if "bytes" in record:
            data = bytes.fromhex(str(record["bytes"]))
        else:
            data = str(record.get("text", "")).encode("utf-8")
        steps.append(ScriptedStep(channel, data, pending_delay))
        pending_delay = 0.0

    return steps, exit_code


class _FakeStream:
    """按脚本逐段吐出字节；耗尽后返回 EOF。"""

    def __init__(self, chunks: list[tuple[bytes, float]], process: _FakeSpawned) -> None:
        self._chunks = chunks
        self._process = process

    async def read(self, size: int) -> bytes:
        if self._process.terminated:
            return b""
        if not self._chunks:
            return b""
        data, delay = self._chunks[0]
        if delay:
            await asyncio.sleep(delay)
        if self._process.terminated:
            return b""
        self._chunks.pop(0)
        return data


@dataclass
class _FakeSpawned:
    """夹具驱动的假进程。"""

    script: list[ScriptedStep]
    exit_code: int
    _finished: asyncio.Event = field(default_factory=asyncio.Event)
    terminated: bool = False
    _returncode: int | None = None

    def __post_init__(self) -> None:
        stdout = [
            (step.data, step.delay_seconds) for step in self.script if step.channel == "stdout"
        ]
        stderr = [
            (step.data, step.delay_seconds) for step in self.script if step.channel != "stdout"
        ]
        self._stdout = _FakeStream(stdout, self)
        self._stderr = _FakeStream(stderr, self)

    # --- SpawnedProcess 接口 ---

    @property
    def pid(self) -> int:
        """夹具进程使用固定 PID（不占用真实进程号）。"""
        return 424242

    @property
    def returncode(self) -> int | None:
        return self._returncode

    @property
    def stdout(self) -> ByteStream:
        return self._stdout

    @property
    def stderr(self) -> ByteStream:
        return self._stderr

    async def wait(self) -> int:
        total_delay = sum(step.delay_seconds for step in self.script)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._finished.wait(), timeout=total_delay + 0.05)
        if self._returncode is None:
            self._returncode = self.exit_code
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = -15
        self._finished.set()

    def kill(self) -> None:
        self.terminated = True
        self._returncode = -9
        self._finished.set()

    def send_ctrl_break(self) -> bool:
        self.terminated = True
        self._returncode = -1073741510
        self._finished.set()
        return True


class FakeBackend:
    """夹具回放后端（进程 §11）。

    Args:
        script: 事件序列；为空时进程立即正常退出。
        exit_code: 退出码。
        console: 模拟「是否有控制台」，用于覆盖取消流程的两条分支。
    """

    def __init__(
        self,
        script: list[ScriptedStep] | None = None,
        *,
        exit_code: int = 0,
        console: bool = False,
    ) -> None:
        self._script = script or []
        self._exit_code = exit_code
        self._console = console
        self.killed: list[int] = []

    def has_console(self) -> bool:
        return self._console

    async def spawn(self, spec: RunSpec) -> SpawnedProcess:
        return _FakeSpawned(list(self._script), self._exit_code)

    async def kill_tree(self, pid: int) -> None:
        self.killed.append(pid)
