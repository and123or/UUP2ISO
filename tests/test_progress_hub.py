"""进度通道门禁（进程 §6、日志 §5.2）。

两条规则：**单调不减**（工具重绘不能让进度倒退）、**节流**（对 UI 每秒最多 5 次），
外加一条铁律：进度不得写入日志。
"""

from __future__ import annotations

import pytest

from uup2iso.infrastructure.progress import ProgressHub


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_hub(*, interval: float = 0.2) -> tuple[ProgressHub, Clock]:
    clock = Clock()
    return ProgressHub(min_interval=interval, clock=clock), clock


def test_monotonic_regression_is_dropped() -> None:
    hub, clock = make_hub()

    assert hub.report("Convert", substep="export", percent=40)
    clock.advance(1)
    assert hub.report("Convert", substep="export", percent=50)
    clock.advance(1)
    assert hub.report("Convert", substep="export", percent=30) is False, "回退必须被吞掉"

    latest = hub.snapshot()[0]
    assert latest.percent == 50


def test_throttle_limits_ui_updates() -> None:
    hub, clock = make_hub(interval=0.2)

    assert hub.report("Convert", substep="export", percent=10)
    assert hub.report("Convert", substep="export", percent=11) is False, "200ms 内应节流"
    clock.advance(0.25)
    assert hub.report("Convert", substep="export", percent=12)


def test_indeterminate_percent_is_throttled_but_allowed() -> None:
    hub, clock = make_hub()
    assert hub.report("Fetch", substep="download", percent=None)
    assert hub.report("Fetch", substep="download", percent=None) is False
    clock.advance(0.5)
    assert hub.report("Fetch", substep="download", percent=None)


def test_different_substeps_are_independent() -> None:
    hub, _clock = make_hub()
    assert hub.report("Slim", substep="mount", percent=10)
    assert hub.report("Slim", substep="apply", percent=10), "不同子步骤各自计时"


def test_subscribers_receive_updates_and_can_unsubscribe() -> None:
    hub, clock = make_hub()
    seen: list[float | None] = []
    unsubscribe = hub.subscribe(lambda update: seen.append(update.percent))

    hub.report("Output", percent=10)
    clock.advance(1)
    hub.report("Output", percent=20)
    unsubscribe()
    clock.advance(1)
    hub.report("Output", percent=30)

    assert seen == [10, 20]


def test_snapshot_keeps_latest_per_key() -> None:
    hub, clock = make_hub()
    hub.report("Convert", substep="export", percent=10)
    clock.advance(1)
    hub.report("Convert", substep="export", percent=60)

    snapshot = hub.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0].percent == 60


def test_reset_clears_monotonic_baseline() -> None:
    """阶段切换时若不重置，上一阶段的 100% 会压住新阶段的进度。"""
    hub, clock = make_hub()
    hub.report("Convert", percent=100)
    clock.advance(1)

    hub.reset("Slim")
    assert hub.report("Slim", percent=1)


def test_reset_all() -> None:
    hub, clock = make_hub()
    hub.report("Convert", percent=90)
    clock.advance(1)
    hub.reset()
    assert hub.report("Convert", percent=5)


def test_progress_must_not_be_wired_to_log_sink() -> None:
    """给个明确的失败点：谁想把进度接到日志，会立刻被拦下（日志 §5.2）。"""
    hub, _clock = make_hub()
    with pytest.raises(RuntimeError) as caught:
        hub.attach_log(object())
    assert "不得写入日志" in str(caught.value)
