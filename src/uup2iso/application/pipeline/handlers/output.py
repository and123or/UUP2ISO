"""`Output` 阶段处理器（P2-1：先出 WIM）。

严格按 `输出校验与原子性规范` 的四步走：

```
assemble  定位产物、解析最终名、空间与同卷预检（不创建任何东西）
package   写入暂存区 <输出目录>\\.uup2iso-tmp\\<task>\\<name>.part（+ fsync）
verify    L1（存在/非零/大小一致）+ L2（wimlib info 可解析、索引唯一、自算 SHA256）
commit    解析重名 → os.replace 原子落位 → 复核大小与哈希 → 写伴生（.sha256 / readme）
```

三条硬约束：

* **最终文件名上永不出现未校验内容**（先 `.part` 再 rename）；
* **禁止 `shutil.move`/`copy` 当提交手段**（跨卷会退化成复制，非原子）；
* **跨卷直接拒绝**（`OUTPUT_CROSS_VOLUME`），预检失败不创建暂存区。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from uup2iso.application.pipeline.handlers import StageContext
from uup2iso.application.pipeline.orchestrator import FileOp
from uup2iso.core.errors import ErrorCode, StageExecutionError
from uup2iso.core.models.config import TaskConfig
from uup2iso.core.models.enums import OutputType, ToolKind
from uup2iso.core.models.pipeline import StageKind
from uup2iso.infrastructure.process.backend import RunSpec
from uup2iso.infrastructure.process.runner import ProcessRunner

__all__ = [
    "OutputArtifact",
    "OutputStageHandler",
    "render_name",
    "resolve_collision",
]

_STAGING_DIR = ".uup2iso-tmp"
_CHUNK = 1024 * 1024
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_NAME_LIMIT = 200
_MAX_SUFFIX = 999


@dataclass(frozen=True, slots=True)
class OutputSource:
    """输出源：一个成品文件，或一个待打包的目录（ISO 用）。"""

    path: Path
    is_dir: bool = False
    kind: str = "wim"
    """`wim` / `esd` / `iso`（由输出类型决定）。"""


@dataclass(frozen=True, slots=True)
class OutputArtifact:
    """一次输出提交的结果。"""

    source: Path
    staging: Path
    final: Path
    size: int
    sha256: str
    sidecars: tuple[Path, ...] = ()
    renamed: bool = False
    """是否因重名加了后缀。"""

    def to_dict(self) -> dict[str, object]:
        return {
            "final": str(self.final),
            "size": self.size,
            "sha256": self.sha256,
            "sidecars": [str(item) for item in self.sidecars],
            "renamed": self.renamed,
        }


class OutputStageHandler:
    """`StageHandler` 的 `Output` 实现（P2-1 仅 WIM）。

    Args:
        runner: 进程执行器（`wimlib-imagex info/verify`）。
        tools_dir: 程序目录的 `tools`（用于定位 wimlib）。
        now: 可注入时间（readme 与日志用）。
    """

    stage = StageKind.OUTPUT

    def __init__(
        self,
        *,
        runner: ProcessRunner | None = None,
        tools_dir: Path | None = None,
        now: Any = None,
    ) -> None:
        self._runner = runner
        self._tools_dir = tools_dir
        self._now = now or (lambda: datetime.now())
        self.outcome: OutputArtifact | None = None

    # -- StageHandler -----------------------------------------------------

    async def run(self, ctx: StageContext) -> None:
        executed = {substep.name for substep in ctx.plan.executed_substeps}
        config = ctx.config
        source = self._locate_source(ctx)
        output_dir = self._resolve_output_dir(config)
        name = self._compose_name(ctx, config, source.path)

        staging_root = output_dir / _STAGING_DIR / ctx.task_id
        staging = staging_root / f"{name}.part"

        if "assemble" in executed:
            self._preflight(source, output_dir, staging)

        digest: str | None = None
        if "package" in executed:
            staging_root.mkdir(parents=True, exist_ok=True)
            if source.is_dir:
                digest = await self._pack_iso(ctx, source.path, staging, name)
            else:
                digest = _copy_with_digest(source.path, staging)

        if "verify" in executed:
            if digest is None:
                digest = _sha256_of(staging)
            await self._verify(ctx, staging, source, digest, level=config.output.verify_level)

        if "commit" in executed:
            if digest is None:
                digest = _sha256_of(staging)
            await self._commit(ctx, staging, output_dir, name, digest)
        elif self.outcome is None and staging.is_file():
            self.outcome = OutputArtifact(
                source=source.path,
                staging=staging,
                final=staging,
                size=staging.stat().st_size,
                sha256=digest or "",
            )

    # -- 预演 -------------------------------------------------------------

    def describe(self, ctx: StageContext) -> tuple[str, ...]:
        config = ctx.config
        try:
            source = self._locate_source(ctx)
            output_dir = self._resolve_output_dir(config)
            name = self._compose_name(ctx, config, source.path)
        except StageExecutionError as exc:
            return (f"# 尚未就绪：{exc.message}",)

        staging = output_dir / _STAGING_DIR / ctx.task_id / f"{name}.part"
        suffix = {OutputType.ISO: ".iso", OutputType.ESD: ".esd"}.get(config.output.type, ".wim")
        final = output_dir / f"{name}{suffix}"

        if source.is_dir:
            bios, uefi = _boot_files(source.path)
            parts = [_oscdimg_path(self._tools_dir).name]
            if bios is not None:
                parts.append(f"-b{bios.name}（BIOS 引导）")
            if uefi is not None:
                parts.extend(["-u2", f"-yo{uefi.name}（UEFI 引导）"])
            parts.extend(["-m", "-o", "-h", "-l<卷标>", "", f'"{source.path}" "{staging}"'])
            commands = [
                " ".join(item for item in parts if item),
                f'7zr l "{staging}"   # L2：setup.exe + sources\\install.*',
                "# L1：偏移 32769 处应有 CD001 签名",
            ]
        else:
            commands = [
                f'copy "{source.path}" → "{staging}"（fsync）',
                f'wimlib-imagex info "{staging}"   # L2：可解析 + 索引唯一',
            ]
            if config.output.verify_level >= 3:
                commands.append(f'wimlib-imagex verify "{staging}"   # L3（已勾选）')

        commands.append(f'rename("{staging}", "{final}")  # 原子落位')
        commands.append(f'写伴生 "{final.name}.sha256" 与 readme')
        return tuple(commands)

    def file_operations(self, ctx: StageContext) -> tuple[FileOp, ...]:
        config = ctx.config
        try:
            source = self._locate_source(ctx)
            output_dir = self._resolve_output_dir(config)
            name = self._compose_name(ctx, config, source.path)
        except StageExecutionError:
            return ()
        staging = output_dir / _STAGING_DIR / ctx.task_id / f"{name}.part"
        suffix = {OutputType.ISO: ".iso", OutputType.ESD: ".esd"}.get(config.output.type, ".wim")
        final = output_dir / f"{name}{suffix}"
        return (
            FileOp("read", str(source.path), "转换产物（只读）"),
            FileOp("create", str(staging), "暂存区（校验通过才改名）"),
            FileOp("create", str(final), "最终产物"),
            FileOp("create", str(final.with_name(final.name + ".sha256")), "伴生哈希"),
            FileOp("create", str(output_dir / f"{name}.readme.txt"), "伴生说明"),
        )

    def reusable_artifacts(self, ctx: StageContext) -> tuple[str, ...]:
        return ()

    # -- assemble ---------------------------------------------------------

    def _locate_source(self, ctx: StageContext) -> OutputSource:
        """定位输出源。

        * `wim`/`esd`：转换器产出的 `install.wim` / `install.esd`；
        * `iso`：优先用转换器自己产出的 `.iso`（`AutoStart=1/2`）；
          否则若有 `ISOFOLDER` 目录，则由本处理器调 `oscdimg` 打包。
        """
        output_type = ctx.config.output.type
        convert_dir = ctx.work_dir / "convert"

        if output_type is OutputType.ISO:
            produced = _first(convert_dir, "*.iso")
            if produced is not None:
                return OutputSource(path=produced, kind="iso")
            folder = convert_dir / "ISOFOLDER"
            if folder.is_dir():
                return OutputSource(path=folder, is_dir=True, kind="iso")
            raise StageExecutionError(
                "未找到 ISO 或 ISOFOLDER：请先把 convert.mode 设为 full（AutoStart=1）完成转换，"
                "或在支持的模式下产出 ISO 目录",
                code=ErrorCode.INPUT_UNREADABLE,
            )

        if output_type is OutputType.ESD:
            found = _first(convert_dir, "install.esd")
            if found is None:
                raise StageExecutionError(
                    "未找到 install.esd：请先跑 Convert", code=ErrorCode.INPUT_UNREADABLE
                )
            return OutputSource(path=found, kind="esd")

        found = _first(convert_dir, "install.wim")
        if found is None:
            raise StageExecutionError(
                "未找到 install.wim：请先跑 Convert（或指定入口 B 并完成转换）",
                code=ErrorCode.INPUT_UNREADABLE,
            )
        return OutputSource(path=found, kind="wim")

    def _resolve_output_dir(self, config: TaskConfig) -> Path:
        """输出目录：空 → 程序目录下的 output；相对 → 相对程序目录；绝对 → 原样。

        目录不存在会**自动创建**（用户不必先手工建目录）。
        """
        from uup2iso.application.startup import default_output_dir, program_dir

        raw = (config.output.dir or "").strip()
        if not raw:
            path = default_output_dir()
        else:
            candidate = Path(raw)
            path = candidate if candidate.is_absolute() else program_dir() / candidate

        if not path.is_dir():
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise StageExecutionError(
                    f"输出目录不可写：{path}（{exc}）。请改到可写位置或指定 output.dir",
                    code=ErrorCode.PERMISSION_DENIED,
                ) from exc
        return path

    def _compose_name(self, ctx: StageContext, config: TaskConfig, source: Path) -> str:
        """按模板渲染文件名（无扩展名）。

        取值优先级：版本判定（Prepare）→ 配置 → **产物文件名解析** → `unknown`。
        产物名里的 arch/edition/lang 来自镜像本身（转换器从 install.wim 元数据拼的），
        比配置里手写的更可信——这也是 `unknown` 的主要来源。
        """
        template = config.output.name_template or "Win11_{build}_{edition}_{arch}_{lang}"
        version = ctx.extras.get("version")
        image = None
        if source.suffix.lower() == ".iso":
            from uup2iso.infrastructure.input.image_name import parse_converter_iso_name

            image = parse_converter_iso_name(source.name)
            if image is not None:
                ctx.extras["image"] = image
        values = {
            "edition": _pick(
                getattr(version, "edition", ""),
                (config.convert.editions[0] if config.convert and config.convert.editions else ""),
                (image.edition if image else ""),
                "unknown",
            ),
            # `{build}` 语义 = **产物装出来的版本**（镜像基线），不是数据源版本：
            # 未集成 LCU 时镜像仍是 26100.1，直接写 26300 会误导。
            "build": _pick(
                (image.build_revision if image else ""),
                getattr(version, "build", ""),
                (config.source.build if config.source else ""),
                "unknown",
            ),
            "sourceBuild": _pick(
                getattr(version, "build", ""),
                (config.source.build if config.source else ""),
                "unknown",
            ),
            "arch": _pick(
                getattr(config.source, "arch", "") if config.source else "",
                (image.arch if image else ""),
                "unknown",
            ),
            "lang": _pick(
                getattr(version, "language", ""),
                (config.source.language if config.source else ""),
                (image.language if image else ""),
                "unknown",
            ),
        }
        return render_name(template, values)

    def _preflight(self, source: OutputSource, output_dir: Path, staging: Path) -> None:
        """空间与同卷预检；**失败时不创建任何目录**。"""
        if staging.drive.lower() != output_dir.drive.lower():
            raise StageExecutionError(
                f"暂存区与输出目录不在同一磁盘：{staging.drive} ≠ {output_dir.drive}",
                code=ErrorCode.OUTPUT_CROSS_VOLUME,
            )
        size = _source_size(source.path)
        if size <= 0:
            raise StageExecutionError(f"产物为空：{source}", code=ErrorCode.OUTPUT_VERIFY_FAILED)
        needed = int(size * 1.05) + 16 * 1024 * 1024
        free = shutil.disk_usage(output_dir).free
        if free < needed:
            raise StageExecutionError(
                f"输出卷空间不足：需要 {needed / 1e9:.2f} GB / 可用 {free / 1e9:.2f} GB",
                code=ErrorCode.DISK_FULL,
            )

    # -- verify -----------------------------------------------------------

    async def _verify(
        self, ctx: StageContext, staging: Path, source: OutputSource, digest: str, *, level: int
    ) -> None:
        if not staging.is_file():
            raise StageExecutionError(
                f"暂存产物不存在：{staging}", code=ErrorCode.OUTPUT_VERIFY_FAILED
            )
        size = staging.stat().st_size
        if size <= 0:
            raise StageExecutionError("L1 失败：产物为空", code=ErrorCode.OUTPUT_VERIFY_FAILED)

        if source.kind == "iso":
            if not is_iso_file(staging):
                raise StageExecutionError(
                    "L1 失败：ISO 签名缺失（偏移 32769 处无 CD001）",
                    code=ErrorCode.OUTPUT_VERIFY_FAILED,
                )
            if level >= 2:
                await self._verify_iso_layout(ctx, staging, source.path)
            return

        if source.path.is_file() and size != source.path.stat().st_size:
            raise StageExecutionError(
                f"L1 失败：暂存大小 {size} ≠ 源 {source.path.stat().st_size}",
                code=ErrorCode.OUTPUT_VERIFY_FAILED,
            )

        if level >= 2:
            info_rc, info_text = await self._wimlib(ctx, "info", staging)
            if info_rc != 0:
                raise StageExecutionError(
                    f"L2 失败：wimlib info 无法解析（exit={info_rc}）",
                    code=ErrorCode.OUTPUT_VERIFY_FAILED,
                )
            images = _count_images(info_text)
            if images < 1:
                raise StageExecutionError(
                    "L2 失败：映像数为 0", code=ErrorCode.OUTPUT_VERIFY_FAILED
                )
            self._log(ctx, "output_validated", verify_level=2, images=images, sha256=digest[:12])

        if level >= 3:
            verify_rc, _ = await self._wimlib(ctx, "verify", staging)
            if verify_rc != 0:
                raise StageExecutionError(
                    f"L3 失败：wimlib verify 报错（exit={verify_rc}）",
                    code=ErrorCode.OUTPUT_VERIFY_FAILED,
                )
            self._log(ctx, "output_validated", verify_level=3, sha256=digest[:12])

    async def _pack_iso(self, ctx: StageContext, folder: Path, staging: Path, name: str) -> str:
        """用 `oscdimg` 把 ISOFOLDER 打成 ISO（写到暂存名），返回 SHA256。"""
        exe = _oscdimg_path(self._tools_dir)
        if not exe.is_file():
            raise StageExecutionError(
                f"未找到 oscdimg：{exe}（需要 ADK 的 oscdimg.exe）", code=ErrorCode.TOOL_MISSING
            )
        bios, uefi = _boot_files(folder)
        if bios is None and uefi is None:
            raise StageExecutionError(
                "ISO 骨架缺少引导文件（boot\\etfsboot.com 或 efi\\microsoft\\boot\\efisys.bin）",
                code=ErrorCode.INPUT_DAMAGED,
            )
        _require_iso_skeleton(folder)
        configured = getattr(ctx.config.convert, "volume_label", "") if ctx.config.convert else ""
        label = (configured or f"UUP2ISO_{name}")[:32]
        args = build_oscdimg_args(exe, folder, staging, label=label, bios_boot=bios, uefi_boot=uefi)
        rc, _ = await self._run_collect(ctx, args, stage="Output")
        if rc != 0:
            raise StageExecutionError(
                f"oscdimg 失败（exit={rc}），详见日志", code=ErrorCode.TOOL_FAILED
            )
        self._log(ctx, "iso_packed", label=label, size=staging.stat().st_size)
        return _sha256_of(staging)

    async def _verify_iso_layout(self, ctx: StageContext, iso: Path, source: Path) -> None:
        r"""L2：用 7-Zip 列 ISO，确认 `setup.exe` 与 `sources\install.*` 都在。"""
        seven = _seven_zip(self._tools_dir)
        if seven is None:
            # `7zr.exe` 是精简版，不支持 ISO/UDF；没有完整版 7z 时降级为只做 L1
            self._log(ctx, "iso_layout_skipped", reason="no_full_7z")
            return
        rc, text = await self._run_collect(ctx, [str(seven), "l", str(iso)], stage="Output")
        if rc != 0:
            raise StageExecutionError(
                f"L2 失败：7z 无法列出 ISO（exit={rc}）", code=ErrorCode.OUTPUT_VERIFY_FAILED
            )
        lower = text.lower().replace("/", "\\")
        missing = [
            name for name in ("setup.exe", "install.wim", "install.esd") if name not in lower
        ]
        if "setup.exe" in missing or len(missing) == 3:
            raise StageExecutionError(
                "L2 失败：ISO 内缺少 setup.exe 或 sources\\install.*",
                code=ErrorCode.OUTPUT_VERIFY_FAILED,
            )
        self._log(ctx, "iso_layout_ok", source=str(source))

    async def _run_collect(
        self, ctx: StageContext, args: list[str], *, stage: str
    ) -> tuple[int, str]:
        """跑一个外部命令并收集全部输出行（不依赖 ProcessResult.last_lines）。"""
        if self._runner is None:
            raise StageExecutionError(
                "Output 缺少 ProcessRunner（组合根未接线）", code=ErrorCode.INTERNAL_ERROR
            )
        tool = ToolKind.OSCDIMG if "oscdimg" in args[0].lower() else ToolKind.SEVEN_ZIP
        lines: list[str] = []

        def _collect(event: object) -> None:
            text = getattr(event, "text", "")
            if isinstance(text, str) and text:
                lines.append(text)

        spec = RunSpec(tool=tool, args=tuple(args), cwd=ctx.work_dir, stage=stage)
        result = await self._runner.run(spec, on_event=_collect, check=False)
        return int(getattr(result, "returncode", 0)), "\n".join(lines)

    async def _wimlib(self, ctx: StageContext, verb: str, target: Path) -> tuple[int, str]:
        """跑一次 wimlib，返回 ``(返回码, 合并后的输出文本)``。

        `ProcessResult` 只保留最后若干行，取不到完整 stdout；
        所以用 `on_event` 把**所有行**收集起来再解析（`Image Count:` 在输出开头）。
        """
        if self._runner is None:
            raise StageExecutionError(
                "Output 缺少 ProcessRunner（组合根未接线）", code=ErrorCode.INTERNAL_ERROR
            )
        exe = _wimlib_path(self._tools_dir)
        spec = RunSpec(
            tool=ToolKind.WIMLIB,
            args=(str(exe), verb, str(target)),
            cwd=ctx.work_dir,
            stage="Output",
        )
        lines: list[str] = []

        def _collect(event: object) -> None:
            text = getattr(event, "text", "")
            if isinstance(text, str) and text:
                lines.append(text)

        result = await self._runner.run(spec, on_event=_collect, check=False)
        return int(getattr(result, "returncode", 0)), "\n".join(lines)

    # -- commit -----------------------------------------------------------

    async def _commit(
        self, ctx: StageContext, staging: Path, output_dir: Path, name: str, digest: str
    ) -> None:
        config = ctx.config
        suffix = {
            OutputType.ISO: ".iso",
            OutputType.ESD: ".esd",
        }.get(config.output.type, ".wim")
        final, renamed = resolve_collision(
            output_dir, name + suffix, strategy=config.output.collision
        )
        if renamed:
            self._log(
                ctx, "output_name_collision", strategy=config.output.collision, final=final.name
            )

        staging.replace(final)
        size = final.stat().st_size
        if _sha256_of(final) != digest:
            raise StageExecutionError(
                "提交后复核失败：SHA256 不一致（磁盘可能有问题）",
                code=ErrorCode.OUTPUT_VERIFY_FAILED,
            )

        sidecars = self._write_sidecars(ctx, final, digest, size)
        self._log(
            ctx,
            "output_committed",
            file=final.name,
            size=size,
            sha256=digest[:12],
            sidecars=len(sidecars),
        )
        staging_root = staging.parent
        staging.unlink(missing_ok=True)
        if staging_root.is_dir() and not any(staging_root.iterdir()):
            staging_root.rmdir()

        self.outcome = OutputArtifact(
            source=staging,
            staging=staging,
            final=final,
            size=size,
            sha256=digest,
            sidecars=sidecars,
            renamed=renamed,
        )
        ctx.extras["output"] = self.outcome

    def _write_sidecars(
        self, ctx: StageContext, final: Path, digest: str, size: int
    ) -> tuple[Path, ...]:
        """伴生文件：先写暂存名再 rename；失败只告警，不阻断产物。"""
        written: list[Path] = []
        targets = [
            (final.with_name(final.name + ".sha256"), f"{digest} *{final.name}\n"),
            (
                final.with_name(final.name.replace(final.suffix, "") + ".readme.txt"),
                self._readme(ctx, final, digest, size),
            ),
        ]
        for target, content in targets:
            tmp = target.with_name(target.name + ".tmp")
            try:
                tmp.write_text(content, encoding="utf-8", newline="\r\n")
                tmp.replace(target)
                written.append(target)
            except OSError:
                self._log(ctx, "sidecar_failed", file=target.name)
                tmp.unlink(missing_ok=True)
        return tuple(written)

    def _readme(self, ctx: StageContext, final: Path, digest: str, size: int) -> str:
        config = ctx.config
        version = ctx.extras.get("version")
        lines = [
            f"产物：{final.name}",
            f"大小：{size} 字节（{size / 1e9:.2f} GB）",
            f"SHA256：{digest}",
            f"产物版本：{_image_version(ctx) or getattr(version, 'build', '') or '-'}",
            f"数据源构建：{getattr(version, 'build', '') or '-'}",
            f"镜像基线：{_image_baseline(ctx) or '-'}",
            f"版本族：{getattr(version, 'family', '') or '-'}",
            f"语言：{getattr(version, 'language', '') or '-'}",
            f"edition：{getattr(version, 'edition', '') or '-'}",
            f"输出类型：{config.output.type.value}",
            f"校验级别：L{config.output.verify_level}",
            f"任务：{ctx.task_id}",
            f"生成时间：{self._now().isoformat(timespec='seconds')}",
        ]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _log(ctx: StageContext, event: str, **fields: object) -> None:
        sink: Any = ctx.log
        if sink is not None:
            sink.info("app.output", event, stage="Output", event=event, **fields)


# ---------------------------------------------------------------------------
# 辅助函数（可单测）
# ---------------------------------------------------------------------------


def render_name(template: str, values: dict[str, str]) -> str:
    """渲染文件名模板：替换非法字符、合并下划线、超长截断。"""
    try:
        rendered = template.format(**values)
    except KeyError:
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", value)
    cleaned = _ILLEGAL.sub("_", rendered).strip(" .")
    cleaned = re.sub(r"_{2,}", "_", cleaned) or "output"
    if len(cleaned) > _NAME_LIMIT:
        cleaned = cleaned[:_NAME_LIMIT]
    return cleaned


def resolve_collision(
    directory: Path, filename: str, *, strategy: str = "suffix"
) -> tuple[Path, bool]:
    """解析重名（默认加 `_N` 后缀；`overwrite` 直接覆盖）。"""
    target = directory / filename
    if not target.exists() or strategy == "overwrite":
        return target, False

    stem = target.stem
    suffix = target.suffix
    for index in range(1, _MAX_SUFFIX + 1):
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate, True
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return directory / f"{stem}_{stamp}{suffix}", True


def _copy_with_digest(source: Path, target: Path) -> str:
    """分块复制并计算 SHA256，结束前 `flush + fsync`（规范 §3.1 ①）。"""
    digest = hashlib.sha256()
    with source.open("rb") as reader, target.open("wb") as writer:
        while True:
            chunk = reader.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest()


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_images(stdout: str | None) -> int:
    match = re.search(r"Image Count:\s*(\d+)", stdout or "")
    return int(match.group(1)) if match else 0


def _image_version(ctx: StageContext) -> str:
    """产物版本（`26100.1`）：镜像基线的主版本.修订。"""
    image = ctx.extras.get("image")
    value = getattr(image, "build_revision", "")
    return value if isinstance(value, str) else ""


def _image_baseline(ctx: StageContext) -> str:
    """从 `ctx.extras["image"]` 取镜像基线串（`26100.1.240331-1435`）。"""
    image = ctx.extras.get("image")
    baseline = getattr(image, "baseline", "")
    return baseline if isinstance(baseline, str) else ""


def _source_size(path: Path) -> int:
    """源体积：文件取自身，目录取递归求和（ISO 骨架用）。"""
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return 0


ISO_SIGNATURE_OFFSET = 32769
ISO_SIGNATURE = b"CD001"


def is_iso_file(path: Path) -> bool:
    """按文件头判断 ISO（偏移 32769 处 `CD001`，与输入嗅探同一判据）。"""
    try:
        with path.open("rb") as handle:
            handle.seek(ISO_SIGNATURE_OFFSET)
            return handle.read(len(ISO_SIGNATURE)) == ISO_SIGNATURE
    except OSError:
        return False


def _require_iso_skeleton(folder: Path) -> None:
    """打包前确认骨架里有 `setup.exe` 与 `sources\\install.*`（缺则 INPUT_DAMAGED）。"""
    has_setup = (folder / "setup.exe").is_file()
    sources = folder / "sources"
    has_install = sources.is_dir() and any(
        (sources / f"install{suffix}").is_file() for suffix in (".wim", ".esd", ".swm")
    )
    if has_setup and has_install:
        return
    missing = "setup.exe" if not has_setup else "sources\\install.*"
    raise StageExecutionError(f"ISO 骨架缺少 {missing}", code=ErrorCode.INPUT_DAMAGED)


def _boot_files(folder: Path) -> tuple[Path | None, Path | None]:
    """返回 `(BIOS 引导, UEFI 引导)`；缺失的为 None。"""
    bios = folder / "boot" / "etfsboot.com"
    uefi = folder / "efi" / "microsoft" / "boot" / "efisys.bin"
    return (bios if bios.is_file() else None, uefi if uefi.is_file() else None)


def build_oscdimg_args(
    exe: Path,
    folder: Path,
    target: Path,
    *,
    label: str,
    bios_boot: Path | None,
    uefi_boot: Path | None,
) -> list[str]:
    """构造 oscdimg 命令行（BIOS/UEFI 双启动；`-m -o -h` 为标准组合）。"""
    # 全部转绝对路径：ProcessRunner 的 cwd 是任务工作目录，相对路径会解析错
    args = [str(exe)]
    if bios_boot is not None:
        args.append(f"-b{Path(bios_boot).resolve()}")
    if uefi_boot is not None:
        args.extend(["-u2", f"-yo{Path(uefi_boot).resolve()}"])
    args.extend(
        ["-m", "-o", "-h", f"-l{label}", str(Path(folder).resolve()), str(Path(target).resolve())]
    )
    return args


def _oscdimg_path(tools_dir: Path | None) -> Path:
    from uup2iso.application.startup import program_dir

    base = tools_dir or program_dir() / "tools"
    return base / "oscdimg.exe"


def _seven_zip(tools_dir: Path | None) -> Path | None:
    """完整版 7-Zip（`7z.exe` / `7za.exe`）；`7zr.exe` 不支持 ISO/UDF，不用于校验。"""
    from uup2iso.application.startup import program_dir

    base = tools_dir or program_dir() / "tools"
    for name in ("7z.exe", "7za.exe"):
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def _wimlib_path(tools_dir: Path | None) -> Path:
    from uup2iso.application.startup import program_dir

    base = tools_dir or program_dir() / "tools"
    candidate = base / "wimlib-imagex" / "wimlib-imagex.exe"
    return candidate if candidate.is_file() else base / "wimlib-imagex.exe"


def _first(root: Path, pattern: str) -> Path | None:
    if not root.is_dir():
        return None
    for item in sorted(root.rglob(pattern)):
        if item.is_file():
            return item
    return None


def _pick(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""
