"""`Fetch` 阶段处理器门禁（编排 §3、下载 §4/§5）。

用假数据源 + 假下载器覆盖：子步骤顺序、清单落盘、下载失败后刷新清单重试、
独立校验子步骤、dry-run 输出与入口限制。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from uup2iso.application.fetch import ManifestRequest, build_manifest
from uup2iso.application.pipeline import StageContext, plan_pipeline
from uup2iso.application.pipeline.handlers import FetchStageHandler
from uup2iso.core.errors import ErrorCode, StageExecutionError
from uup2iso.core.models import default_task_config
from uup2iso.core.models.enums import EntryPoint
from uup2iso.core.models.manifest import UupFile
from uup2iso.core.models.pipeline import StageKind
from uup2iso.core.models.source import SourcePayload

CONTENT = b"PAYLOAD" * 50
SHA1 = hashlib.sha1(CONTENT).hexdigest()


def make_config(*, mode: EntryPoint = EntryPoint.ONLINE) -> object:
    config = default_task_config(mode=mode)
    config.input.start_stage = "Convert"
    config.source = {  # type: ignore[assignment]
        "baseUrl": "https://uupdump.net/",
        "build": "26100.9725",
        "revision": "9725",
        "language": "zh-cn",
        "edition": "professional",
        "channel": "Retail",
    }
    return config


def make_payload() -> SourcePayload:
    return SourcePayload(
        build="26100.9725",
        files=(
            UupFile(name="a.cab", size=len(CONTENT), sha1=SHA1, url="http://x.microsoft.com/a.cab"),
            UupFile(name="b.cab", size=len(CONTENT), sha1=SHA1, url="http://x.microsoft.com/b.cab"),
        ),
        arch="amd64",
    )


class FakeSource:
    def __init__(self, payload: SourcePayload | None = None) -> None:
        self.payload = payload or make_payload()
        self.calls = 0

    def list_builds(self, *, search: str | None = None) -> tuple[object, ...]:
        return (
            SimpleNamespace(
                build="26100.9725", revision="9725", arch="x64", id="uuid-1", title="Windows 11"
            ),
        )

    def list_languages(self, _id: str) -> tuple[str, ...]:
        return ("zh-cn",)

    def list_editions(self, _id: str, _lang: str) -> tuple[object, ...]:
        return (SimpleNamespace(code="PROFESSIONAL", display_name="Windows Pro"),)

    def get_files(self, _id: str, *, language: str, edition: str) -> SourcePayload:
        self.calls += 1
        return self.payload


class FakeDownloader:
    def __init__(
        self, *, fail_first: bool = False, failures: int = 0, skip_files: bool = False
    ) -> None:
        self.fail_first = fail_first
        self.failures = failures
        self.skip_files = skip_files
        self.calls = 0

    async def download(self, manifest: object, *, dest_dir: Path, names: object = None) -> object:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise StageExecutionError("清单过期", code=ErrorCode.DOWNLOAD_CORRUPT)
        if self.failures and self.calls <= self.failures:
            raise StageExecutionError("清单过期", code=ErrorCode.DOWNLOAD_CORRUPT)
        dest_dir.mkdir(parents=True, exist_ok=True)
        files = []
        if not self.skip_files:
            for item in manifest.files:
                (dest_dir / item.name).write_bytes(CONTENT)
                files.append(SimpleNamespace(name=item.name, is_ok=True, state=None))
        return SimpleNamespace(verified_files=tuple(files), retry_rounds=0, files=tuple(files))


def make_ctx(tmp_path: Path, config: object | None = None) -> StageContext:
    cfg = config or make_config()
    plan = plan_pipeline(cfg).stage(StageKind.FETCH)
    assert plan is not None
    return StageContext(
        task_id="7f3a1b2c",
        config=cfg,  # type: ignore[arg-type]
        stage=StageKind.FETCH,
        plan=plan,
        work_dir=tmp_path / "work",
    )


def test_fetch_runs_all_substeps_and_writes_manifest(tmp_path: Path) -> None:
    source = FakeSource()
    downloader = FakeDownloader()
    handler = FetchStageHandler(source=source, downloader_factory=lambda _w: downloader)
    ctx = make_ctx(tmp_path)

    asyncio.run(handler.run(ctx))

    assert handler.outcome is not None
    assert handler.outcome.file_count == 2
    assert handler.outcome.verified_files == 2
    assert handler.outcome.total_bytes == 2 * len(CONTENT)
    manifest_path = tmp_path / "work" / "manifest.json"
    assert manifest_path.is_file()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["build"] == "26100.9725"
    assert len(payload["files"]) == 2
    assert (tmp_path / "work" / "download" / "a.cab").is_file()


def test_fetch_refreshes_manifest_when_download_fails(tmp_path: Path) -> None:
    """实测：URL 只有 ~12 分钟寿命 → 下载失败先刷新一次清单。"""
    source = FakeSource()
    downloader = FakeDownloader(failures=2)  # 首次 + 退避重试都失败，才触发刷新
    handler = FetchStageHandler(
        source=source, downloader_factory=lambda _w: downloader, retry_wait=0
    )
    ctx = make_ctx(tmp_path)

    asyncio.run(handler.run(ctx))

    assert handler.outcome is not None and handler.outcome.refreshed is True
    assert source.calls == 2, "退避重试仍失败后才重新拉清单"
    assert downloader.calls == 3


def test_verify_substep_catches_missing_files(tmp_path: Path) -> None:
    source = FakeSource()
    downloader = FakeDownloader(skip_files=True)
    handler = FetchStageHandler(source=source, downloader_factory=lambda _w: downloader)

    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path)))
    assert caught.value.code is ErrorCode.DOWNLOAD_CORRUPT
    assert "a.cab" in caught.value.message


def test_missing_source_is_internal_error(tmp_path: Path) -> None:
    handler = FetchStageHandler(downloader_factory=lambda _w: FakeDownloader())
    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path)))
    assert caught.value.code is ErrorCode.INTERNAL_ERROR


def test_non_online_entry_rejected(tmp_path: Path) -> None:
    config = make_config(mode=EntryPoint.IMAGE)
    config.input.image = {"path": "D:/a.iso", "type": "iso", "editions": [1]}  # type: ignore[assignment]
    handler = FetchStageHandler(source=FakeSource(), downloader_factory=lambda _w: FakeDownloader())

    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path, config)))
    assert caught.value.code is ErrorCode.CONFIG_SEMANTIC_INVALID


def test_describe_and_file_operations(tmp_path: Path) -> None:
    handler = FetchStageHandler(source=FakeSource(), downloader_factory=lambda _w: FakeDownloader())
    ctx = make_ctx(tmp_path)

    commands = handler.describe(ctx)
    assert any("listid.php" in item for item in commands)
    assert any("aria2c --input-file=" in item for item in commands)
    assert "--max-tries=3" in commands[-1]

    ops = handler.file_operations(ctx)
    actions = {op.action for op in ops}
    assert actions == {"create"}
    assert any(op.path.endswith("manifest.json") for op in ops)


def test_reusable_artifacts_after_run(tmp_path: Path) -> None:
    handler = FetchStageHandler(source=FakeSource(), downloader_factory=lambda _w: FakeDownloader())
    ctx = make_ctx(tmp_path)
    assert handler.reusable_artifacts(ctx) == ()

    asyncio.run(handler.run(ctx))
    assert handler.reusable_artifacts(ctx) == ("manifest.json",)


def test_existing_manifest_is_reused(tmp_path: Path) -> None:
    """磁盘上已有清单时不该重复联网（子步骤裁剪后只跑 download/verify）。"""
    config = make_config()
    plan = plan_pipeline(config).stage(StageKind.FETCH)
    assert plan is not None
    # 手工裁掉 manifest 子步骤，模拟续跑
    trimmed = type(plan)(
        stage=plan.stage,
        substeps=tuple(sub for sub in plan.substeps if sub.name != "manifest"),
        skip_reason=None,
        implied=False,
    )
    work = tmp_path / "work"
    manifest, _ = build_manifest(
        make_payload(),
        ManifestRequest(
            build="26100.9725",
            language="zh-cn",
            edition="professional",
            base_url="https://uupdump.net/",
            fetched_at=datetime.now(UTC),
        ),
    )
    from uup2iso.application.fetch import write_manifest

    write_manifest(work / "manifest.json", manifest)

    source = FakeSource()
    handler = FetchStageHandler(source=source, downloader_factory=lambda _w: FakeDownloader())
    ctx = StageContext(
        task_id="t",
        config=config,  # type: ignore[arg-type]
        stage=StageKind.FETCH,
        plan=trimmed,
        work_dir=work,
    )

    asyncio.run(handler.run(ctx))

    assert source.calls == 0, "清单已在磁盘，不该再联网"
    assert handler.outcome is not None and handler.outcome.verified_files == 2
