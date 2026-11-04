"""`Convert` 阶段处理器（路线 A：交给已验证转换器聚合）。

**为什么不自己聚合**：`install.wim` 需要把几十个 `.ESD` 组件按映射规则拼装，
映射表属转换器内部知识（见 `docs/记录/转换路线验证.md` 的实测结论）。
自己复刻等于重写一遍且无法验证，所以本处理器只做**编排**：

```
config    生成 ConvertConfig.ini（由 TaskConfig 映射，不手写键值）
cab       解包转换器本体（uup-converter-wimlib.7z → bin/）
aggregate 调用 convert-UUP.cmd <输入目录>（AutoStart=3 仅出 install.wim）
skeleton  校验 ISO 骨架产物（转换器内部完成，我们只验收）
bootwim   校验 boot.wim 产物（同上）
```

参考的转换器方案（`tools/uup-converter-wimlib.7z` 解包后的 `convert-UUP.cmd`）：
* 输入目录可由**参数**指定（第 1 个参数，需目录下含 `*.esd`），因此**不必复制 9 GB 数据**；
* 全部行为由 `ConvertConfig.ini` 的 `[convert-UUP]` / `[Store_Apps]` 段驱动；
* `AutoStart`：1=ISO+install.wim，2=ISO+install.esd，3=仅 install.wim，4=仅 install.esd；
* 依赖自带的 `bin\\`（7z、wimlib、cdimage、bcdedit 等），脚本自己把 `bin` 加进 PATH。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uup2iso.application.pipeline.handlers import StageContext
from uup2iso.application.pipeline.orchestrator import FileOp
from uup2iso.core.errors import ErrorCode, StageExecutionError
from uup2iso.core.models.config import ConvertSection, TaskConfig
from uup2iso.core.models.enums import CompressionKind, EntryPoint, ToolKind
from uup2iso.core.models.pipeline import StageKind
from uup2iso.infrastructure.process.backend import RunSpec
from uup2iso.infrastructure.process.runner import ProcessRunner

__all__ = ["ConvertOptions", "ConvertStageHandler", "converter_paths"]

_CONVERTER_ARCHIVE = "uup-converter-wimlib.7z"
_CONVERTER_ENTRY = "convert-UUP.cmd"
_SEVEN_ZIP = "7zr.exe"
_INI_NAME = "ConvertConfig.ini"


@dataclass(frozen=True, slots=True)
class ConvertOptions:
    """`ConvertConfig.ini` 的强类型视图（键名与转换器完全一致）。"""

    auto_start: int = 3
    """1=ISO+install.wim，2=ISO+install.esd，3=仅 wim，4=仅 esd。"""
    add_updates: int = 0
    """0=不集成；1=集成到映像；2=外挂到 ISO 分发。"""
    cleanup: int = 0
    reset_base: int = 0
    netfx3: int = 0
    start_virtual: int = 0
    wim2esd: int = 0
    wim2swm: int = 0
    skip_iso: int = 1
    skip_winre: int = 0
    lcu_winre: int = 0
    lcu_msu_expand: int = 0
    skip_lcu_msu: int = 0
    skip_edge: int = 0
    add_drivers: int = 0
    drv_source: str = ""
    auto_exit: int = 1
    disable_updating_upgrade: int = 1
    skip_apps: int = 1
    apps_level: int = 0
    stub_apps_full: int = 0
    custom_list: int = 0

    def to_ini(self) -> str:
        """生成 ini 文本（段落顺序与官方模板一致，便于人工比对）。"""
        lines = [
            "[convert-UUP]",
            f"AutoStart    ={self.auto_start}",
            f"AddUpdates   ={self.add_updates}",
            f"Cleanup      ={self.cleanup}",
            f"ResetBase    ={self.reset_base}",
            f"NetFx3       ={self.netfx3}",
            f"StartVirtual ={self.start_virtual}",
            f"wim2esd      ={self.wim2esd}",
            f"wim2swm      ={self.wim2swm}",
            f"SkipISO      ={self.skip_iso}",
            f"SkipWinRE    ={self.skip_winre}",
            f"LCUwinre     ={self.lcu_winre}",
            f"LCUmsuExpand ={self.lcu_msu_expand}",
            f"SkipLCUmsu   ={self.skip_lcu_msu}",
            f"SkipEdge     ={self.skip_edge}",
            f"AutoExit     ={self.auto_exit}",
            f"DisableUpdatingUpgrade={self.disable_updating_upgrade}",
            f"AddDrivers   ={self.add_drivers}",
            f"Drv_Source   ={self.drv_source}",
            "",
            "[Store_Apps]",
            f"SkipApps     ={self.skip_apps}",
            f"AppsLevel    ={self.apps_level}",
            f"StubAppsFull ={self.stub_apps_full}",
            f"CustomList   ={self.custom_list}",
            "",
        ]
        return "\r\n".join(lines)


def options_from(convert: ConvertSection | None) -> ConvertOptions:
    """把 `TaskConfig.convert` 映射成 ini 选项（缺省段按最保守值）。"""
    if convert is None:
        return ConvertOptions()

    full_iso = convert.mode == "full"
    esd_output = convert.compression is CompressionKind.LZMS
    updates_mode = convert.updates.mode if convert.updates.integrate else 0
    add_updates = (
        0
        if not convert.updates.integrate
        else (2 if isinstance(updates_mode, str) and updates_mode == "external" else 1)
    )

    return ConvertOptions(
        auto_start=(2 if esd_output else 1) if full_iso else (4 if esd_output else 3),
        add_updates=add_updates,
        cleanup=1 if convert.cleanup.component_cleanup else 0,
        reset_base=1 if convert.cleanup.reset_base else 0,
        netfx3=1 if convert.features.netfx3 else 0,
        wim2esd=1 if esd_output else 0,
        skip_iso=0 if full_iso else 1,
        skip_winre=1 if convert.winre.skip else 0,
        lcu_winre=1 if convert.winre.update else 0,
        skip_edge=1 if convert.apps.skip else 0,
        add_drivers=1 if convert.drivers.enabled else 0,
        drv_source=convert.drivers.source_dir or "",
        skip_apps=1 if convert.apps.skip else 0,
        apps_level=convert.apps.level,
        stub_apps_full=1 if convert.apps.stub_full else 0,
        custom_list=1 if convert.apps.custom_list else 0,
    )


@dataclass(frozen=True, slots=True)
class ConverterPaths:
    """转换器在工作目录中的布局。"""

    root: Path
    """`<work>/convert`。"""
    entry: Path
    """`convert-UUP.cmd` 的落位。"""
    ini: Path
    archive: Path
    seven_zip: Path

    @property
    def bin_dir(self) -> Path:
        return self.root / "bin"

    @property
    def iso_folder(self) -> Path:
        return self.root / "ISOFOLDER"


def converter_paths(tools_dir: Path, work_dir: Path) -> ConverterPaths:
    root = work_dir / "convert"
    return ConverterPaths(
        root=root,
        entry=root / _CONVERTER_ENTRY,
        ini=root / _INI_NAME,
        archive=tools_dir / _CONVERTER_ARCHIVE,
        seven_zip=tools_dir / _SEVEN_ZIP,
    )


class ConvertStageHandler:
    """`StageHandler` 的 `Convert` 实现（编排转换器，不自己聚合）。"""

    stage = StageKind.CONVERT

    def __init__(
        self,
        *,
        tools_dir: Path | None = None,
        runner: ProcessRunner | None = None,
        log: Any = None,
        admin_check: object | None = None,
    ) -> None:
        self._tools_dir = tools_dir
        self._runner = runner
        self._log = log
        self._admin_check: object = admin_check or is_admin
        self.outcome: dict[str, Any] | None = None

    # -- StageHandler -----------------------------------------------------

    async def run(self, ctx: StageContext) -> None:
        paths = converter_paths(self._require_tools(ctx), ctx.work_dir)
        executed = {substep.name for substep in ctx.plan.executed_substeps}
        options = options_from(getattr(ctx.config, "convert", None))
        source = _resolve_input_dir(ctx.config)

        # 顺序关键：**先解包再写 ini**。包内自带 ConvertConfig.ini（AutoStart=0），
        # 若先写后被解包覆盖，转换器就会退回交互菜单（实测踩过）。
        if "config" in executed or "cab" in executed:
            unpack_converter(paths)
        if "config" in executed:
            write_config_ini(paths, options)
        if "aggregate" in executed:
            if not self._is_admin():
                raise StageExecutionError(
                    "转换器需要管理员权限（内部会调用 DISM；非管理员它会弹 UAC 新窗口导致本进程失败）。"
                    "请以管理员身份重新运行本程序。",
                    code=ErrorCode.PERMISSION_DENIED,
                )
            await self._invoke(ctx, paths, source)
        if "skeleton" in executed:
            _require_exists(paths.iso_folder, "ISO 骨架目录", "skeleton")
        if "bootwim" in executed:
            _require_any(paths.root, "boot.wim", "bootwim")

        self.outcome = {
            "root": str(paths.root),
            "install_wim": _find_first(paths.root, "install.wim"),
            "install_esd": _find_first(paths.root, "install.esd"),
            "iso": _find_first(paths.root, "*.iso"),
        }

    # -- 预演 -------------------------------------------------------------

    def describe(self, ctx: StageContext) -> tuple[str, ...]:
        """`--dry-run`：完整命令序列（**不执行**）。"""
        paths = converter_paths(self._tools_dir or Path("tools"), ctx.work_dir)
        options = options_from(getattr(ctx.config, "convert", None))
        source = _resolve_input_dir(ctx.config)
        ini_flags = (
            f"AutoStart={options.auto_start} SkipISO={options.skip_iso} "
            f"AddUpdates={options.add_updates} Cleanup={options.cleanup} "
            f"wim2esd={options.wim2esd} SkipApps={options.skip_apps}"
        )

        return (
            f'"{paths.seven_zip}" x "{paths.archive}" -o"{paths.root}" -y',
            f"# 写入 {paths.ini}（{ini_flags}）",
            f'cmd.exe /c "{paths.entry.name}" "{source}"  # cwd={paths.root}',
            f'wimlib-imagex info "{paths.root / "ISOFOLDER" / "sources" / "install.wim"}"',
            f'# 可选："{paths.bin_dir / "cdimage.exe"}" -b... # 由转换器内部调用',
        )

    def file_operations(self, ctx: StageContext) -> tuple[FileOp, ...]:
        paths = converter_paths(self._tools_dir or Path("tools"), ctx.work_dir)
        ops = [
            FileOp("create", str(paths.ini), "由 TaskConfig 生成的转换器配置"),
            FileOp("create", str(paths.bin_dir), "转换器自带 bin（7z/wimlib/cdimage…）"),
            FileOp("create", str(paths.iso_folder), "ISO 骨架目录（转换器内部创建）"),
        ]
        convert = getattr(ctx.config, "convert", None)
        if convert is not None and convert.mode == "full":
            ops.append(FileOp("create", str(paths.root / "install.iso"), "转换器产出的 ISO"))
        return tuple(ops)

    def reusable_artifacts(self, ctx: StageContext) -> tuple[str, ...]:
        """已存在即可复用的产物（续跑用）。"""
        root = ctx.work_dir / "convert"
        found = []
        for name in ("install.wim", "install.esd"):
            if _find_first(root, name):
                found.append(f"convert/{name}")
        return tuple(found)

    # -- 内部 -------------------------------------------------------------

    async def _invoke(self, ctx: StageContext, paths: ConverterPaths, source: Path) -> None:
        if self._runner is None:
            raise StageExecutionError(
                "Convert 缺少 ProcessRunner（组合根未接线）", code=ErrorCode.INTERNAL_ERROR
            )
        _require_exists(paths.entry, "转换器脚本", "aggregate")
        spec = RunSpec(
            tool=ToolKind.CONVERTER,
            args=("cmd.exe", "/c", paths.entry.name, str(source)),
            cwd=paths.root,
            stage="Convert",
        )
        result = await self._runner.run(
            spec, on_event=lambda event: self._relay(ctx, event), check=False
        )
        if getattr(result, "returncode", 0) != 0:
            raise StageExecutionError(
                f"转换器退出码 {getattr(result, 'returncode', '?')}（详见日志）",
                code=ErrorCode.TOOL_FAILED,
            )

    def _relay(self, ctx: StageContext, event: object) -> None:
        kind = getattr(getattr(event, "kind", None), "value", "")
        text = getattr(event, "text", "")
        if kind == "progress":
            progress: Any = ctx.progress
            if progress is not None:
                progress.report("Convert", percent=None, message=text)
        sink: Any = ctx.log
        if sink is not None and isinstance(text, str) and text:
            sink.info("app.convert", text, stage="Convert", event="converter")

    def _is_admin(self) -> bool:
        check = self._admin_check
        return bool(check()) if callable(check) else True

    def _require_tools(self, ctx: StageContext) -> Path:
        tools = self._tools_dir
        if tools is None:
            raise StageExecutionError(
                "Convert 缺少 tools 目录（组合根未接线）", code=ErrorCode.INTERNAL_ERROR
            )
        return tools


# ---------------------------------------------------------------------------
# 辅助（预演与执行共用）
# ---------------------------------------------------------------------------


def is_admin() -> bool:
    """当前进程是否管理员（Windows）。

    转换器非管理员时会用 `start ... -verb runas` 启一个**新窗口**提权，
    原进程随即失败——所以我们在调用前先拦。
    """
    import ctypes
    import sys

    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def write_config_ini(paths: ConverterPaths, options: ConvertOptions) -> Path:
    """原子写 `ConvertConfig.ini`（转换器按 `findstr` 逐键读取）。"""
    paths.root.mkdir(parents=True, exist_ok=True)
    path = paths.ini
    tmp = path.with_suffix(".ini.tmp")
    # 必须**无 BOM**：转换器用 findstr 读首行 `[convert-UUP]`，BOM 会让匹配失败 → 退回交互菜单
    tmp.write_bytes(options.to_ini().encode("ascii"))
    tmp.replace(path)
    return path


def unpack_converter(paths: ConverterPaths) -> Path:
    """用 `7zr.exe` 解包转换器（幂等：已解包则跳过）。"""
    if paths.entry.is_file() and paths.bin_dir.is_dir():
        return paths.root
    _require_exists(paths.archive, "转换器压缩包", "cab")
    _require_exists(paths.seven_zip, "7zr.exe", "cab")
    paths.root.mkdir(parents=True, exist_ok=True)
    import subprocess  # noqa: S404 - 仅用于解包，前置检查已在上方

    subprocess.run(  # noqa: S603
        [str(paths.seven_zip), "x", str(paths.archive), f"-o{paths.root}", "-y"],
        check=True,
        capture_output=True,
    )
    return paths.root


def _resolve_input_dir(config: TaskConfig) -> Path:
    """入口 B 的 UUP 目录；入口 A 需要先 Fetch 出同结构目录。"""
    folder = getattr(config.input, "uup_folder", None)
    if folder is None:
        if config.input.mode is EntryPoint.ONLINE:
            raise StageExecutionError(
                "Convert 需要已下载的 UUP 目录：请先跑 Fetch",
                code=ErrorCode.CONFIG_SEMANTIC_INVALID,
            )
        raise StageExecutionError(
            "Convert 缺少 UUP 输入目录", code=ErrorCode.CONFIG_SEMANTIC_INVALID
        )
    path = Path(getattr(folder, "path", ""))
    if not path.is_dir():
        raise StageExecutionError(f"UUP 目录不存在：{path}", code=ErrorCode.INPUT_UNREADABLE)
    return path


def _require_exists(path: Path, label: str, substep: str) -> None:
    if not path.exists():
        raise StageExecutionError(
            f"{substep} 子步骤缺少{label}：{path}", code=ErrorCode.INPUT_DAMAGED
        )


def _require_any(root: Path, name: str, substep: str) -> None:
    if _find_first(root, name) is None:
        raise StageExecutionError(
            f"{substep} 子步骤未产出 {name}（转换器输出可能与预期不同，请查看日志）",
            code=ErrorCode.TOOL_FAILED,
        )


def _find_first(root: Path, pattern: str) -> str | None:
    if not root.is_dir():
        return None
    for item in sorted(root.rglob(pattern)):
        if item.is_file():
            return str(item)
    return None
