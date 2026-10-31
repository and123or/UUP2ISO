"""ToolProvider 门禁（引擎 §9.1、进程 §2.1–§2.4）。

验收关注两点：**缺失与降级都能在报告里解释清楚**、必需工具缺失即阻断而不静默降级。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from uup2iso.core.models.enums import EngineKind, ToolKind
from uup2iso.infrastructure.process.backend import (
    FakeBackend,
    RunSpec,
    ScriptedStep,
    SpawnedProcess,
)
from uup2iso.infrastructure.process.errors import ToolMissingError
from uup2iso.infrastructure.process.runner import ProcessRunner
from uup2iso.infrastructure.tools.lock import LockedTool, ToolLock
from uup2iso.infrastructure.tools.provider import ToolProvider, describe_missing
from uup2iso.infrastructure.tools.route import Operation, select_route

VERSION_OUTPUTS = {
    "aria2c.exe": "aria2 version 1.37.0\nCopyright (C) 2006, 2019 Tatsuhiro Tsujikawa",
    "7z.exe": "7-Zip 24.09 (x64)\nCopyright (c) 1999-2024 Igor Pavlov",
    "wimlib-imagex.exe": "wimlib-imagex 1.14.4\nCopyright 2012-2024 Eric Biggers",
    "dism.exe": "Deployment Image Servicing and Management tool\nVersion: 10.0.26100.1",
}

ALL_TOOLS = {
    "aria2c.exe": "",
    "7z.exe": "",
    "wimlib-imagex.exe": "",
    "Dism.exe": "",
    "oscdimg.exe": "",
}


class _VersionBackend(FakeBackend):
    """按可执行文件名返回对应版本输出（夹具回放）。"""

    def __init__(self, outputs: dict[str, str]) -> None:
        super().__init__()
        self._outputs = outputs
        self.invocations: list[str] = []

    async def spawn(self, spec: RunSpec) -> SpawnedProcess:
        name = Path(spec.args[0]).name.lower()
        self.invocations.append(name)
        text = self._outputs.get(name, "")
        inner = FakeBackend(
            [ScriptedStep("stdout", (text + "\n").encode("utf-8"))] if text else [],
            exit_code=0,
        )
        return await inner.spawn(spec)


async def _no_sleep(_seconds: float) -> None:
    return None


def build_provider(
    tmp_path: Path,
    *,
    files: dict[str, str] | None = None,
    outputs: dict[str, str] | None = None,
    lock: ToolLock | None = None,
    settings: dict[str, str] | None = None,
    admin: bool = True,
) -> ToolProvider:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    for name in files if files is not None else ALL_TOOLS:
        (tools_dir / name).write_text("stub", encoding="utf-8")

    backend = _VersionBackend(outputs if outputs is not None else VERSION_OUTPUTS)
    runner = ProcessRunner(backend=backend, sleep=_no_sleep)
    return ToolProvider(
        tools_dir=tools_dir,
        settings=settings,
        lock=lock if lock is not None else ToolLock(),
        runner=runner,
        cache_path=tmp_path / "config" / "tool_caps.json",
        search_path=False,
        admin=admin,
    )


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


# --- 定位 -------------------------------------------------------------------


def test_locate_prefers_tools_dir(tmp_path: Path) -> None:
    provider = build_provider(tmp_path)
    info = provider.locate(ToolKind.ARIA2C)

    assert info is not None
    assert info.source.value == "packaged"
    assert info.path.name == "aria2c.exe"
    assert len(info.sha256 or "") == 64


def test_locate_uses_configured_path(tmp_path: Path) -> None:
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "aria2c.exe").write_text("stub", encoding="utf-8")
    provider = build_provider(
        tmp_path, files={}, settings={"tools.aria2c": str(custom / "aria2c.exe")}
    )

    info = provider.locate(ToolKind.ARIA2C)
    assert info is not None
    assert info.source.value == "configured"


def test_locate_missing_returns_none(tmp_path: Path) -> None:
    provider = build_provider(tmp_path, files={})
    assert provider.locate(ToolKind.WIMLIB) is None
    assert provider.is_available(ToolKind.WIMLIB) is False


def test_hash_mismatch_rejects_tool(tmp_path: Path) -> None:
    """清单登记了哈希就只认这个哈希；不符即拒绝使用（引擎 §9.1）。"""
    lock = ToolLock(tools=[LockedTool(name="aria2c", sha256="0" * 64, version="1.37.0")])
    provider = build_provider(tmp_path, lock=lock)

    assert provider.locate(ToolKind.ARIA2C) is None
    report = run(provider.probe_all([ToolKind.ARIA2C]))
    check = report.check_for(ToolKind.ARIA2C)
    assert check is not None
    assert check.problem == "hash_mismatch"
    assert report.blocked is True


def test_converter_is_a_directory_with_version_file(tmp_path: Path) -> None:
    provider = build_provider(tmp_path)
    converter = tmp_path / "tools" / "uup-converter-wimlib"
    converter.mkdir()
    (converter / "version.txt").write_text("1.2.3\n", encoding="utf-8")

    info = provider.locate(ToolKind.CONVERTER)
    assert info is not None
    assert info.path == converter
    assert info.version == "1.2.3"


# --- 版本探测与能力 ---------------------------------------------------------


def test_probe_all_parses_versions(tmp_path: Path) -> None:
    provider = build_provider(tmp_path)
    report = run(provider.probe_all())

    versions = {check.kind: check.version for check in report.checks}
    assert versions[ToolKind.ARIA2C] == "1.37.0"
    assert versions[ToolKind.SEVEN_ZIP] == "24.09"
    assert versions[ToolKind.WIMLIB] == "1.14.4"
    assert versions[ToolKind.DISM] == "10.0.26100.1"

    # 这四个工具都能定位且无问题（转换器与 reg 未放夹具，属于「缺失」而非「出错」）
    for kind in (ToolKind.ARIA2C, ToolKind.SEVEN_ZIP, ToolKind.WIMLIB, ToolKind.DISM):
        check = report.check_for(kind)
        assert check is not None
        assert check.ok


def test_report_lines_are_log_ready(tmp_path: Path) -> None:
    provider = build_provider(tmp_path)
    report = run(provider.probe_all())

    lines = report.log_lines()
    assert any(line.startswith("event=tool_check tool=aria2c") for line in lines)
    assert any("version=1.37.0" in line for line in lines)
    assert any("managed=no" in line for line in lines), "空清单下工具应标记 unmanaged"


def test_capabilities_follow_probed_version(tmp_path: Path) -> None:
    provider = build_provider(tmp_path)
    before = provider.capabilities(EngineKind.WIMLIB)
    assert before.is_conservative, "未探测前必须是保守值"

    run(provider.probe_all())
    after = provider.capabilities(EngineKind.WIMLIB)
    assert after.version_known
    assert after.can_write_esd
    assert after.supports_ref_export


def test_unparseable_version_marks_degraded(tmp_path: Path) -> None:
    """版本探测失败 → 记 capability_degraded，能力退回保守。"""
    outputs = dict(VERSION_OUTPUTS)
    outputs["wimlib-imagex.exe"] = "unexpected output without version"
    provider = build_provider(tmp_path, outputs=outputs)

    report = run(provider.probe_all([ToolKind.WIMLIB]))
    assert any(item.capability == "version" for item in report.degradations)
    assert provider.capabilities(EngineKind.WIMLIB).is_conservative


def test_old_wimlib_reports_esd_and_ref_degraded(tmp_path: Path) -> None:
    outputs = dict(VERSION_OUTPUTS)
    outputs["wimlib-imagex.exe"] = "wimlib-imagex 1.11.0"
    provider = build_provider(tmp_path, outputs=outputs)

    report = run(provider.probe_all([ToolKind.WIMLIB]))
    capabilities = {item.capability for item in report.degradations}
    assert {"esd_output", "ref_export"} <= capabilities


# --- 缺失与阻断 -------------------------------------------------------------


def test_missing_required_blocks(tmp_path: Path) -> None:
    provider = build_provider(tmp_path, files={"Dism.exe": ""})
    report = run(provider.probe_all())

    assert report.blocked is True
    assert ToolKind.ARIA2C in report.missing_required
    assert ToolKind.WIMLIB in report.missing_required


def test_missing_optional_tool_does_not_block(tmp_path: Path) -> None:
    """oscdimg 缺失只影响 Output 阶段，不阻断启动（引擎 §3.2 R10）。"""
    files = {name: data for name, data in ALL_TOOLS.items() if name != "oscdimg.exe"}
    provider = build_provider(tmp_path, files=files)
    report = run(provider.probe_all())

    assert report.blocked is False
    assert ToolKind.OSCDIMG not in report.missing_required
    assert "ADK" in describe_missing(ToolKind.OSCDIMG)


def test_ensure_raises_tool_missing_with_guidance(tmp_path: Path) -> None:
    provider = build_provider(tmp_path, files={})

    with pytest.raises(ToolMissingError) as caught:
        run(provider.ensure(ToolKind.ARIA2C))

    error = caught.value
    assert error.code.value == "TOOL_MISSING"
    assert "tools\\" in error.message or "tools" in error.message


def test_ensure_raises_adk_hint_for_oscdimg(tmp_path: Path) -> None:
    provider = build_provider(tmp_path)
    (tmp_path / "tools" / "oscdimg.exe").unlink()

    with pytest.raises(ToolMissingError) as caught:
        run(provider.ensure(ToolKind.OSCDIMG))
    assert "ADK" in caught.value.message


def test_ensure_returns_version(tmp_path: Path) -> None:
    provider = build_provider(tmp_path)
    info = run(provider.ensure(ToolKind.WIMLIB))
    assert info.version == "1.14.4"


# --- 缓存与路由 -------------------------------------------------------------


def test_capability_cache_is_written(tmp_path: Path) -> None:
    provider = build_provider(tmp_path)
    run(provider.probe_all([ToolKind.ARIA2C]))

    cache = tmp_path / "config" / "tool_caps.json"
    assert cache.is_file()
    assert "1.37.0" in cache.read_text(encoding="utf-8")


def test_route_degrades_to_dism_when_wimlib_missing(tmp_path: Path) -> None:
    """只有 DISM 时：读信息降级到 DISM，并带上降级原因。"""
    provider = build_provider(tmp_path, files={"Dism.exe": ""}, outputs=VERSION_OUTPUTS)
    decision = select_route(Operation.READ_INFO, provider.is_available)

    assert decision.tool is ToolKind.DISM
    assert decision.degraded is True
    assert "降级" in (decision.reason or "")


def test_admin_flag_is_injectable(tmp_path: Path) -> None:
    assert build_provider(tmp_path, admin=True).is_admin() is True
    assert build_provider(tmp_path, admin=False).is_admin() is False
