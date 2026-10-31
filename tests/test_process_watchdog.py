"""四信号看门狗门禁（进程 §7.2）。

判定的要点：**四信号同时无活动**才告警，任一有活动即重置；采样失败按「有活动」处理
（宁可漏报也不误杀）；窗口数由参数算出而不是硬编码。
"""

from __future__ import annotations

import pytest

from uup2iso.infrastructure.process.watchdog import (
    HangEvidence,
    WatchdogConfig,
    WatchdogState,
)

_IDLE_IO = (100, 100)


def test_window_count_is_derived_from_parameters() -> None:
    """10 分钟 / 30 秒 = 20 个窗口；改参数窗口数随之变化。"""
    assert WatchdogConfig().window_count() == 20
    assert WatchdogConfig(hang_warn_minutes=1, hang_sample_seconds=30).window_count() == 2
    assert WatchdogConfig(hang_warn_minutes=10, hang_sample_seconds=300).window_count() == 2


@pytest.mark.parametrize(
    ("minutes", "seconds"),
    [(0, 30), (121, 30), (10, 5), (10, 301)],
)
def test_config_rejects_out_of_range(minutes: int, seconds: int) -> None:
    """设置页允许区间：1–120 分钟、10–300 秒。"""
    with pytest.raises(ValueError):
        WatchdogConfig(hang_warn_minutes=minutes, hang_sample_seconds=seconds)


def _state(*, has_network: bool = False) -> WatchdogState:
    return WatchdogState(
        config=WatchdogConfig(hang_warn_minutes=1, hang_sample_seconds=30),
        has_network=has_network,
    )


def _sample(
    state: WatchdogState,
    *,
    output: bool = False,
    cpu: float | None = 1.0,
    io: tuple[int, int] | None = _IDLE_IO,
    net: tuple[int, int] | None = None,
) -> HangEvidence | None:
    return state.observe(
        output_since_last=output,
        cpu_seconds=cpu,
        io_bytes=io,
        net_bytes=net,
    )


def _prime(state: WatchdogState, *, net: tuple[int, int] | None = None) -> None:
    """第一次采样只建立基线（增量未知，按有活动处理）。"""
    _sample(state, output=True, net=net)


def test_alarms_after_configured_windows_of_total_silence() -> None:
    """1 分钟 / 30 秒 = 2 个窗口；连续 2 次无活动才告警。"""
    state = _state()
    _prime(state)

    assert _sample(state) is None
    evidence = _sample(state)

    assert isinstance(evidence, HangEvidence)
    assert evidence.cpu_delta == 0.0
    assert evidence.elapsed_seconds == 60


def test_any_output_resets_counter() -> None:
    state = _state()
    _prime(state)

    assert _sample(state) is None
    assert _sample(state, output=True) is None
    assert _sample(state) is None


def test_cpu_activity_resets_counter() -> None:
    state = _state()
    _prime(state)

    assert _sample(state) is None
    assert _sample(state, cpu=5.0) is None


def test_disk_activity_resets_counter() -> None:
    """IO 增长说明在搬数据（DISM/wimlib 的常态）。"""
    state = _state()
    _prime(state)

    assert _sample(state) is None
    busy = (100 + 256 * 1024 * 30, 100)
    assert _sample(state, io=busy) is None


def test_network_signal_ignored_when_tool_has_no_network_profile() -> None:
    """无 network 画像的工具（DISM/wimlib）不校验网络，否则会永远不告警。"""
    state = _state(has_network=False)
    _prime(state, net=(0, 0))

    assert _sample(state, net=(0, 0)) is None
    assert _sample(state, net=(0, 0)) is not None


def test_network_signal_prevents_alarm_when_profile_has_network() -> None:
    """aria2c 有网络画像：系统仍有流量时不能判卡死（保守，宁漏报）。"""
    state = _state(has_network=True)
    _prime(state, net=(0, 0))

    busy = 32 * 1024 * 30
    assert _sample(state, net=(busy, 0)) is None
    assert _sample(state, net=(busy, 0)) is None


def test_sampling_failure_is_treated_as_activity() -> None:
    """API 取不到数据（None）视为仍在工作——绝不当成卡死。"""
    state = _state()
    _prime(state)

    assert _sample(state, cpu=None) is None
    assert _sample(state, io=None) is None


def test_counter_wraparound_does_not_count_as_activity() -> None:
    """32 位网络计数器回绕时，负增量按 0 处理（不误判为有流量）。"""
    state = _state(has_network=True)
    _sample(state, output=True, net=(0xFFFFFF00, 0))

    assert _sample(state, net=(0x00000100, 0)) is None


def test_evidence_text_is_readable() -> None:
    evidence = HangEvidence(
        cpu_delta=0.01,
        io_read=1024 * 1024,
        io_write=0,
        net_rx=0,
        net_tx=0,
        elapsed_seconds=600,
    )
    text = evidence.as_text()
    assert "cpu_delta=0.01s" in text
    assert "io_read=1.00MB" in text
    assert "elapsed=600s" in text
