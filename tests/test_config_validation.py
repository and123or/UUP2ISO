"""三层校验与迁移门禁（配置 §7、§8.1、§9）。

这一组是 T6 的核心验收：V0/V1/V2 各自的错误码、未知字段的松/严两种模式、
迁移的备份与幂等、以及指纹是否真的排除了路径。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uup2iso.core.errors import (
    ConfigError,
    ConfigMigrationError,
    ConfigParseError,
    ConfigSchemaError,
    ConfigVersionError,
    ErrorCode,
    ExitCode,
)
from uup2iso.infrastructure.storage import config_loader
from uup2iso.infrastructure.storage.config_loader import (
    dump_task_config,
    load_task_config,
    save_task_config,
)


def payload(**overrides: object) -> dict[str, object]:
    """一份入口 A 的合法配置。"""
    base: dict[str, object] = {
        "schemaVersion": "1.0",
        "meta": {"id": "abcd1234", "name": "常规任务"},
        "input": {"mode": "online", "startStage": "Convert"},
        "source": {
            "baseUrl": "https://uupdump.net/",
            "build": "26100.6508",
            "revision": "6508",
            "language": "zh-cn",
            "edition": "professional",
            "channel": "Retail",
        },
        "output": {"type": "iso"},
    }
    base.update(overrides)
    return base


def write(tmp_path: Path, data: dict[str, object] | str | bytes, name: str = "task.json") -> Path:
    path = tmp_path / name
    if isinstance(data, bytes):
        path.write_bytes(data)
    elif isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --- 正常路径 ---------------------------------------------------------------


def test_load_valid_config(tmp_path: Path) -> None:
    path = write(tmp_path, payload())
    loaded = load_task_config(path)

    assert loaded.config.input.mode.value == "online"
    assert loaded.unknown_fields == ()
    assert loaded.warnings == ()
    assert loaded.migrated_from is None
    assert loaded.fingerprint == loaded.config.fingerprint()


# --- V0 语法 ----------------------------------------------------------------


def test_v0_rejects_bom(tmp_path: Path) -> None:
    """配置 §8.1 V0 明确要求「UTF-8、无 BOM」。"""
    path = write(tmp_path, b"\xef\xbb\xbf" + json.dumps(payload()).encode("utf-8"))
    with pytest.raises(ConfigParseError) as caught:
        load_task_config(path)
    assert caught.value.code is ErrorCode.CONFIG_PARSE_ERROR
    assert caught.value.exit_code is ExitCode.INVALID_ARGUMENT


def test_v0_rejects_broken_json(tmp_path: Path) -> None:
    path = write(tmp_path, '{"schemaVersion": "1.0", ')
    with pytest.raises(ConfigParseError):
        load_task_config(path)


def test_v0_rejects_non_utf8(tmp_path: Path) -> None:
    path = write(tmp_path, '{"name": "中文"}'.encode("gbk"))
    with pytest.raises(ConfigParseError) as caught:
        load_task_config(path)
    assert "UTF-8" in caught.value.message


# --- V1 Schema --------------------------------------------------------------


def test_v1_missing_required_section(tmp_path: Path) -> None:
    data = payload()
    data.pop("output")
    path = write(tmp_path, data)
    with pytest.raises(ConfigSchemaError) as caught:
        load_task_config(path)
    assert caught.value.code is ErrorCode.CONFIG_SCHEMA_INVALID
    assert "output" in caught.value.message


def test_v1_bad_enum_value(tmp_path: Path) -> None:
    path = write(tmp_path, payload(output={"type": "zip"}))
    with pytest.raises(ConfigSchemaError):
        load_task_config(path)


def test_v1_bad_type(tmp_path: Path) -> None:
    path = write(tmp_path, payload(output={"verifyLevel": "high"}))
    with pytest.raises(ConfigSchemaError):
        load_task_config(path)


# --- 版本 ---------------------------------------------------------------


def test_unsupported_major_version(tmp_path: Path) -> None:
    """§7：主版本不匹配直接拒绝，不进入未知字段逻辑。"""
    path = write(tmp_path, payload(schemaVersion="2.0"))
    with pytest.raises(ConfigVersionError) as caught:
        load_task_config(path)
    assert caught.value.code is ErrorCode.CONFIG_VERSION_UNSUPPORTED


def test_bad_version_format(tmp_path: Path) -> None:
    path = write(tmp_path, payload(schemaVersion="1.0.0"))
    with pytest.raises(ConfigSchemaError):
        load_task_config(path)


# --- V2 语义 ---------------------------------------------------------------


def test_v2_online_requires_source(tmp_path: Path) -> None:
    data = payload()
    data.pop("source")
    path = write(tmp_path, data)
    with pytest.raises(ConfigError) as caught:
        load_task_config(path)
    assert caught.value.code is ErrorCode.CONFIG_SEMANTIC_INVALID
    assert "source" in caught.value.message


def test_v2_image_requires_editions(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        payload(
            input={
                "mode": "image",
                "startStage": "Slim",
                "image": {"path": "D:/a.iso", "type": "iso"},
            }
        ),
    )
    with pytest.raises(ConfigError) as caught:
        load_task_config(path)
    assert "editions" in caught.value.message


def test_v2_edition_index_must_exist_in_probe(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        payload(
            input={
                "mode": "image",
                "startStage": "Slim",
                "image": {"path": "D:/a.iso", "type": "iso", "editions": [1, 7]},
            }
        ),
    )
    with pytest.raises(ConfigError) as caught:
        load_task_config(path, known_edition_indexes={1, 2, 3})
    assert "7" in caught.value.message


def test_v2_start_stage_too_early(tmp_path: Path) -> None:
    """输入 §6：UUP 文件夹最早从 Convert 开始，选 Prepare 应被拒。"""
    path = write(
        tmp_path,
        payload(
            input={"mode": "uupFolder", "startStage": "Prepare", "uupFolder": {"path": "D:/uup"}}
        ),
    )
    with pytest.raises(ConfigError) as caught:
        load_task_config(path)
    assert "最早为 Convert" in caught.value.message


def test_v2_virtual_edition_disabled_in_mvp(tmp_path: Path) -> None:
    path = write(tmp_path, payload(convert={"virtualEdition": {"enabled": True}}))
    with pytest.raises(ConfigError) as caught:
        load_task_config(path)
    assert "虚拟版本" in caught.value.message


def test_v2_reset_base_requires_component_cleanup(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        payload(convert={"cleanup": {"componentCleanup": False, "resetBase": True}}),
    )
    with pytest.raises(ConfigError) as caught:
        load_task_config(path)
    assert "resetBase" in caught.value.message


def test_v2_swm_output_rejected_with_its_own_code(tmp_path: Path) -> None:
    """SWM 是「能力未验证」而非「配置写错」，错误码要区分开。"""
    path = write(tmp_path, payload(output={"type": "swm"}))
    with pytest.raises(ConfigError) as caught:
        load_task_config(path)
    assert caught.value.code is ErrorCode.OUTPUT_TYPE_UNSUPPORTED


def test_v2_conservative_mode_blocks_lzms(tmp_path: Path) -> None:
    path = write(tmp_path, payload(convert={"compression": "LZMS"}))
    # 非保守模式下允许
    assert load_task_config(path).config.convert is not None
    with pytest.raises(ConfigError):
        load_task_config(path, conservative_mode=True)


@pytest.mark.parametrize(
    ("section", "value", "field"),
    [
        ({"fetch": {"split": 32}}, 32, "fetch.split"),
        ({"fetch": {"timeoutSeconds": 5}}, 5, "fetch.timeoutSeconds"),
        ({"convert": {"apps": {"level": 9}}}, 9, "convert.apps.level"),
        ({"execution": {"hangWarnMinutes": 0}}, 0, "execution.hangWarnMinutes"),
        ({"output": {"verifyLevel": 4}}, 4, "output.verifyLevel"),
    ],
)
def test_v2_ranges_not_clamped(
    tmp_path: Path, section: dict[str, object], value: int, field: str
) -> None:
    """配置 §5：超范围即报错，不做静默夹紧。"""
    path = write(tmp_path, payload(**section))
    with pytest.raises(ConfigError) as caught:
        load_task_config(path)
    assert field in caught.value.message


# --- 未知字段（§7） ---------------------------------------------------------


def test_unknown_fields_ignored_by_default(tmp_path: Path) -> None:
    path = write(tmp_path, payload(futureSection={"a": 1}, output={"newKnob": True}))
    loaded = load_task_config(path)

    assert "futureSection" in loaded.unknown_fields
    assert "output.newKnob" in loaded.unknown_fields
    assert any(item.startswith("config_unknown_fields") for item in loaded.warnings)


def test_strict_unknown_fields_via_argument(tmp_path: Path) -> None:
    path = write(tmp_path, payload(futureSection={"a": 1}))
    with pytest.raises(ConfigSchemaError) as caught:
        load_task_config(path, strict_unknown=True)
    assert "未知字段" in caught.value.message


def test_strict_unknown_fields_via_task_config(tmp_path: Path) -> None:
    """配置 §3.5：严格开关的唯一归属是 TaskConfig。"""
    path = write(
        tmp_path,
        payload(
            execution={"validation": {"strictUnknownFields": True}},
            futureSection={"a": 1},
        ),
    )
    with pytest.raises(ConfigSchemaError):
        load_task_config(path)


def test_argument_overrides_task_config(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        payload(execution={"validation": {"strictUnknownFields": True}}, futureSection={"a": 1}),
    )
    loaded = load_task_config(path, strict_unknown=False)
    assert "futureSection" in loaded.unknown_fields


# --- 迁移（§9） -------------------------------------------------------------


def test_migration_creates_backup_and_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """次版本落后 → 迁移 → 备份 ``.bak`` → 写回新版本。"""
    monkeypatch.setattr(config_loader, "CURRENT_VERSION", (1, 1))

    def migrate(data: dict[str, object]) -> dict[str, object]:
        migrated = dict(data)
        migrated.setdefault("fetch", {"split": 4})
        return migrated

    monkeypatch.setitem(config_loader.MIGRATORS, (1, 0), migrate)

    path = write(tmp_path, payload())
    loaded = load_task_config(path)

    assert loaded.migrated_from == "1.0"
    assert loaded.config.fetch is not None
    assert loaded.config.fetch.split == 4
    assert path.with_suffix(".bak").is_file(), "迁移前必须备份原文件"
    assert json.loads(path.read_text(encoding="utf-8"))["schemaVersion"] == "1.1"


def test_migration_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """重复加载不得产生二次变化（配置 §9 的硬要求）。"""
    monkeypatch.setattr(config_loader, "CURRENT_VERSION", (1, 1))
    monkeypatch.setitem(
        config_loader.MIGRATORS, (1, 0), lambda data: {**data, "fetch": {"split": 4}}
    )

    path = write(tmp_path, payload())
    first = load_task_config(path)
    second = load_task_config(path)

    assert first.config.fingerprint() == second.config.fingerprint()
    assert second.migrated_from is None, "第二次不应再触发迁移"


def test_missing_migrator_rejected_and_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_loader, "CURRENT_VERSION", (1, 2))
    monkeypatch.delitem(config_loader.MIGRATORS, (1, 0), raising=False)

    path = write(tmp_path, payload())
    original = path.read_bytes()

    with pytest.raises(ConfigMigrationError) as caught:
        load_task_config(path)

    assert caught.value.code is ErrorCode.CONFIG_MIGRATION_FAILED
    assert path.read_bytes() == original, "迁移失败绝不能改写原文件"


# --- 保存 -------------------------------------------------------------------


def test_save_is_atomic_and_reloadable(tmp_path: Path) -> None:
    path = tmp_path / "tasks" / "abcd1234.json"
    loaded = load_task_config(write(tmp_path, payload()))
    save_task_config(path, loaded.config)

    assert path.is_file()
    assert not (path.parent / "abcd1234.json.tmp").exists(), "临时文件必须被替换掉"
    assert load_task_config(path).fingerprint == loaded.fingerprint
    assert dump_task_config(loaded.config).startswith("{")
