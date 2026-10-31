"""全局设置存储门禁（配置 §2；GUI-设置 §2）。

设置层的三条约定：读永不抛错、值统一为字符串、敏感值导出时脱敏。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from uup2iso.infrastructure.storage import DEFAULT_SETTINGS, SettingsStore


def run_save(store: SettingsStore) -> None:
    asyncio.run(store.save())


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    store = SettingsStore.load(tmp_path / "settings.json")
    assert store.get("source.baseUrl") == DEFAULT_SETTINGS["source.baseUrl"]
    assert store.get("download.split") == "8"
    assert store.get("not.exists", "fallback") == "fallback"
    assert store.get("not.exists") is None


def test_set_get_dotted_keys(tmp_path: Path) -> None:
    store = SettingsStore.load(tmp_path / "settings.json")
    store.set("download.proxy", "http://127.0.0.1:7890")
    store.set("process.hang_warn_minutes", "20")

    assert store.get("download.proxy") == "http://127.0.0.1:7890"
    assert store.get("process.hang_warn_minutes") == "20"
    # 未知键也允许（设置比配置宽松，新版本写入的键不该让老版本崩）
    store.set("future.newKnob", "1")
    assert store.get("future.newKnob") == "1"


def test_save_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "config" / "settings.json"
    store = SettingsStore.load(path)
    store.set("output.dir", "D:/out")
    run_save(store)

    assert path.is_file()
    assert not (path.parent / "settings.json.tmp").exists()

    reloaded = SettingsStore.load(path)
    assert reloaded.get("output.dir") == "D:/out"
    assert json.loads(path.read_text(encoding="utf-8"))["output"]["dir"] == "D:/out"


def test_sensitive_values_are_redacted_in_mapping(tmp_path: Path) -> None:
    """日志与诊断包直接用 as_mapping()，凭据不能明文外泄（日志 §5.5）。"""
    store = SettingsStore.load(tmp_path / "settings.json")
    store.set("download.proxyPassword", "s3cret")
    store.set("download.proxy", "http://proxy:8080")

    flat = store.as_mapping()
    assert flat["download.proxyPassword"] == "***"
    assert flat["download.proxy"] == "http://proxy:8080"


def test_raw_dict_keeps_real_values(tmp_path: Path) -> None:
    store = SettingsStore.load(tmp_path / "settings.json")
    store.set("download.proxyPassword", "s3cret")
    assert store.to_dict()["download"]["proxyPassword"] == "s3cret"


def test_corrupt_settings_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    from uup2iso.core.errors import ConfigParseError

    try:
        SettingsStore.load(path)
    except ConfigParseError as exc:
        assert "设置文件" in exc.message
    else:  # pragma: no cover - 明确失败更好
        raise AssertionError("损坏的设置文件应当报错")
