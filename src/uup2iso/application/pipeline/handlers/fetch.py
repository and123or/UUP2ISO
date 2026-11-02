"""`Fetch` 阶段处理器（WP2-T4 的前置胶水）：清单 → 下载 → 校验。

三个子步骤严格按编排 §3 的顺序执行，且**每步都能单独跳过**（续跑场景）：

```
manifest  （拉清单 + 五项自校验 + 落盘 work\\<task>\\manifest.json）
download  （aria2c 按清单平铺下载到 work\\<task>\\download\\）
verify    （重新校验「存在 → 大小 → SHA1」，不信任 aria2c 的退出码）
```

两条实测约束写进实现里：

* **URL 寿命约 12 分钟**：磁盘上已有清单时，若下载报 `DOWNLOAD_CORRUPT`，先怀疑清单过期 →
  刷新一次再重试（只重试一次，避免无限刷新）；
* **不信任 aria2c 退出码**：它可能「部分成功」，所以 `verify` 子步骤独立重算哈希。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uup2iso.application.fetch.manifest_service import (
    ManifestRequest,
    build_manifest,
    load_manifest,
    write_manifest,
)
from uup2iso.application.pipeline.handlers import StageContext
from uup2iso.application.pipeline.orchestrator import FileOp
from uup2iso.core.errors import ErrorCode, StageExecutionError
from uup2iso.core.models.config import TaskConfig
from uup2iso.core.models.enums import Architecture, EntryPoint
from uup2iso.core.models.manifest import Manifest
from uup2iso.core.models.pipeline import StageKind
from uup2iso.infrastructure.download import Aria2Downloader, verify_file
from uup2iso.infrastructure.source.base import UupSource

__all__ = ["FetchOutcome", "FetchStageHandler"]

_MANIFEST_NAME = "manifest.json"
_DOWNLOAD_DIR = "download"
_BATCH_SIZE = 8
"""每批下载的文件数。

实测：一次把 97 个签名 URL 交给 aria2c，CDN 会集体回 403；分小批 + 每批用新清单就正常。
这也正好契合「URL 只有 ~12 分钟寿命」——分批刷新比一次性赌 TTL 稳。
"""


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """`Fetch` 阶段的产物摘要。"""

    manifest: Manifest
    manifest_path: Path
    verified_files: int = 0
    total_bytes: int = 0
    retry_rounds: int = 0
    refreshed: bool = False

    @property
    def file_count(self) -> int:
        return len(self.manifest.files)


class FetchStageHandler:
    """`StageHandler` 的 `Fetch` 实现。

    Args:
        source: 数据源（通常是 `UupDumpSource`）。
        downloader_factory: ``work_dir -> Aria2Downloader``；``None`` 时只能跑 `manifest` 子步骤
            （`--dry-run` 与单测场景）。
    """

    stage = StageKind.FETCH

    def __init__(
        self,
        *,
        source: UupSource | None = None,
        downloader_factory: Callable[[Path], Aria2Downloader] | None = None,
        names: tuple[str, ...] | None = None,
        retry_wait: float = 5.0,
    ) -> None:
        self._source = source
        self._factory = downloader_factory
        self._names = names
        self._retry_wait = retry_wait
        self.outcome: FetchOutcome | None = None

    async def run(self, ctx: StageContext) -> None:
        executed = {substep.name for substep in ctx.plan.executed_substeps}
        work = ctx.work_dir
        manifest_path = work / _MANIFEST_NAME

        manifest: Manifest | None = None
        if "manifest" in executed or not manifest_path.is_file():
            manifest = self._refresh_manifest(ctx, manifest_path)
        else:
            manifest = load_manifest(manifest_path)
            self._log(ctx, "manifest_loaded", file=str(manifest_path), files=len(manifest.files))

        refreshed = False
        if "download" in executed:
            reports = []
            names = list(self._names) if self._names else [item.name for item in manifest.files]
            for index, chunk in enumerate(_chunks(names, _BATCH_SIZE), start=1):
                report, manifest, did_refresh = await self._download_batch(
                    ctx, manifest, chunk, index=index
                )
                refreshed = refreshed or did_refresh
                reports.append(report)
                self._log(ctx, "download_batch", batch=index, files=len(chunk), total=len(names))
            verified_total = sum(len(getattr(r, "verified_files", ()) or ()) for r in reports)
            retry_total = sum(int(getattr(r, "retry_rounds", 0) or 0) for r in reports)
            ctx.extras["download_report"] = reports[-1] if reports else None
            ctx.extras["retry_rounds"] = retry_total
            ctx.extras["verified_files"] = verified_total

        verified = self._verify_manifest(ctx, manifest) if "verify" in executed else 0
        ctx.extras["manifest"] = manifest
        ctx.extras["manifest_path"] = str(manifest_path)

        scope = set(self._names) if self._names else None
        total = sum(item.size for item in manifest.files if scope is None or item.name in scope)
        self.outcome = FetchOutcome(
            manifest=manifest,
            manifest_path=manifest_path,
            verified_files=verified or _as_int(ctx.extras.get("verified_files")),
            total_bytes=total,
            retry_rounds=_as_int(ctx.extras.get("retry_rounds")),
            refreshed=refreshed,
        )

    # -- dry-run ----------------------------------------------------------

    def describe(self, ctx: StageContext) -> tuple[str, ...]:
        """`--dry-run`：列出将执行的外部命令（不执行）。"""
        selection = _selection(ctx.config)
        work = ctx.work_dir
        commands = [
            f"GET {selection.base_url.rstrip('/')}/listid.php?search={selection.build}",
            f"GET {selection.base_url.rstrip('/')}/get.php  # lang={selection.language} "
            f"edition={selection.edition}",
        ]
        if any(sub.name == "download" for sub in ctx.plan.executed_substeps):
            commands.append(
                f"aria2c --input-file={(work / _DOWNLOAD_DIR / 'aria2-input.txt').resolve()} "
                "--continue=true --max-concurrent-downloads=1 --max-tries=3 --retry-wait=2"
            )
        return tuple(commands)

    def file_operations(self, ctx: StageContext) -> tuple[FileOp, ...]:
        work = ctx.work_dir
        return (
            FileOp("create", str(work / _MANIFEST_NAME), "清单（五项自校验后落盘）"),
            FileOp("create", str(work / _DOWNLOAD_DIR), "UUP 文件平铺目录"),
            FileOp("create", str(work / _DOWNLOAD_DIR / "download.state.json"), "下载状态"),
        )

    def reusable_artifacts(self, ctx: StageContext) -> tuple[str, ...]:
        work = ctx.work_dir
        items = [_MANIFEST_NAME] if (work / _MANIFEST_NAME).is_file() else []
        return tuple(items)

    # -- 内部 -------------------------------------------------------------

    def _refresh_manifest(self, ctx: StageContext, path: Path) -> Manifest:
        source = self._source
        if source is None:
            raise StageExecutionError(
                "Fetch 缺少数据源实现（UupSource）", code=ErrorCode.INTERNAL_ERROR
            )
        selection = _selection(ctx.config)
        builds = source.list_builds(search=selection.build)
        matched = [
            item
            for item in builds
            if item.build == selection.build and item.arch is selection.arch_hint
        ]
        build = (
            matched or [item for item in builds if item.build == selection.build] or list(builds)
        )[0]
        payload = source.get_files(build.id, language=selection.language, edition=selection.edition)
        manifest, warnings = build_manifest(
            payload,
            ManifestRequest(
                build=payload.build or selection.build,
                language=selection.language,
                edition=selection.edition,
                base_url=selection.base_url,
                fetched_at=payload.expires_at or _now(),  # 仅作记录
                revision=build.revision,
            ),
            debug_dir=ctx.work_dir / "debug",
        )
        write_manifest(path, manifest)
        for warning in warnings:
            self._log(ctx, "manifest_warning", detail=warning)
        self._log(
            ctx,
            "manifest_ready",
            files=len(manifest.files),
            total_bytes=manifest.total_bytes,
            expires=(payload.expires_at.isoformat() if payload.expires_at else "-"),
        )
        return manifest

    async def _download_batch(
        self,
        ctx: StageContext,
        manifest: Manifest,
        chunk: list[str],
        *,
        index: int,
    ) -> tuple[Any, Manifest, bool]:
        """下载一批；失败按「退避重试 → 刷新清单重试」两级兜底。

        实测：清单刚生成时 CDN 会短暂回 403（URL 生效延迟/瞬时限流），
        直接刷新清单没用（URL 一样），先等几秒重试一次更有效。
        """
        attempts = (
            ("initial", 0.0, False),
            ("backoff", self._retry_wait, False),
            ("refresh", min(self._retry_wait, 2.0), True),
        )
        last: StageExecutionError | None = None
        active = manifest
        refreshed = False
        for label, wait, need_refresh in attempts:
            if wait:
                await asyncio.sleep(wait)
            current = active
            if need_refresh:
                self._log(ctx, "manifest_refresh_on_failure", batch=index, files=len(chunk))
                current = self._refresh_manifest(ctx, ctx.work_dir / _MANIFEST_NAME)
                active = current
                refreshed = True
            try:
                return await self._download(ctx, current, names=chunk), active, refreshed
            except StageExecutionError as exc:
                if exc.code is not ErrorCode.DOWNLOAD_CORRUPT:
                    raise
                last = exc
                self._log(ctx, "batch_retry", batch=index, attempt=label, files=len(chunk))
        raise (
            last
            if last is not None
            else StageExecutionError("批次下载失败", code=ErrorCode.DOWNLOAD_CORRUPT)
        )

    async def _download(
        self, ctx: StageContext, manifest: Manifest, *, names: list[str] | None = None
    ) -> Any:
        if self._factory is None:
            raise StageExecutionError(
                "Fetch 缺少下载器（Aria2Downloader）", code=ErrorCode.INTERNAL_ERROR
            )
        downloader = self._factory(ctx.work_dir)
        binder = getattr(downloader, "bind", None)
        if callable(binder):
            binder(log=ctx.log, progress=ctx.progress)
        return await downloader.download(
            manifest,
            dest_dir=ctx.work_dir / _DOWNLOAD_DIR,
            names=tuple(names) if names is not None else self._names,
        )

    def _verify_manifest(self, ctx: StageContext, manifest: Manifest) -> int:
        box = ctx.work_dir / _DOWNLOAD_DIR
        verified = 0
        failures: list[str] = []
        scope = set(self._names) if self._names else None
        for item in manifest.files:
            if scope is not None and item.name not in scope:
                continue
            outcome = verify_file(box / item.name, item.size, item.sha1)
            if outcome.is_ok:
                verified += 1
            else:
                failures.append(f"{item.name}（{outcome.error}）")
        self._log(ctx, "manifest_verified", verified=verified, failed=len(failures))
        if failures:
            raise StageExecutionError(
                "下载校验未通过：" + "；".join(failures[:5]),
                code=ErrorCode.DOWNLOAD_CORRUPT,
            )
        return verified

    @staticmethod
    def _log(ctx: StageContext, event: str, **fields: object) -> None:
        sink: Any = ctx.log
        if sink is not None:
            sink.info("app.fetch", event, stage="Fetch", event=event, **fields)


# ---------------------------------------------------------------------------
# 任务选择
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Selection:
    build: str
    language: str
    edition: str
    base_url: str
    arch_hint: Architecture


def _selection(config: TaskConfig) -> _Selection:
    """从 `TaskConfig` 取构建选择（入口 A 才用得到 Fetch）。"""
    mode = getattr(config.input, "mode", None)
    if mode is not EntryPoint.ONLINE:
        raise StageExecutionError(
            "只有入口 A（在线构建）需要 Fetch", code=ErrorCode.CONFIG_SEMANTIC_INVALID
        )
    source = getattr(config, "source", None)
    if source is None:
        raise StageExecutionError("入口 A 缺少 source 配置", code=ErrorCode.CONFIG_SEMANTIC_INVALID)
    return _Selection(
        build=str(_pick(source, "build", "")),
        language=str(_pick(source, "language", "zh-cn")).lower(),
        edition=str(_pick(source, "edition", "professional")).lower(),
        base_url=str(_pick(source, "base_url", "https://uupdump.net/")),
        arch_hint=Architecture.X64,
    )


def _pick(source: object, name: str, default: object = None) -> object:
    """同时兼容 pydantic 模型与 dict（测试里两种都会出现）。"""
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _as_int(value: object) -> int:
    """把 extras 里的计数安全转成 int（可能是 None/字符串）。"""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return 0


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)
