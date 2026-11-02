"""`Prepare` 处理器门禁（输入 §4、§7；编排 §3）。

用假 ops 覆盖全部分支：子步骤裁剪、`probe.json` 落盘、自检失败、dry-run 命令。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from uup2iso.application.pipeline import plan_pipeline
from uup2iso.application.pipeline.handlers.prepare import (
    PrepareStageHandler,
    resolve_input_path,
    write_probe_json,
)
from uup2iso.core.errors import ErrorCode, InputError, StageExecutionError
from uup2iso.core.models import default_task_config
from uup2iso.core.models.enums import EntryPoint, ImageType
from uup2iso.core.models.pipeline import StageKind
from uup2iso.infrastructure.input import InputKind, ProbeReport


class FakeOps:
    def __init__(self, *, selfcheck_ok: bool = True, kind: InputKind = InputKind.ISO) -> None:
        self.calls: list[str] = []
        self._selfcheck_ok = selfcheck_ok
        self._kind = kind

    def probe(self, path: Path) -> ProbeReport:
        self.calls.append(f"probe:{path.name}")
        return ProbeReport(type=self._kind, path=path)

    def materialize(self, ctx: object, report: ProbeReport) -> Path | None:
        self.calls.append("materialize")
        return Path("image/install.wim")

    def extract(self, ctx: object, report: ProbeReport) -> Path | None:
        self.calls.append("extract")
        return Path("image")

    def selfcheck(self, ctx: object, report: ProbeReport) -> tuple[bool, str]:
        self.calls.append("selfcheck")
        return self._selfcheck_ok, "install.wim 可读" if self._selfcheck_ok else "install.wim 缺失"


def make_ctx(
    tmp_path: Path, *, image_type: ImageType = ImageType.ISO, mode: EntryPoint = EntryPoint.IMAGE
) -> SimpleNamespace:
    config = default_task_config(mode=mode)
    config.input.start_stage = "Prepare" if mode is not EntryPoint.ONLINE else "Convert"
    if mode is EntryPoint.IMAGE:
        config.input.image = {  # type: ignore[assignment]
            "path": str(tmp_path / f"input.{image_type.value}"),
            "type": image_type.value,
            "editions": [1],
        }
    plan = plan_pipeline(config).stage(StageKind.PREPARE)
    assert plan is not None
    return SimpleNamespace(
        task_id="7f3a1b2c",
        config=config,
        plan=plan,
        work_dir=tmp_path / "work" / "7f3a1b2c",
        stage=StageKind.PREPARE,
        attempt=1,
        cancel=None,
        log=None,
        progress=None,
        extras={},
    )


def test_iso_runs_probe_extract_selfcheck(tmp_path: Path) -> None:
    ops = FakeOps()
    handler = PrepareStageHandler(ops)
    ctx = make_ctx(tmp_path)

    asyncio.run(handler.run(ctx))

    assert ops.calls == ["probe:input.iso", "extract", "selfcheck"]
    assert handler.outcome is not None
    assert handler.outcome.type == "iso"


def test_esd_runs_materialize_instead_of_extract(tmp_path: Path) -> None:
    ops = FakeOps(kind=InputKind.ESD)
    handler = PrepareStageHandler(ops)
    ctx = make_ctx(tmp_path, image_type=ImageType.ESD)

    asyncio.run(handler.run(ctx))

    assert ops.calls == ["probe:input.esd", "materialize", "selfcheck"]


def test_probe_json_is_written(tmp_path: Path) -> None:
    handler = PrepareStageHandler(FakeOps())
    ctx = make_ctx(tmp_path)

    asyncio.run(handler.run(ctx))

    probe_json = ctx.work_dir / "input" / "probe.json"
    assert probe_json.is_file()
    payload = json.loads(probe_json.read_text(encoding="utf-8"))
    assert payload["type"] == "iso"
    assert payload["schemaVersion"] == "1.0"
    assert not list(probe_json.parent.glob("*.tmp")), "原子写不应留下 tmp"

    assert handler.outcome is not None
    assert handler.outcome.probe_json == probe_json


def test_selfcheck_failure_raises_input_error(tmp_path: Path) -> None:
    handler = PrepareStageHandler(FakeOps(selfcheck_ok=False))

    with pytest.raises(InputError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path)))

    assert caught.value.code is ErrorCode.INPUT_DAMAGED
    assert "install.wim 缺失" in caught.value.message


def test_missing_input_raises_input_error(tmp_path: Path) -> None:
    """默认使用内置 ops：输入不存在时报 INPUT_UNREADABLE（不是内部错误）。"""
    handler = PrepareStageHandler()

    with pytest.raises(InputError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path)))
    assert caught.value.code is ErrorCode.INPUT_UNREADABLE


def test_builtin_ops_refuses_unwired_engine_actions(tmp_path: Path) -> None:
    """物化/解包属引擎层：内置实现必须**明确报未接线**，不能静默成功。"""
    from uup2iso.application.pipeline.handlers.prepare import BuiltinPrepareOps

    ops = BuiltinPrepareOps()
    ctx = make_ctx(tmp_path)
    report = ops.probe(tmp_path / "fake.iso") if False else None  # 不真探测

    with pytest.raises(StageExecutionError) as caught:
        ops.materialize(ctx, report)  # type: ignore[arg-type]
    assert caught.value.code is ErrorCode.INTERNAL_ERROR
    assert "尚未接线" in caught.value.message

    with pytest.raises(StageExecutionError):
        ops.extract(ctx, report)  # type: ignore[arg-type]


def test_builtin_selfcheck_accepts_uup_folder_without_manifest(tmp_path: Path) -> None:
    """无清单的 UUP 目录也应通过自检（P0-2 换判据）。"""
    from types import SimpleNamespace

    from uup2iso.application.pipeline.handlers.prepare import BuiltinPrepareOps
    from uup2iso.core.models import default_task_config
    from uup2iso.core.models.enums import EntryPoint

    uup = tmp_path / "uups"
    uup.mkdir()
    (uup / "6c7bf258-2632-47dd-83ee-c23945978da1.AggregatedMetadata.cab").write_bytes(b"CAB")
    (uup / "Microsoft-Windows-Foundation-Package.ESD").write_bytes(b"ESD")
    (uup / "professional_zh-cn.esd").write_bytes(b"ESD")

    config = default_task_config(mode=EntryPoint.UUP_FOLDER)
    config.input.uup_folder = {"path": str(uup)}  # type: ignore[assignment]
    ctx = SimpleNamespace(config=config, work_dir=tmp_path / "work")

    ok, detail = BuiltinPrepareOps().selfcheck(ctx, None)  # type: ignore[arg-type]
    assert ok is True
    assert "2 个 .esd" in detail
    assert "无清单文件" in detail


def test_builtin_selfcheck_rejects_partial_download(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from uup2iso.application.pipeline.handlers.prepare import BuiltinPrepareOps
    from uup2iso.core.models import default_task_config
    from uup2iso.core.models.enums import EntryPoint

    uup = tmp_path / "uups"
    uup.mkdir()
    (uup / "a.esd").write_bytes(b"ESD")
    (uup / "b.cab.part").write_bytes(b"partial")

    config = default_task_config(mode=EntryPoint.UUP_FOLDER)
    config.input.uup_folder = {"path": str(uup)}  # type: ignore[assignment]
    ctx = SimpleNamespace(config=config, work_dir=tmp_path / "work")

    ok, detail = BuiltinPrepareOps().selfcheck(ctx, None)  # type: ignore[arg-type]
    assert ok is False
    assert "残留" in detail


def test_builtin_selfcheck_rejects_empty_folder(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from uup2iso.application.pipeline.handlers.prepare import BuiltinPrepareOps
    from uup2iso.core.models import default_task_config
    from uup2iso.core.models.enums import EntryPoint

    uup = tmp_path / "empty"
    uup.mkdir()
    config = default_task_config(mode=EntryPoint.UUP_FOLDER)
    config.input.uup_folder = {"path": str(uup)}  # type: ignore[assignment]
    ctx = SimpleNamespace(config=config, work_dir=tmp_path / "work")

    ok, detail = BuiltinPrepareOps().selfcheck(ctx, None)  # type: ignore[arg-type]
    assert ok is False
    assert "未找到任何 .esd" in detail


def test_describe_lists_planned_commands(tmp_path: Path) -> None:
    handler = PrepareStageHandler(FakeOps())
    commands = handler.describe(make_ctx(tmp_path))

    assert any(command.startswith("wimlib-imagex info --xml") for command in commands)
    assert any(command.startswith("7z x") for command in commands)
    assert any("自检" in command for command in commands)


def test_reusable_artifacts_reports_existing_files(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    handler = PrepareStageHandler(FakeOps())
    assert handler.reusable_artifacts(ctx) == ()

    (ctx.work_dir / "input").mkdir(parents=True)
    (ctx.work_dir / "input" / "probe.json").write_text("{}", encoding="utf-8")
    assert handler.reusable_artifacts(ctx) == ("input/probe.json",)


def test_entry_a_has_no_local_input(tmp_path: Path) -> None:
    config = default_task_config(mode=EntryPoint.ONLINE)
    config.source = {  # type: ignore[assignment]
        "baseUrl": "https://uupdump.net/",
        "build": "26100.1",
        "revision": "1",
        "language": "zh-cn",
        "edition": "professional",
        "channel": "Retail",
    }

    with pytest.raises(InputError) as caught:
        resolve_input_path(config)
    assert caught.value.code is ErrorCode.INPUT_UNREADABLE


def test_write_probe_json_is_atomic(tmp_path: Path) -> None:
    report = ProbeReport(type=InputKind.ISO, path=tmp_path / "a.iso")
    target = write_probe_json(tmp_path / "work", report)

    assert target.is_file()
    assert not list(target.parent.glob("*.tmp"))
    assert json.loads(target.read_text(encoding="utf-8"))["type"] == "iso"
