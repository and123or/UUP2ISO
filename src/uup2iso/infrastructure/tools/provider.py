"""`ToolProvider`：工具定位、校验、版本/能力探测与自检报告（引擎 §9.1、进程 §2.1–§2.4）。

三件事分开做，避免互相污染：

* :meth:`ToolProvider.locate` —— 纯查询，不写盘、不起进程（``IToolProvider`` 的同步契约）；
* :meth:`ToolProvider.ensure` —— 需要「真的能用」时调用，会探测版本，缺工具抛 ``TOOL_MISSING``；
* :meth:`ToolProvider.probe_all` —— 启动自检，产出可逐行写日志的 :class:`ToolReport`。

为什么 ``capabilities()`` 是同步的：接口在 `IToolProvider` 里定义为同步。版本探测是异步的，
所以**必须先 ``await probe_all()``**；未探测就查能力会得到保守值（宁可不支持，也不假装支持）。
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
import re
import shutil
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from uup2iso.core.models.enums import EngineKind, ToolKind
from uup2iso.core.models.tools import EngineCapabilities, ToolInfo, ToolSource
from uup2iso.infrastructure.process.backend import RunSpec
from uup2iso.infrastructure.process.errors import ToolMissingError
from uup2iso.infrastructure.process.runner import ProcessRunner
from uup2iso.infrastructure.tools.capabilities import capabilities_for, parse_version
from uup2iso.infrastructure.tools.lock import LockedTool, ToolLock, load_lock

if TYPE_CHECKING:  # 避免与 fetcher → provider 的导入形成环
    from uup2iso.infrastructure.tools.fetcher import ToolFetcher

__all__ = [
    "TOOL_EXECUTABLES",
    "VERSION_ARGS",
    "CapabilityDegraded",
    "ToolCheck",
    "ToolProvider",
    "ToolReport",
    "describe_missing",
]

TOOL_EXECUTABLES: dict[ToolKind, tuple[str, ...]] = {
    ToolKind.ARIA2C: ("aria2c.exe",),
    ToolKind.SEVEN_ZIP: ("7z.exe", "7za.exe"),
    ToolKind.WIMLIB: ("wimlib-imagex.exe",),
    ToolKind.DISM: ("Dism.exe",),
    ToolKind.OSCDIMG: ("oscdimg.exe",),
    ToolKind.REG: ("reg.exe",),
    ToolKind.CONVERTER: (),
}
"""各工具的可执行文件名（顺序即候选顺序）。转换器是目录，单独处理。"""

VERSION_ARGS: dict[ToolKind, tuple[str, ...]] = {
    ToolKind.ARIA2C: ("--version",),
    ToolKind.SEVEN_ZIP: ("i",),
    ToolKind.WIMLIB: ("--version",),
    ToolKind.DISM: ("/Version",),
    ToolKind.REG: (),
    ToolKind.OSCDIMG: (),
    ToolKind.CONVERTER: (),
}
"""版本探测命令（进程 §2.2）。oscdimg 无版本参数，需读 PE 资源（本轮记 unknown）。"""

_VERSION_PATTERNS: dict[ToolKind, re.Pattern[str]] = {
    ToolKind.ARIA2C: re.compile(r"aria2 version (\d+\.\d+\.\d+)"),
    ToolKind.SEVEN_ZIP: re.compile(r"7-Zip(?: \[[^\]]+\])? (\d+\.\d+)"),
    ToolKind.WIMLIB: re.compile(r"wimlib-imagex (\d+\.\d+\.\d+)"),
    ToolKind.DISM: re.compile(r"Version:\s*(\d+\.\d+\.\d+\.\d+)"),
}

_CONVERTER_DIR = "uup-converter-wimlib"
_CONVERTER_VERSION_FILE = "version.txt"

_SYSTEM32 = Path(os.environ.get("SystemRoot", r"C://Windows")) / "System32"

_ELEVATION_REQUIRED = 740
"""Windows ERROR_ELEVATION_REQUIRED：DISM 在非管理员下直接失败（进程 §2.4）。"""

_REQUIRED: frozenset[ToolKind] = frozenset(
    {ToolKind.ARIA2C, ToolKind.SEVEN_ZIP, ToolKind.WIMLIB, ToolKind.DISM}
)
"""必需工具：缺失即阻断（FR10.3）。``oscdimg`` 可选，转换器随包。"""


@dataclass(frozen=True, slots=True)
class _ProbeResult:
    """一次版本探测的原始结果。

    需要 ``head``：aria2c 的版本号是**第一行**，而 ``ProcessResult.last_lines`` 只保留最后
    20 行，光看尾部会把它截掉（实测踩到）。
    """

    version: str | None
    returncode: int | None
    head: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CapabilityDegraded:
    """一条降级记录（引擎 §3.2 备用启用、§5 保守模式限制都记这个）。"""

    tool: ToolKind
    capability: str
    reason: str

    def as_text(self) -> str:
        return f"capability_degraded tool={self.tool.value} capability={self.capability} reason={self.reason}"


@dataclass(frozen=True, slots=True)
class ToolCheck:
    """单个工具的自检结果。"""

    kind: ToolKind
    found: bool
    path: Path | None = None
    source: str | None = None
    version: str | None = None
    problem: str | None = None
    managed: bool = False
    """是否在锁定清单中（``False`` 表示「未纳入哈希管理」，需告警）。"""

    @property
    def ok(self) -> bool:
        return self.found and self.problem is None

    @property
    def required(self) -> bool:
        return self.kind in _REQUIRED

    def as_log_line(self) -> str:
        """一行可写进日志的事实（不写浮点/进度，只写关键字段）。"""
        parts = [
            "event=tool_check",
            f"tool={self.kind.value}",
            f"found={'yes' if self.found else 'no'}",
        ]
        if self.source:
            parts.append(f"source={self.source}")
        parts.append(f"version={self.version or 'unknown'}")
        parts.append(f"managed={'yes' if self.managed else 'no'}")
        if self.path is not None:
            parts.append(f"path={self.path}")
        if self.problem:
            parts.append(f"problem={self.problem}")
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class ToolReport:
    """启动自检报告。

    Attributes:
        checks: 每个工具一条。
        degradations: 降级项（备用启用 / 能力缺失）。
        warnings: 其它告警（清单缺失、清单被篡改、版本未知等）。
    """

    checks: tuple[ToolCheck, ...] = ()
    degradations: tuple[CapabilityDegraded, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def missing_required(self) -> tuple[ToolKind, ...]:
        return tuple(
            check.kind for check in self.checks if check.required and not check.ok
        )

    @property
    def blocked(self) -> bool:
        """必需工具缺失即阻断任务（``TOOL_MISSING``，退出码 4）。"""
        return bool(self.missing_required)

    def log_lines(self) -> tuple[str, ...]:
        """可逐行写日志的文本（``event=...`` 形式，便于 grep）。"""
        lines = [check.as_log_line() for check in self.checks]
        lines.extend(item.as_text() for item in self.degradations)
        lines.extend(f"WARN {item}" for item in self.warnings)
        return tuple(lines)

    def check_for(self, kind: ToolKind) -> ToolCheck | None:
        return next((check for check in self.checks if check.kind is kind), None)


def describe_missing(kind: ToolKind) -> str:
    """缺失工具的处理指引（引擎 §9.2：失败要给手动指引，不能只说「找不到」）。"""
    if kind is ToolKind.OSCDIMG:
        return (
            "缺少 oscdimg.exe：它随 Windows ADK 的「部署工具」组件安装，"
            "本程序不自动获取（ADK 许可不允许再分发）。请安装 ADK 后重试。"
        )
    if kind is ToolKind.CONVERTER:
        return (
            "缺少 uup-converter-wimlib：转换器随程序包分发且版本锁定，"
            "请重新解压完整程序包（不会自动下载）。"
        )
    if kind is ToolKind.DISM:
        return "找不到 Dism.exe：请确认系统为 Windows 10/11，并以管理员身份运行本程序。"
    return (
        f"缺少 {kind.value}：请将其放入程序目录的 tools\\ 下，"
        "或在设置中指定路径；缺失时任务无法开始（不会静默降级）。"
    )


class ToolProvider:
    """``IToolProvider`` 的实现。

    Args:
        tools_dir: 程序目录下的 ``tools\\``。
        settings: 全局设置的扁平视图（键 ``tools.<name>`` 为显式路径）。
        lock: 覆盖锁定清单；``None`` 时从 ``tools_dir/tools.lock.json`` 读取。
        runner: 进程执行器；``None`` 时按默认构造（真实进程）。
        cache_path: 能力缓存 ``config\\tool_caps.json``。
        search_path: 是否在系统 ``PATH`` 中查找（测试通常关掉）。
        expected_lock_identity: 编译期常量；``None`` 表示不校验清单身份。
        probe_timeout: 单次版本探测超时（进程 §2.3：10 秒）。
    """

    def __init__(
        self,
        *,
        tools_dir: Path,
        settings: Mapping[str, str] | None = None,
        lock: ToolLock | None = None,
        runner: ProcessRunner | None = None,
        cache_path: Path | None = None,
        search_path: bool = True,
        expected_lock_identity: str | None = None,
        probe_timeout: float = 10.0,
        admin: bool | None = None,
        fetcher: ToolFetcher | None = None,
    ) -> None:
        self._tools_dir = tools_dir
        self._settings = dict(settings or {})
        self._runner = runner or ProcessRunner()
        self._cache_path = cache_path
        self._search_path = search_path
        self._probe_timeout = probe_timeout

        if lock is not None:
            self._lock = lock
            self._lock_warnings: tuple[str, ...] = ()
        else:
            result = load_lock(tools_dir / "tools.lock.json", expected_identity=expected_lock_identity)
            self._lock = result.lock
            self._lock_warnings = result.warnings

        self._located: dict[ToolKind, ToolInfo | None] = {}
        self._problems: dict[ToolKind, str] = {}
        self._versions: dict[ToolKind, str | None] = {}
        self._admin = admin
        self._fetcher = fetcher
        self._load_cache()

    # -- IToolProvider ----------------------------------------------------

    def locate(self, kind: ToolKind) -> ToolInfo | None:
        """按 ``tools\\ → 设置 → PATH`` 查找（进程 §2.1），不做哈希校验以外的副作用。"""
        if kind in self._located:
            return self._located[kind]

        info, problem = self._search(kind)
        self._located[kind] = info if problem is None else None
        if problem is not None:
            self._problems[kind] = problem
        return self._located[kind]

    def is_available(self, kind: ToolKind) -> bool:
        return self.locate(kind) is not None

    def capabilities(self, engine: EngineKind) -> EngineCapabilities:
        """取引擎能力；**未探测过版本时返回保守值**（调用方应先 ``probe_all``）。"""
        tool = ToolKind.WIMLIB if engine is EngineKind.WIMLIB else ToolKind.DISM
        version_text = self._versions.get(tool)
        return capabilities_for(engine, parse_version(version_text))

    async def ensure(self, kind: ToolKind) -> ToolInfo:
        """确保工具可用并补上版本；缺失即抛 ``TOOL_MISSING``（含手动指引）。"""
        info = self.locate(kind)
        if info is None and self._fetcher is not None:
            # WP0-T8：必需且清单登记的工具可以自动补齐；失败仍在下面抛错
            await self._fetcher.fetch(kind)
            self._located.pop(kind, None)
            self._problems.pop(kind, None)
            info = self.locate(kind)

        if info is None:
            problem = self._problems.get(kind)
            detail = describe_missing(kind)
            if problem:
                detail = f"{detail}（当前问题：{problem}）"
            raise ToolMissingError(
                detail,
                tool=kind.value,
                file=info.path if info else None,
            )

        if kind not in self._versions:
            probe = await self._probe_version(kind, info)
            self._versions[kind] = probe.version
            self._store_cache()
        return ToolInfo(
            kind=info.kind,
            path=info.path,
            source=info.source,
            version=self._versions.get(kind),
            sha256=info.sha256,
        )

    # -- 自检 -------------------------------------------------------------

    async def probe_all(self, kinds: Iterable[ToolKind] | None = None) -> ToolReport:
        """启动自检：定位 + 版本探测 + 能力推导，产出可写日志的报告。"""
        targets: Sequence[ToolKind] = tuple(kinds) if kinds is not None else tuple(ToolKind)
        checks: list[ToolCheck] = []
        degradations: list[CapabilityDegraded] = []
        warnings: list[str] = list(self._lock_warnings)

        for kind in targets:
            info = self.locate(kind)
            problem = self._problems.get(kind)
            if info is None:
                checks.append(
                    ToolCheck(kind=kind, found=False, problem=problem, managed=self._is_managed(kind))
                )
                continue

            probe = await self._probe_version(kind, info)
            version = probe.version
            self._versions[kind] = version
            managed = self._is_managed(kind)
            if not managed and kind in _REQUIRED:
                warnings.append(f"tool_unmanaged tool={kind.value}")
            if probe.returncode == _ELEVATION_REQUIRED:
                # 实测：非管理员运行 dism /Version 也会返回 740（进程 §2.4）
                degradations.append(
                    CapabilityDegraded(
                        tool=kind,
                        capability="admin",
                        reason="需要管理员权限：请以管理员身份运行本程序（进程 §2.4）",
                    )
                )
                warnings.append(f"tool_admin_required tool={kind.value} exit=740")
            elif version is None and VERSION_ARGS.get(kind):
                degradations.append(
                    CapabilityDegraded(
                        tool=kind,
                        capability="version",
                        reason="版本探测失败或超时，进入保守模式（引擎 §4）",
                    )
                )
            checks.append(
                ToolCheck(
                    kind=kind,
                    found=True,
                    path=info.path,
                    source=info.source.value,
                    version=version,
                    managed=managed,
                )
            )

        self._store_cache()
        self._append_capability_degradations(degradations)
        return ToolReport(
            checks=tuple(checks),
            degradations=tuple(degradations),
            warnings=tuple(warnings),
        )

    def route_availability(self) -> dict[ToolKind, bool]:
        """供 :func:`select_route` 使用的可用性快照。"""
        return {kind: self.is_available(kind) for kind in ToolKind}

    # -- 权限 -------------------------------------------------------------

    def is_admin(self) -> bool:
        """当前进程是否具备管理员令牌（进程 §2.4：DISM 需要）。"""
        if self._admin is not None:
            return self._admin
        if sys.platform != "win32":
            return False
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False

    # -- 内部：定位 -------------------------------------------------------

    def _search(self, kind: ToolKind) -> tuple[ToolInfo | None, str | None]:
        if kind is ToolKind.CONVERTER:
            return self._locate_converter()

        names = TOOL_EXECUTABLES.get(kind, ())
        for name in names:
            candidates: list[tuple[Path, ToolSource]] = [
                (self._tools_dir / name, ToolSource.PACKAGED),
                (self._tools_dir / kind.value / name, ToolSource.PACKAGED),
            ]
            configured = self._settings.get(f"tools.{kind.value}")
            if configured:
                candidates.append((Path(configured), ToolSource.CONFIGURED))

            for path, source in candidates:
                if path.is_file():
                    return self._verified(kind, path, source)

            if self._search_path:
                found = shutil.which(name)
                if found:
                    return self._verified(kind, Path(found), ToolSource.PATH)

            if kind is ToolKind.DISM:
                system = _SYSTEM32 / name
                if system.is_file():
                    return self._verified(kind, system, ToolSource.PATH)

        return None, "not_found"

    def _locate_converter(self) -> tuple[ToolInfo | None, str | None]:
        directory = self._tools_dir / _CONVERTER_DIR
        configured = self._settings.get(f"tools.{ToolKind.CONVERTER.value}")
        if configured:
            directory = Path(configured)
        if not directory.is_dir():
            return None, "not_found"

        version_file = directory / _CONVERTER_VERSION_FILE
        version: str | None = None
        if version_file.is_file():
            try:
                version = version_file.read_text(encoding="utf-8").strip() or None
            except OSError:
                version = None
        self._versions[ToolKind.CONVERTER] = version
        return (
            ToolInfo(
                kind=ToolKind.CONVERTER,
                path=directory,
                source=ToolSource.PACKAGED,
                version=version,
            ),
            None,
        )

    def _verified(
        self, kind: ToolKind, path: Path, source: ToolSource
    ) -> tuple[ToolInfo | None, str | None]:
        """命中白名单后做哈希校验；清单未登记则放行但标记 unmanaged（D1 决策）。"""
        digest = _sha256(path)
        entry: LockedTool | None = self._lock.entry_for(kind.value)
        # 注意：entry.sha256 是下载包哈希，不能拿来比 exe；只比 executable_sha256
        expected = entry.executable_sha256 if entry else ""
        if expected and digest != expected:
            return None, "hash_mismatch"

        return (
            ToolInfo(
                kind=kind,
                path=path,
                source=source,
                version=entry.version if entry else None,
                sha256=digest,
            ),
            None,
        )

    def _is_managed(self, kind: ToolKind) -> bool:
        entry = self._lock.entry_for(kind.value)
        return bool(entry and entry.sha256)

    # -- 内部：版本探测 ---------------------------------------------------

    async def _probe_version(self, kind: ToolKind, info: ToolInfo) -> _ProbeResult:
        if kind is ToolKind.CONVERTER:
            return _ProbeResult(version=info.version, returncode=0)
        args = VERSION_ARGS.get(kind)
        if not args or not _VERSION_PATTERNS.get(kind):
            return _ProbeResult(version=None, returncode=None)

        head: list[str] = []

        def _collect(event: object) -> None:
            text = getattr(event, "text", "")
            kind_value = getattr(event, "kind", None)
            # 只收非进度行，且最多 5 行（版本号通常在第一行）
            if kind_value is not None and getattr(kind_value, "value", "") == "progress":
                return
            if len(head) < 5 and isinstance(text, str):
                head.append(text)

        spec = RunSpec(
            tool=kind,
            args=(str(info.path), *args),
            cwd=self._tools_dir,
            version_known=False,
        )
        try:
            result = await asyncio.wait_for(
                self._runner.run(spec, check=False, on_event=_collect),
                timeout=self._probe_timeout,
            )
        except (TimeoutError, OSError):
            return _ProbeResult(version=None, returncode=None)

        text = "\n".join([*head, *result.last_lines])
        match = _VERSION_PATTERNS[kind].search(text)
        return _ProbeResult(
            version=match.group(1) if match else None,
            returncode=result.returncode,
            head=tuple(head),
        )

    # -- 内部：能力降级汇总 ------------------------------------------------

    def _append_capability_degradations(self, sink: list[CapabilityDegraded]) -> None:
        for engine in (EngineKind.WIMLIB, EngineKind.DISM):
            caps = self.capabilities(engine)
            if not caps.version_known:
                continue
            if engine is EngineKind.WIMLIB and not caps.can_write_esd:
                sink.append(
                    CapabilityDegraded(
                        tool=ToolKind.WIMLIB,
                        capability="esd_output",
                        reason="wimlib 版本低于写 LZMS 的阈值，ESD 输出不可用",
                    )
                )
            if engine is EngineKind.WIMLIB and not caps.supports_ref_export:
                sink.append(
                    CapabilityDegraded(
                        tool=ToolKind.WIMLIB,
                        capability="ref_export",
                        reason="wimlib 版本不支持 --ref 分片引用导出",
                    )
                )

    # -- 内部：能力缓存（进程 §2.3） ---------------------------------------

    def _load_cache(self) -> None:
        if self._cache_path is None or not self._cache_path.is_file():
            return
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for name, item in payload.items():
            if not isinstance(item, dict):
                continue
            try:
                kind = ToolKind(name)
            except ValueError:
                continue
            self._versions.setdefault(kind, item.get("version"))

    def _store_cache(self) -> None:
        if self._cache_path is None:
            return
        payload: dict[str, Any] = {}
        for kind, info in self._located.items():
            if info is None:
                continue
            stat = _stat(info.path)
            payload[kind.value] = {
                "version": self._versions.get(kind),
                "sha256": info.sha256,
                "size": stat[0],
                "mtime": stat[1],
            }
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_path.with_name(f"{self._cache_path.name}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self._cache_path)


def _sha256(path: Path) -> str:
    if path.is_dir():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stat(path: Path) -> tuple[int, float]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0.0)
    return (stat.st_size, stat.st_mtime)
