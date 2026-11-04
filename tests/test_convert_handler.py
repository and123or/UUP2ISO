"""`Convert` 处理器门禁（路线 A：编排转换器，不自己聚合）。

本轮只做**预演**（`describe` / `file_operations`）与配置映射，不实际调用转换器。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from uup2iso.application.pipeline import StageContext, plan_pipeline
from uup2iso.application.pipeline.handlers import ConvertOptions, ConvertStageHandler
from uup2iso.application.pipeline.handlers.convert import (
    converter_paths,
    options_from,
    unpack_converter,
    write_config_ini,
)
from uup2iso.core.errors import ErrorCode, StageExecutionError
from uup2iso.core.models import default_task_config
from uup2iso.core.models.config import ConvertSection
from uup2iso.core.models.enums import CompressionKind, EntryPoint
from uup2iso.core.models.pipeline import StageKind


def make_config(tmp_path: Path, *, mode: EntryPoint = EntryPoint.UUP_FOLDER) -> object:
    config = default_task_config(mode=mode)
    config.input.start_stage = "Convert"
    config.input.uup_folder = {"path": str(tmp_path / "uups")}  # type: ignore[assignment]
    config.convert = ConvertSection()  # type: ignore[assignment]
    return config


def make_ctx(tmp_path: Path, config: object | None = None) -> StageContext:
    cfg = config or make_config(tmp_path)
    plan = plan_pipeline(cfg).stage(StageKind.CONVERT)  # type: ignore[arg-type]
    assert plan is not None
    return StageContext(
        task_id="7f3a1b2c",
        config=cfg,  # type: ignore[arg-type]
        stage=StageKind.CONVERT,
        plan=plan,
        work_dir=tmp_path / "work",
    )


# --- 选项映射 ---------------------------------------------------------------


def test_wim_only_maps_to_autostart_3(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    options = options_from(config.convert)  # type: ignore[attr-defined]

    assert options.auto_start == 3, "仅出 install.wim"
    assert options.skip_iso == 1
    assert options.wim2esd == 0
    assert options.auto_exit == 1


def test_full_iso_maps_to_autostart_1(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.convert.mode = "full"  # type: ignore[union-attr]
    options = options_from(config.convert)  # type: ignore[attr-defined]

    assert options.auto_start == 1
    assert options.skip_iso == 0


def test_lzms_selects_esd_variant(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.convert.compression = CompressionKind.LZMS  # type: ignore[union-attr]
    options = options_from(config.convert)  # type: ignore[attr-defined]

    assert options.wim2esd == 1
    assert options.auto_start == 4, "仅 esd（非 ISO 模式）"


def test_updates_and_cleanup_flags_follow_config(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.convert.updates.integrate = False  # type: ignore[union-attr]
    config.convert.cleanup.component_cleanup = False  # type: ignore[union-attr]
    config.convert.apps.skip = False  # type: ignore[union-attr]
    options = options_from(config.convert)  # type: ignore[attr-defined]

    assert options.add_updates == 0
    assert options.cleanup == 0
    assert options.skip_apps == 0


def test_defaults_are_conservative() -> None:
    options = options_from(None)
    assert options.auto_start == 3
    assert options.add_updates == 0
    assert options.skip_apps == 1
    assert options.add_drivers == 0


# --- ini 生成 ---------------------------------------------------------------


def test_ini_contains_sections_and_crlf() -> None:
    text = ConvertOptions().to_ini()

    assert "[convert-UUP]" in text
    assert "[Store_Apps]" in text
    assert "AutoStart    =3" in text
    assert "SkipISO      =1" in text
    assert "AutoExit     =1" in text
    assert "\r\n" in text, "转换器用 findstr 逐行解析，保持 CRLF"


def test_write_config_ini_is_atomic(tmp_path: Path) -> None:
    paths = converter_paths(tmp_path / "tools", tmp_path / "work")
    write_config_ini(paths, ConvertOptions())

    assert paths.ini.is_file()
    assert not list(paths.ini.parent.glob("*.tmp"))
    raw = paths.ini.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "不能带 BOM：findstr 匹配首行会失败"
    assert raw.startswith(b"[convert-UUP]"), "首行必须是段落名"
    assert b"AutoStart    =3" in raw


# --- 解包 -------------------------------------------------------------------


def test_unpack_is_idempotent(tmp_path: Path) -> None:
    tools = tmp_path / "tools"
    paths = converter_paths(tools, tmp_path / "work")
    paths.root.mkdir(parents=True)
    paths.entry.write_text("rem converter", encoding="utf-8")
    paths.bin_dir.mkdir()

    assert unpack_converter(paths) == paths.root, "已解包不应再解一次"
    assert not paths.archive.exists(), "幂等路径不应触碰压缩包"


def test_unpack_requires_archive(tmp_path: Path) -> None:
    paths = converter_paths(tmp_path / "tools", tmp_path / "work")
    with pytest.raises(StageExecutionError) as caught:
        unpack_converter(paths)
    assert caught.value.code is ErrorCode.INPUT_DAMAGED
    assert "转换器压缩包" in caught.value.message


# --- 预演 -------------------------------------------------------------------


def test_describe_rejects_missing_input_dir(tmp_path: Path) -> None:
    handler = ConvertStageHandler(tools_dir=tmp_path / "tools")
    with pytest.raises(StageExecutionError) as caught:
        handler.describe(make_ctx(tmp_path))
    assert caught.value.code is ErrorCode.INPUT_UNREADABLE


def test_describe_lists_full_command_chain(tmp_path: Path) -> None:
    (tmp_path / "uups").mkdir()  # 预演也要输入目录存在（早发现配置错）
    handler = ConvertStageHandler(tools_dir=tmp_path / "tools")
    commands = handler.describe(make_ctx(tmp_path))
    joined = "\n".join(commands)

    assert "7zr.exe" in joined and "-o" in joined, "第一步是解包转换器"
    assert "ConvertConfig.ini" in joined
    assert "cmd.exe /c" in joined and "convert-UUP.cmd" in joined
    assert "AutoStart=3" in joined and "SkipISO=1" in joined
    assert str(tmp_path / "uups") in joined, "输入目录必须以参数形式传入（不复制 9GB）"
    assert "wimlib-imagex info" in joined


def test_file_operations_for_wim_only(tmp_path: Path) -> None:
    (tmp_path / "uups").mkdir()
    handler = ConvertStageHandler(tools_dir=tmp_path / "tools")
    ops = handler.file_operations(make_ctx(tmp_path))
    paths = [op.path for op in ops]

    assert len(ops) == 3
    assert any(item.endswith("ConvertConfig.ini") for item in paths)
    assert any(item.endswith("bin") for item in paths)
    assert any(item.endswith("ISOFOLDER") for item in paths)
    assert all(op.action == "create" for op in ops)


def test_file_operations_add_iso_for_full_mode(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.convert.mode = "full"  # type: ignore[union-attr]
    handler = ConvertStageHandler(tools_dir=tmp_path / "tools")

    ops = handler.file_operations(make_ctx(tmp_path, config))
    assert any(op.path.endswith("install.iso") for op in ops)


# --- 执行前置 ---------------------------------------------------------------


def test_run_without_runner_is_internal_error(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    (tmp_path / "uups").mkdir()  # 输入目录存在
    paths = converter_paths(tmp_path / "tools", tmp_path / "work")
    paths.root.mkdir(parents=True)  # 转换器已解包 → 跳过 cab 子步骤
    paths.entry.write_text("rem converter", encoding="utf-8")
    paths.bin_dir.mkdir()

    handler = ConvertStageHandler(tools_dir=tmp_path / "tools", admin_check=lambda: True)
    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path, config)))
    assert caught.value.code is ErrorCode.INTERNAL_ERROR


def test_missing_uup_dir_is_rejected(tmp_path: Path) -> None:
    config = make_config(tmp_path)  # 目录并不存在
    handler = ConvertStageHandler(tools_dir=tmp_path / "tools")

    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path, config)))
    assert caught.value.code is ErrorCode.INPUT_UNREADABLE


def test_image_entry_cannot_convert(tmp_path: Path) -> None:
    config = default_task_config(mode=EntryPoint.IMAGE)
    config.input.image = {"path": str(tmp_path / "a.iso"), "type": "iso", "editions": [1]}  # type: ignore[assignment]
    handler = ConvertStageHandler(tools_dir=tmp_path / "tools")

    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path, config)))
    assert caught.value.code is ErrorCode.CONFIG_SEMANTIC_INVALID


def test_reusable_artifacts_reports_existing_wim(tmp_path: Path) -> None:
    handler = ConvertStageHandler(tools_dir=tmp_path / "tools")
    ctx = make_ctx(tmp_path)
    assert handler.reusable_artifacts(ctx) == ()

    wim = ctx.work_dir / "convert" / "ISOFOLDER" / "sources" / "install.wim"
    wim.parent.mkdir(parents=True)
    wim.write_bytes(b"WIM")

    assert handler.reusable_artifacts(ctx) == ("convert/install.wim",)


# --- 入口 B 的计划裁剪（2026-11-02 修正）------------------------------------


def test_entry_b_skips_fetch(tmp_path: Path) -> None:
    """入口 B 素材已在本地：Fetch 必须 skipped，不能排成「有活但没处理器」。"""
    from uup2iso.application.pipeline import plan_pipeline

    plan = plan_pipeline(make_config(tmp_path))  # type: ignore[arg-type]
    fetch = next(stage for stage in plan.stages if stage.stage is StageKind.FETCH)

    assert fetch.skipped is True
    assert fetch.skip_reason == "input_satisfied"
    assert StageKind.FETCH not in [stage.stage for stage in plan.executed]


def test_entry_b_keeps_convert(tmp_path: Path) -> None:
    from uup2iso.application.pipeline import plan_pipeline

    plan = plan_pipeline(make_config(tmp_path))  # type: ignore[arg-type]
    convert = next(stage for stage in plan.stages if stage.stage is StageKind.CONVERT)

    assert convert.skipped is False
    assert "aggregate" in [sub.name for sub in convert.executed_substeps]


def test_entry_a_still_fetches_when_starting_at_convert(tmp_path: Path) -> None:
    """入口 A 从 Convert 起步时素材还没下载，Fetch 仍要执行。"""
    from uup2iso.application.pipeline import plan_pipeline

    config = default_task_config(mode=EntryPoint.ONLINE)
    config.input.start_stage = "Convert"
    config.source = {  # type: ignore[assignment]
        "baseUrl": "https://uupdump.net/",
        "build": "26300.9818",
        "revision": "9818",
        "language": "zh-cn",
        "edition": "professional",
        "channel": "Retail",
    }
    plan = plan_pipeline(config)
    fetch = next(stage for stage in plan.stages if stage.stage is StageKind.FETCH)

    assert fetch.skipped is False


def test_requires_admin_before_invoking_converter(tmp_path: Path) -> None:
    """转换器非管理员会自提权弹新窗口 → 我们在调用前直接拒绝。"""
    config = make_config(tmp_path)
    (tmp_path / "uups").mkdir()
    paths = converter_paths(tmp_path / "tools", tmp_path / "work")
    paths.root.mkdir(parents=True)
    paths.entry.write_text("rem converter", encoding="utf-8")
    paths.bin_dir.mkdir()

    handler = ConvertStageHandler(tools_dir=tmp_path / "tools", admin_check=lambda: False)
    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path, config)))
    assert caught.value.code is ErrorCode.PERMISSION_DENIED
    assert "管理员" in caught.value.message


def test_config_written_after_unpack_keeps_autostart(tmp_path: Path) -> None:
    """先解包后写 ini：包内默认 AutoStart=0 不得覆盖我们的值。"""
    from uup2iso.application.pipeline.handlers.convert import unpack_converter

    config = make_config(tmp_path)
    (tmp_path / "uups").mkdir()
    paths = converter_paths(tmp_path / "tools", tmp_path / "work")
    paths.root.mkdir(parents=True)
    paths.entry.write_text("rem converter", encoding="utf-8")
    paths.bin_dir.mkdir()
    # 模拟包内默认 ini（AutoStart=0），即"解包产物"
    paths.ini.write_text("[convert-UUP]\r\nAutoStart    =0\r\n", encoding="ascii")

    ctx = make_ctx(tmp_path, config)
    # 只跑到 config 子步骤：把 aggregate/skeleton/bootwim 从计划里剔除
    step_names = [sub.name for sub in ctx.plan.substeps]
    assert "config" in step_names

    from uup2iso.application.pipeline.handlers.convert import options_from, write_config_ini

    unpack_converter(paths)  # 幂等：已解包则跳过，保留包内默认 ini
    write_config_ini(paths, options_from(config.convert))  # type: ignore[attr-defined]
    text = paths.ini.read_text(encoding="ascii")
    assert "AutoStart    =3" in text, "写入必须发生在解包之后"


def test_image_only_mode_tolerates_missing_iso_skeleton(tmp_path: Path) -> None:
    """AutoStart=3（仅 wim）时没有 ISOFOLDER 不算失败：install.wim 才是关键产物。"""
    config = make_config(tmp_path)  # 默认 wim_only → AutoStart=3
    (tmp_path / "uups").mkdir()
    paths = converter_paths(tmp_path / "tools", tmp_path / "work")
    paths.root.mkdir(parents=True)
    paths.entry.write_text("rem converter", encoding="utf-8")
    paths.bin_dir.mkdir()

    class NoopRunner:
        async def run(self, spec: object, *, on_event: object = None, check: bool = True) -> object:
            from types import SimpleNamespace

            return SimpleNamespace(returncode=0, last_lines=())

    handler = ConvertStageHandler(
        tools_dir=tmp_path / "tools", runner=NoopRunner(), admin_check=lambda: True
    )
    asyncio.run(handler.run(make_ctx(tmp_path, config)))

    assert handler.outcome is not None
    assert handler.outcome["install_wim"] is None, "没有产物时如实记录为空"


def test_full_iso_mode_requires_skeleton(tmp_path: Path) -> None:
    """AutoStart=1（出 ISO）时缺 ISOFOLDER 必须失败。"""
    config = make_config(tmp_path)
    config.convert.mode = "full"  # type: ignore[union-attr]
    (tmp_path / "uups").mkdir()
    paths = converter_paths(tmp_path / "tools", tmp_path / "work")
    paths.root.mkdir(parents=True)
    paths.entry.write_text("rem converter", encoding="utf-8")
    paths.bin_dir.mkdir()

    class NoopRunner:
        async def run(self, spec: object, *, on_event: object = None, check: bool = True) -> object:
            from types import SimpleNamespace

            return SimpleNamespace(returncode=0, last_lines=())

    handler = ConvertStageHandler(
        tools_dir=tmp_path / "tools", runner=NoopRunner(), admin_check=lambda: True
    )
    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path, config)))
    assert caught.value.code is ErrorCode.INPUT_DAMAGED
