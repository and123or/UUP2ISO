"""转换器产物名解析门禁（消除 `unknown` 的主要来源）。

样本来自真实运行：
`26100.1.240331-1435.GE_RELEASE_CLIENTPRO_OEMRET_X64FRE_ZH-CN.ISO`
"""

from __future__ import annotations

from pathlib import Path

from uup2iso.infrastructure.input.image_name import parse_converter_iso_name

REAL = "26100.1.240331-1435.GE_RELEASE_CLIENTPRO_OEMRET_X64FRE_ZH-CN.ISO"


def test_parses_real_converter_output() -> None:
    info = parse_converter_iso_name(REAL)

    assert info is not None
    assert (info.build, info.revision) == ("26100", "1")
    assert info.date == "240331-1435"
    assert info.release_type == "RELEASE"
    assert info.channel == "OEMRET"
    assert info.edition_token == "CLIENTPRO"
    assert info.edition == "professional"
    assert info.arch == "x64"
    assert info.language == "zh-cn"
    assert info.baseline == "26100.1.240331-1435"


def test_volume_channel_is_kept() -> None:
    info = parse_converter_iso_name(
        "26100.1.240331-1435.GE_RELEASE_PROFESSIONALVL_VOL_X64FRE_EN-US.ISO"
    )
    assert info is not None
    assert info.channel == "VOL"
    assert info.edition == "professional"
    assert info.language == "en-us"


def test_home_edition_mapping() -> None:
    info = parse_converter_iso_name("26100.1.240331-1435.GE_RELEASE_CORE_OEMRET_X64FRE_ZH-CN")
    assert info is not None and info.edition == "home"


def test_unknown_edition_token_is_lowercased_not_dropped() -> None:
    """认不得的版本 token 原样小写保留——宁可给 clientpro，也不退回 unknown。"""
    info = parse_converter_iso_name(
        "26100.1.240331-1435.AAA_RELEASE_CLIENTPROX_OEMRET_ARM64FRE_JA-JP.ISO"
    )
    assert info is not None
    assert info.edition == "clientprox"
    assert info.arch == "arm64"


def test_non_converter_name_returns_none() -> None:
    assert parse_converter_iso_name("install.wim") is None
    assert parse_converter_iso_name("professional_26300.9818_x64_zh-cn.iso") is None
    assert parse_converter_iso_name("") is None


# --- 与 Output 命名联动 -----------------------------------------------------


def test_output_name_uses_image_info(tmp_path: Path) -> None:
    """`{arch}` 不再落到 unknown：ISO 源的文件名解析结果会补位。"""
    from uup2iso.application.pipeline import StageContext, plan_pipeline
    from uup2iso.application.pipeline.handlers import OutputStageHandler
    from uup2iso.core.models import default_task_config
    from uup2iso.core.models.config import ConvertSection, OutputSection, SourceSection
    from uup2iso.core.models.enums import EntryPoint, OutputType
    from uup2iso.core.models.pipeline import StageKind

    config = default_task_config(mode=EntryPoint.UUP_FOLDER)
    config.input.start_stage = "Convert"
    config.input.uup_folder = {"path": str(tmp_path)}
    config.convert = ConvertSection()
    config.source = SourceSection(
        base_url="https://uupdump.net/",
        build="26300.9818",
        revision="9818",
        language="",
        edition="",
        channel="Retail",
    )
    config.output = OutputSection(dir=str(tmp_path), type=OutputType.ISO)

    plan = plan_pipeline(config).stage(StageKind.OUTPUT)
    ctx = StageContext(
        task_id="t1", config=config, stage=StageKind.OUTPUT, plan=plan, work_dir=tmp_path
    )
    handler = OutputStageHandler(tools_dir=tmp_path / "tools")
    name = handler._compose_name(ctx, config, Path(REAL))

    assert "unknown" not in name, f"不该再出现 unknown：{name}"
    assert name.startswith("Win11_26100.1_"), f"产物名应描述镜像版本：{name}"
    assert "professional" in name and "x64" in name and "zh-cn" in name
