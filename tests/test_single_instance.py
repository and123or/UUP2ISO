"""单实例守卫门禁（日志 §6.1、FR9.4）。

用唯一互斥量名，避免测试与真实运行中的程序互相干扰。
"""

from __future__ import annotations

import uuid

from uup2iso.infrastructure.single_instance import DEFAULT_MUTEX_NAME, SingleInstanceGuard


def unique_name() -> str:
    return rf"Local\UUP2ISO.Test.{uuid.uuid4().hex}"


def test_default_name_matches_spec() -> None:
    assert DEFAULT_MUTEX_NAME == r"Local\UUP2ISO.SingleInstance"


def test_first_acquire_wins_second_fails() -> None:
    name = unique_name()
    first = SingleInstanceGuard(name)
    second = SingleInstanceGuard(name)

    try:
        assert first.acquire() is True
        assert second.acquire() is False
        assert first.held is True
        assert second.held is False
    finally:
        first.release()
        second.release()


def test_acquire_is_idempotent_for_same_guard() -> None:
    guard = SingleInstanceGuard(unique_name())
    try:
        assert guard.acquire() is True
        assert guard.acquire() is True, "同一对象重复 acquire 应保持持有"
    finally:
        guard.release()


def test_release_allows_next_instance() -> None:
    name = unique_name()
    first = SingleInstanceGuard(name)
    assert first.acquire()
    first.release()

    second = SingleInstanceGuard(name)
    try:
        assert second.acquire() is True
    finally:
        second.release()


def test_different_names_are_independent() -> None:
    left = SingleInstanceGuard(unique_name())
    right = SingleInstanceGuard(unique_name())
    try:
        assert left.acquire() is True
        assert right.acquire() is True, "不同互斥量名互不影响"
    finally:
        left.release()
        right.release()


def test_context_manager_releases() -> None:
    name = unique_name()
    with SingleInstanceGuard(name) as guard:
        assert guard.held
    assert not guard.held

    other = SingleInstanceGuard(name)
    try:
        assert other.acquire() is True
    finally:
        other.release()


def test_already_running_flag() -> None:
    name = unique_name()
    holder = SingleInstanceGuard(name)
    assert holder.acquire()

    blocked = SingleInstanceGuard(name)
    try:
        assert blocked.acquire() is False
        assert blocked.already_running is True
        assert blocked.held is False
    finally:
        holder.release()
        blocked.release()


def test_release_is_safe_without_acquire() -> None:
    SingleInstanceGuard(unique_name()).release()  # 不应抛错
