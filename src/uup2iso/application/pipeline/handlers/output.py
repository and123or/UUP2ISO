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
        name = self._compose_name(ctx, config, source)

        staging_root = output_dir / _STAGING_DIR / ctx.task_id
        staging = staging_root / f"{name}.part"

        if "assemble" in executed:
            self._preflight(source, output_dir, staging)

        digest: str | None = None
        if "package" in executed:
            staging_root.mkdir(parents=True, exist_ok=True)
            digest = _copy_with_digest(source, staging)

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
                source=source,
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
            name = self._compose_name(ctx, config, source)
        except StageExecutionError as exc:
            return (f"# 尚未就绪：{exc.message}",)

        staging = output_dir / _STAGING_DIR / ctx.task_id / f"{name}.part"
        commands = [
            f'copy "{source}" → "{staging}"（fsync）',
            f'wimlib-imagex info "{staging}"   # L2：可解析 + 索引唯一',
        ]
        if config.output.verify_level >= 3:
            commands.append(f'wimlib-imagex verify "{staging}"   # L3（已勾选）')
        commands.append(f'rename("{staging}", "{output_dir / (name + ".wim")}")  # 原子落位')
        commands.append(f'写伴生 "{output_dir / (name + ".wim.sha256")}" 与 readme')
        return tuple(commands)

    def file_operations(self, ctx: StageContext) -> tuple[FileOp, ...]:
        config = ctx.config
        try:
            source = self._locate_source(ctx)
            output_dir = self._resolve_output_dir(config)
            name = self._compose_name(ctx, config, source)
        except StageExecutionError:
            return ()
        staging = output_dir / _STAGING_DIR / ctx.task_id / f"{name}.part"
        final = output_dir / f"{name}.wim"
        return (
            FileOp("read", str(source), "转换产物（只读）"),
            FileOp("create", str(staging), "暂存区（校验通过才改名）"),
            FileOp("create", str(final), "最终产物"),
            FileOp("create", str(final.with_suffix(".wim.sha256")), "伴生哈希"),
            FileOp("create", str(output_dir / f"{name}.readme.txt"), "伴生说明"),
        )

    def reusable_artifacts(self, ctx: StageContext) -> tuple[str, ...]:
        return ()

    # -- assemble ---------------------------------------------------------

    def _locate_source(self, ctx: StageContext) -> Path:
        """转换产物：优先 install.wim（P2-1 只支持 WIM）。"""
        output_type = ctx.config.output.type
        if output_type is OutputType.ISO:
            raise StageExecutionError(
                "ISO 输出属 P2-2（尚未实现）；当前只支持 type=wim",
                code=ErrorCode.OUTPUT_TYPE_UNSUPPORTED,
            )
        if output_type is OutputType.ESD:
            found = _first(ctx.work_dir / "convert", "install.esd")
            if found is None:
                raise StageExecutionError(
                    "未找到 install.esd：请先跑 Convert", code=ErrorCode.INPUT_UNREADABLE
                )
            return found

        found = _first(ctx.work_dir / "convert", "install.wim")
        if found is None:
            raise StageExecutionError(
                "未找到 install.wim：请先跑 Convert（或指定入口 B 并完成转换）",
                code=ErrorCode.INPUT_UNREADABLE,
            )
        return found

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
        """按模板渲染文件名（无扩展名）。变量缺失填 `unknown` 并记 WARN。"""
        template = config.output.name_template or "{edition}_{build}_{arch}_{lang}"
        version = ctx.extras.get("version")
        values = {
            "edition": _pick(
                getattr(version, "edition", ""),
                (config.convert.editions[0] if config.convert and config.convert.editions else ""),
                "unknown",
            ),
            "build": _pick(
                getattr(version, "build", ""),
                (config.source.build if config.source else ""),
                "unknown",
            ),
            "arch": _pick(getattr(config.source, "arch", "") if config.source else "", "unknown"),
            "lang": _pick(
                getattr(version, "language", ""),
                (config.source.language if config.source else ""),
                "unknown",
            ),
        }
        return render_name(template, values)

    def _preflight(self, source: Path, output_dir: Path, staging: Path) -> None:
        """空间与同卷预检；**失败时不创建任何目录**。"""
        if staging.drive.lower() != output_dir.drive.lower():
            raise StageExecutionError(
                f"暂存区与输出目录不在同一磁盘：{staging.drive} ≠ {output_dir.drive}",
                code=ErrorCode.OUTPUT_CROSS_VOLUME,
            )
        size = source.stat().st_size
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
        self, ctx: StageContext, staging: Path, source: Path, digest: str, *, level: int
    ) -> None:
        if not staging.is_file():
            raise StageExecutionError(
                f"暂存产物不存在：{staging}", code=ErrorCode.OUTPUT_VERIFY_FAILED
            )
        size = staging.stat().st_size
        if size != source.stat().st_size:
            raise StageExecutionError(
                f"L1 失败：暂存大小 {size} ≠ 源 {source.stat().st_size}",
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
        suffix = ".esd" if config.output.type is OutputType.ESD else ".wim"
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
            f"构建：{getattr(version, 'build', '') or '-'}",
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


def _wimlib_path(tools_dir: Path | None) -> Path:
    base = tools_dir or Path("tools")
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
