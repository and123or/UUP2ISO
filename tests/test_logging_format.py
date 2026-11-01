"""行格式、脱敏与级别判定门禁（日志 §2.3、§3、§5.5）。"""

from __future__ import annotations

from datetime import datetime

import pytest

from uup2iso.infrastructure.logging import (
    DEBUG,
    INFO,
    WARN,
    format_line,
    level_rank,
    redact_text,
    redact_url,
    resolve_level,
    single_line,
)

TS = datetime(2026, 10, 21, 22, 6, 0, 123000)


def test_line_shape_matches_spec() -> None:
    """`<ts> [<LEVEL>] [<stage>] <logger>: <msg> | k=v`（日志 §2.3）。"""
    line = format_line(
        timestamp=TS,
        level=INFO,
        stage="Convert",
        logger_name="infra.process",
        message="wimlib export 完成",
        fields={"task": "7f3a", "exit": 0, "duration": "19.4s"},
    )
    assert line.startswith("2026-10-21 22:06:00.123 [INFO ] [Convert] infra.process: ")
    assert line.endswith("| task=7f3a exit=0 duration=19.4s")


def test_level_is_five_characters_wide() -> None:
    for level, expected in ((INFO, "[INFO ]"), (WARN, "[WARN ]")):
        line = format_line(timestamp=TS, level=level, stage=None, logger_name="x", message="m")
        assert expected in line


def test_no_stage_placeholder() -> None:
    line = format_line(timestamp=TS, level=INFO, stage=None, logger_name="x", message="m")
    assert "[-]" in line


def test_message_is_flattened_to_single_line() -> None:
    line = format_line(
        timestamp=TS,
        level=INFO,
        stage="Slim",
        logger_name="x",
        message="第一行\n第二行\r\n第三行\t结束",
    )
    assert "\n" not in line and "\r" not in line
    assert "第一行 第二行 第三行 结束" in line


def test_none_fields_are_skipped() -> None:
    line = format_line(
        timestamp=TS,
        level=INFO,
        stage=None,
        logger_name="x",
        message="m",
        fields={"a": 1, "b": None, "c": ""},
    )
    assert "a=1" in line
    assert "b=" not in line
    assert line.count("|") == 1


def test_boolean_and_float_fields() -> None:
    line = format_line(
        timestamp=TS,
        level=INFO,
        stage=None,
        logger_name="x",
        message="m",
        fields={"ok": True, "ratio": 0.5},
    )
    assert "ok=true" in line
    assert "ratio=0.5" in line


def test_level_rank_orders_correctly() -> None:
    assert level_rank(DEBUG) < level_rank(INFO) < level_rank(WARN)


def test_single_line_helper() -> None:
    assert single_line(" a\n b\tc ") == "a b c"


# --- 脱敏（日志 §5.5） -------------------------------------------------------


def test_proxy_password_is_masked() -> None:
    text = redact_text("download proxy-password: S3cret! user: bob")
    assert "S3cret!" not in text
    assert "***" in text


def test_url_credentials_are_masked() -> None:
    text = redact_text("connect http://user:p@ss@proxy.local:8080/x")
    assert "p@ss" not in text
    assert "user:***@proxy.local" in text


def test_product_key_keeps_last_group_only() -> None:
    text = redact_text("key=ABCDE-12345-FGHIJ-67890-KLMNO")
    assert text.endswith("XXXXX-XXXXX-XXXXX-XXXXX-KLMNO")
    assert "ABCDE-12345" not in text


def test_signed_url_drops_token_params() -> None:
    url = "https://host/path/file.zip?sig=abc&token=xyz&keep=1"
    assert redact_url(url) == "https://host/path/file.zip?keep=1"


def test_redaction_is_idempotent() -> None:
    once = redact_text("password=hunter2 key=ABCDE-12345-FGHIJ-67890-KLMNO")
    twice = redact_text(once)
    assert once == twice


# --- 级别判定（日志 §3） -----------------------------------------------------


@pytest.mark.parametrize(
    ("frozen", "entry", "debug", "expected"),
    [
        (True, "gui", False, INFO),
        (False, "gui", False, DEBUG),
        (True, "cli", False, INFO),
        (False, "cli", False, INFO),
        (True, "gui", True, DEBUG),
        (False, "cli", True, DEBUG),
    ],
)
def test_resolve_level_matrix(frozen: bool, entry: str, debug: bool, expected: str) -> None:
    level, source = resolve_level(frozen=frozen, entry=entry, debug=debug)
    assert level == expected
    assert source, "级别来源必须能显示给用户"
