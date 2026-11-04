"""`Output` 阶段处理器门禁（输出 §3 原子提交、§5 重名、§6 校验分级、§8 伴生）。

重点守住三件事：**未校验内容绝不出现在最终文件名上**、重名默认加后缀、伴生与产物同语义。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from uup2iso.application.pipeline import StageContext, plan_pipeline
from uup2iso.application.pipeline.handlers import OutputStageHandler
from uup2iso.application.pipeline.handlers.output import (
    OutputArtifact,
    _count_images,
    is_iso_file,
    render_name,
    resolve_collision,
)
from uup2iso.core.errors import ErrorCode, StageExecutionError
from uup2iso.core.models import default_task_config
from uup2iso.core.models.config import (
    ConvertSection,
    OutputSection,
    SourceSection,
)
from uup2iso.core.models.enums import EntryPoint, OutputType
from uup2iso.core.models.pipeline import StageKind
from uup2iso.core.models.version import VersionResolution


class FakeRunner:
    """假 wimlib：按 verb 返回预设退出码与 stdout。"""

    def __init__(self, *, info_rc: int = 0, verify_rc: int = 0, images: int = 3) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.info_rc = info_rc
        self.verify_rc = verify_rc
        self.images = images

    async def run(self, spec: object, *, on_event: object = None, check: bool = True) -> object:
        args = tuple(spec.args)
        self.calls.append(args)
        verb = args[1]
        lines = (
            ["WIM Information:", f"Image Count: {self.images}", ""]
            if verb == "info"
            else ["Verifying"]
        )
        if on_event is not None:
            for text in lines:
                on_event(SimpleNamespace(text=text))
        rc = self.info_rc if verb == "info" else self.verify_rc
        return SimpleNamespace(returncode=rc, last_lines=tuple(lines))


class IsoRunner(FakeRunner):
    """假 oscdimg：按最后一个参数写一个带 ISO 签名的小文件。"""

    def __init__(self) -> None:
        super().__init__()
        self.last_args: list[str] = []

    async def run(self, spec: object, *, on_event: object = None, check: bool = True) -> object:
        args = [str(item) for item in spec.args]
        self.calls.append(tuple(args))
        if "7zr" in args[0].lower():
            # 假 7-Zip 列表：含关键文件，供 L2 布局校验
            if on_event is not None:
                for text in ("setup.exe", " sources\\install.wim", " boot\\etfsboot.com"):
                    on_event(SimpleNamespace(text=text))
            return SimpleNamespace(returncode=0, last_lines=("setup.exe",))
        self.last_args = args
        target = Path(args[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = bytearray(40000)
        payload[32769:32774] = b"CD001"
        target.write_bytes(bytes(payload))
        if on_event is not None:
            on_event(SimpleNamespace(text="OSCDIMG 2.56"))
        return SimpleNamespace(returncode=0, last_lines=("OSCDIMG",))


def make_config(tmp_path: Path, *, output_type: OutputType = OutputType.WIM) -> object:
    config = default_task_config(mode=EntryPoint.UUP_FOLDER)
    config.input.start_stage = "Convert"
    config.input.uup_folder = {"path": str(tmp_path / "uups")}  # type: ignore[assignment]
    config.convert = ConvertSection()  # type: ignore[assignment]
    config.source = SourceSection(  # type: ignore[call-arg]
        base_url="https://uupdump.net/",
        build="26300.9818",
        revision="9818",
        language="zh-cn",
        edition="professional",
        channel="Retail",
    )
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    config.output = OutputSection(dir=str(out), type=output_type)  # type: ignore[assignment]
    return config


def make_wim(ctx_work: Path, payload: bytes = b"WIMDATA" * 1000) -> Path:
    wim = ctx_work / "convert" / "ISOFOLDER" / "sources" / "install.wim"
    wim.parent.mkdir(parents=True, exist_ok=True)
    wim.write_bytes(payload)
    return wim


def make_ctx(tmp_path: Path, config: object | None = None) -> StageContext:
    cfg = config or make_config(tmp_path)
    plan = plan_pipeline(cfg).stage(StageKind.OUTPUT)  # type: ignore[arg-type]
    assert plan is not None
    ctx = StageContext(
        task_id="7f3a1b2c",
        config=cfg,  # type: ignore[arg-type]
        stage=StageKind.OUTPUT,
        plan=plan,
        work_dir=tmp_path / "work",
    )
    ctx.extras["version"] = VersionResolution(
        family="26H2", major=26300, build="26300.9818", revision="9818", language="zh-cn"
    )
    return ctx


# --- 命名与重名 -------------------------------------------------------------


def test_default_name_template(tmp_path: Path) -> None:
    """默认模板描述「装出来的系统」：Win11_{build}_{edition}_{arch}_{lang}。"""
    from uup2iso.core.models.config import OutputSection

    assert OutputSection().name_template == "Win11_{build}_{edition}_{arch}_{lang}"


def test_render_name_substitutes_variables() -> None:
    name = render_name(
        "{edition}_{build}_{arch}_{lang}",
        {"edition": "professional", "build": "26300.9818", "arch": "x64", "lang": "zh-cn"},
    )
    assert name == "professional_26300.9818_x64_zh-cn"


def test_render_name_sanitizes_illegal_chars() -> None:
    name = render_name("{edition}_{build}", {"edition": "a/b:c*?", "build": "1"})
    assert name == "a_b_c_1"
    assert not any(ch in name for ch in '\\/:*?"<>|')


def test_render_name_collapses_underscores_and_truncates() -> None:
    assert render_name("{a}__{b}", {"a": "x", "b": "y"}) == "x_y"
    long_name = render_name("{a}", {"a": "z" * 300})
    assert len(long_name) <= 200


def test_render_name_unknown_variable_falls_back() -> None:
    assert render_name("{edition}", {"edition": "unknown"}) == "unknown"


def test_resolve_collision_appends_suffix(tmp_path: Path) -> None:
    (tmp_path / "a.wim").write_bytes(b"x")

    final, renamed = resolve_collision(tmp_path, "a.wim")
    assert renamed is True
    assert final.name == "a_1.wim"

    final.write_bytes(b"y")
    final2, _ = resolve_collision(tmp_path, "a.wim")
    assert final2.name == "a_2.wim"


def test_resolve_collision_overwrite_strategy(tmp_path: Path) -> None:
    (tmp_path / "a.wim").write_bytes(b"x")
    final, renamed = resolve_collision(tmp_path, "a.wim", strategy="overwrite")
    assert renamed is False
    assert final.name == "a.wim"


# --- 入口与预检 -------------------------------------------------------------


def test_missing_wim_is_reported(tmp_path: Path) -> None:
    handler = OutputStageHandler(runner=FakeRunner(), tools_dir=tmp_path / "tools")
    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path)))
    assert caught.value.code is ErrorCode.INPUT_UNREADABLE
    assert "install.wim" in caught.value.message


def test_iso_without_folder_or_iso_is_unreadable(tmp_path: Path) -> None:
    """ISO 模式但既没有转换器产的 ISO、也没有 ISOFOLDER → 提示先跑 full 模式。"""
    config = make_config(tmp_path, output_type=OutputType.ISO)
    handler = OutputStageHandler(runner=FakeRunner(), tools_dir=tmp_path / "tools")

    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path, config)))
    assert caught.value.code is ErrorCode.INPUT_UNREADABLE
    assert "ISOFOLDER" in caught.value.message


def test_iso_packed_from_folder(tmp_path: Path) -> None:
    """有 ISOFOLDER 时用 oscdimg 打包，并校验 ISO 签名。"""
    config = make_config(tmp_path, output_type=OutputType.ISO)
    wim = make_wim(tmp_path / "work")
    folder = wim.parent.parent  # ISOFOLDER
    (folder / "boot").mkdir(exist_ok=True)
    (folder / "boot" / "etfsboot.com").write_bytes(b"BIOS")
    efi = folder / "efi" / "microsoft" / "boot"
    efi.mkdir(parents=True)
    (efi / "efisys.bin").write_bytes(b"UEFI")
    (folder / "setup.exe").write_bytes(b"SETUP")

    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "oscdimg.exe").write_bytes(b"MZ")
    (tools / "7zr.exe").write_bytes(b"MZ")

    runner = IsoRunner()
    handler = OutputStageHandler(runner=runner, tools_dir=tools)
    asyncio.run(handler.run(make_ctx(tmp_path, config)))

    outcome = handler.outcome
    assert outcome.final.suffix == ".iso"
    assert outcome.final.stat().st_size > 32774, "应写入带签名的 ISO"
    assert is_iso_file(outcome.final)
    assert any("-u2" in arg for arg in runner.last_args), "双启动需 -u2 + -yo"
    assert any(arg.startswith("-letfsboot") for arg in str(runner.last_args) for _ in [0]) or any(
        arg.startswith("-b") for arg in runner.last_args
    )


def test_iso_missing_boot_files_is_rejected(tmp_path: Path) -> None:
    config = make_config(tmp_path, output_type=OutputType.ISO)
    wim = make_wim(tmp_path / "work")
    folder = wim.parent.parent  # 没有 boot/ 与 efi/
    (folder / "setup.exe").write_bytes(b"SETUP")
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "oscdimg.exe").write_bytes(b"MZ")

    handler = OutputStageHandler(runner=FakeRunner(), tools_dir=tools)
    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path, config)))
    assert caught.value.code is ErrorCode.INPUT_DAMAGED


def test_oscdimg_args_shape(tmp_path: Path) -> None:
    from uup2iso.application.pipeline.handlers.output import build_oscdimg_args

    exe = tmp_path / "oscdimg.exe"
    args = build_oscdimg_args(
        exe,
        tmp_path / "folder",
        tmp_path / "out.iso",
        label="LABEL1",
        bios_boot=tmp_path / "etfsboot.com",
        uefi_boot=tmp_path / "efisys.bin",
    )

    assert args[0] == str(exe)
    assert any(arg.startswith("-b") for arg in args)
    assert "-u2" in args and any(arg.startswith("-yo") for arg in args)
    assert "-m" in args and "-o" in args and "-h" in args
    assert "-lLABEL1" in args
    assert args[-2:] == [str(tmp_path / "folder"), str(tmp_path / "out.iso")]


def test_empty_output_dir_defaults_to_program_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`output.dir` 为空 → 程序目录下的 output（不是磁盘根、也不是当前目录）。"""
    from uup2iso.application import startup

    monkeypatch.setattr(startup, "program_dir", lambda: tmp_path / "app")
    config = make_config(tmp_path)
    config.output.dir = ""  # type: ignore[union-attr]
    make_wim(tmp_path / "work")

    handler = OutputStageHandler(runner=FakeRunner(), tools_dir=tmp_path / "tools")
    asyncio.run(handler.run(make_ctx(tmp_path, config)))

    final = handler.outcome.final  # type: ignore[union-attr]
    assert final.parent == tmp_path / "app" / "output", "默认落在程序目录下"
    assert final.parent.is_dir(), "目录应被自动创建"


def test_relative_output_dir_is_relative_to_program_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uup2iso.application import startup

    monkeypatch.setattr(startup, "program_dir", lambda: tmp_path / "app")
    config = make_config(tmp_path)
    config.output.dir = "out-here"  # type: ignore[union-attr]
    make_wim(tmp_path / "work")

    handler = OutputStageHandler(runner=FakeRunner(), tools_dir=tmp_path / "tools")
    asyncio.run(handler.run(make_ctx(tmp_path, config)))

    assert handler.outcome.final.parent == tmp_path / "app" / "out-here"  # type: ignore[union-attr]


def test_absolute_output_dir_is_used_as_is(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    target = tmp_path / "custom-out"
    config.output.dir = str(target)  # type: ignore[union-attr]
    make_wim(tmp_path / "work")

    handler = OutputStageHandler(runner=FakeRunner(), tools_dir=tmp_path / "tools")
    asyncio.run(handler.run(make_ctx(tmp_path, config)))

    assert handler.outcome.final.parent == target  # type: ignore[union-attr]


def test_disk_full_is_preflight_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "disk_usage", lambda _p: SimpleNamespace(total=1, used=1, free=10))
    make_wim(tmp_path / "work")
    handler = OutputStageHandler(runner=FakeRunner(), tools_dir=tmp_path / "tools")

    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path)))
    assert caught.value.code is ErrorCode.DISK_FULL
    assert not (tmp_path / "out" / ".uup2iso-tmp").exists(), "预检失败不得创建暂存区"


# --- 端到端 -----------------------------------------------------------------


def test_output_commits_atomically_with_sidecars(tmp_path: Path) -> None:
    make_wim(tmp_path / "work")
    runner = FakeRunner()
    handler = OutputStageHandler(runner=runner, tools_dir=tmp_path / "tools")
    ctx = make_ctx(tmp_path)

    asyncio.run(handler.run(ctx))

    assert isinstance(handler.outcome, OutputArtifact)
    final = handler.outcome.final
    assert final.is_file() and final.parent == tmp_path / "out"
    assert final.name.endswith(".wim")
    assert handler.outcome.size == final.stat().st_size
    assert handler.outcome.sha256

    sidecars = {path.name for path in handler.outcome.sidecars}
    assert f"{final.name}.sha256" in sidecars
    assert "readme.txt" in " ".join(sidecars)

    sha_text = (tmp_path / "out" / f"{final.name}.sha256").read_text(encoding="utf-8")
    assert sha_text.startswith(handler.outcome.sha256)
    assert f"*{final.name}" in sha_text

    assert not (tmp_path / "out" / ".uup2iso-tmp" / ctx.task_id).exists(), "提交后应清空暂存"

    info_calls = [call for call in runner.calls if call[1] == "info"]
    assert len(info_calls) == 1, "L2 默认开启"


def test_second_run_gets_suffix(tmp_path: Path) -> None:
    make_wim(tmp_path / "work")
    handler = OutputStageHandler(runner=FakeRunner(), tools_dir=tmp_path / "tools")

    asyncio.run(handler.run(make_ctx(tmp_path)))
    first = handler.outcome.final.name  # type: ignore[union-attr]
    asyncio.run(handler.run(make_ctx(tmp_path)))
    second = handler.outcome.final.name  # type: ignore[union-attr]

    assert first.endswith(".wim") and second != first
    assert "_1" in second


def test_verify_failure_keeps_staging_and_no_final(tmp_path: Path) -> None:
    make_wim(tmp_path / "work")
    handler = OutputStageHandler(runner=FakeRunner(info_rc=1), tools_dir=tmp_path / "tools")

    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path)))
    assert caught.value.code is ErrorCode.OUTPUT_VERIFY_FAILED

    out = tmp_path / "out"
    assert not list(out.glob("*.wim")), "校验失败不得产出最终文件"
    assert list((out / ".uup2iso-tmp").rglob("*.part")), "暂存应保留以便排查"


def test_level3_calls_wimlib_verify(tmp_path: Path) -> None:
    make_wim(tmp_path / "work")
    config = make_config(tmp_path)
    config.output.verify_level = 3  # type: ignore[union-attr]
    runner = FakeRunner()
    handler = OutputStageHandler(runner=runner, tools_dir=tmp_path / "tools")

    asyncio.run(handler.run(make_ctx(tmp_path, config)))

    verbs = [call[1] for call in runner.calls]
    assert verbs == ["info", "verify"]


def test_level1_skips_wimlib(tmp_path: Path) -> None:
    make_wim(tmp_path / "work")
    config = make_config(tmp_path)
    config.output.verify_level = 1  # type: ignore[union-attr]
    runner = FakeRunner()
    handler = OutputStageHandler(runner=runner, tools_dir=tmp_path / "tools")

    asyncio.run(handler.run(make_ctx(tmp_path, config)))

    assert runner.calls == [], "L1 不应调用 wimlib"


def test_missing_runner_is_internal_error(tmp_path: Path) -> None:
    make_wim(tmp_path / "work")
    handler = OutputStageHandler(tools_dir=tmp_path / "tools")

    with pytest.raises(StageExecutionError) as caught:
        asyncio.run(handler.run(make_ctx(tmp_path)))
    assert caught.value.code is ErrorCode.INTERNAL_ERROR


# --- 预演 -------------------------------------------------------------------


def test_describe_lists_commit_chain(tmp_path: Path) -> None:
    make_wim(tmp_path / "work")
    handler = OutputStageHandler(runner=FakeRunner(), tools_dir=tmp_path / "tools")
    commands = "\n".join(handler.describe(make_ctx(tmp_path)))

    assert "copy" in commands
    assert "wimlib-imagex info" in commands
    assert "rename(" in commands
    assert ".uup2iso-tmp" in commands


def test_describe_without_source_is_informative(tmp_path: Path) -> None:
    handler = OutputStageHandler(runner=FakeRunner(), tools_dir=tmp_path / "tools")
    commands = handler.describe(make_ctx(tmp_path))
    assert commands[0].startswith("# 尚未就绪")


def test_file_operations_lists_staging_and_sidecars(tmp_path: Path) -> None:
    make_wim(tmp_path / "work")
    handler = OutputStageHandler(runner=FakeRunner(), tools_dir=tmp_path / "tools")
    ops = handler.file_operations(make_ctx(tmp_path))
    paths = [op.path for op in ops]

    assert any(p.endswith(".part") for p in paths), "必须走暂存"
    assert any(p.endswith(".wim") for p in paths)
    assert any(p.endswith(".sha256") for p in paths)


def test_count_images_parses_output() -> None:
    assert _count_images("Image Count: 3\n") == 3
    assert _count_images("garbage") == 0
    assert _count_images(None) == 0
