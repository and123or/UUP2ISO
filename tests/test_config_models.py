"""TaskConfig 模型门禁（配置 §3、§5、§7、§8.2）。

覆盖：必填项、默认值逐条对照 §5、指纹排除项（路径/meta/代理）、未知字段收集。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from uup2iso.core.models import (
    TaskConfig,
    default_task_config,
    iter_unknown_fields,
)
from uup2iso.core.models.enums import CompressionKind, EntryPoint, OutputType
from uup2iso.infrastructure.storage import dump_task_config


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": "1.0",
        "meta": {"id": "abcd1234", "name": "demo"},
        "input": {"mode": "uupFolder", "startStage": "Convert", "uupFolder": {"path": "D:/uup"}},
        "output": {},
    }
    payload.update(overrides)
    return payload


# --- 必填与默认值 -----------------------------------------------------------


def test_required_top_level_fields() -> None:
    """配置 §3.1：schemaVersion / meta / input / output 必填。"""
    for field in ("schemaVersion", "meta", "input", "output"):
        payload = _payload()
        payload.pop(field)
        with pytest.raises(ValidationError):
            TaskConfig.model_validate(payload)


def test_defaults_match_spec_section_5() -> None:
    """配置 §5 的默认值一条条对照，防止实现漂移。"""
    config = TaskConfig.model_validate(_payload(fetch={}, convert={}))

    assert config.output.type is OutputType.ISO
    assert config.output.collision == "suffix"
    assert config.output.verify_level == 2
    assert config.output.name_template == "{edition}_{build}_{arch}_{lang}"

    assert config.fetch is not None
    assert config.fetch.split == 8
    assert config.fetch.timeout_seconds == 30

    assert config.convert is not None
    assert config.convert.mode == "wim_only"
    assert config.convert.compression is CompressionKind.LZX
    assert config.convert.updates.mode == "in_image"
    assert config.convert.drivers.scope == "all"
    assert config.convert.apps.level == 0

    assert config.execution is None  # 未给出时不塞默认值
    plain = TaskConfig.model_validate(_payload())
    assert plain.input.start_stage == "Convert"


def test_execution_defaults_when_present() -> None:
    config = TaskConfig.model_validate(_payload(execution={}))
    assert config.execution is not None
    assert config.execution.resume is True
    assert config.execution.hang_warn_minutes == 10
    assert config.execution.dry_run is False
    assert config.execution.validation.strict_unknown_fields is False


def test_unknown_fields_are_kept_not_dropped() -> None:
    """§7 默认忽略：模型仍保留它们，供上层收集与提示。"""
    config = TaskConfig.model_validate(_payload(futureSection={"x": 1}, output={"newKnob": True}))
    unknown = iter_unknown_fields(config)
    assert "futureSection" in unknown
    assert "output.newKnob" in unknown


def test_unknown_fields_nested_paths() -> None:
    config = TaskConfig.model_validate(_payload(convert={"apps": {"level": 1, "bonus": 2}}))
    assert "convert.apps.bonus" in iter_unknown_fields(config)


# --- 指纹（配置 §8.2） ------------------------------------------------------


def test_fingerprint_ignores_paths_meta_and_proxy() -> None:
    """同一套业务选项、不同机器路径 → 指纹必须相同。"""
    left = TaskConfig.model_validate(
        _payload(
            input={
                "mode": "image",
                "startStage": "Slim",
                "image": {"path": "D:/a.iso", "editions": [1]},
            },
            fetch={"proxy": "http://user:pass@proxy-a:8080"},
            output={"dir": "D:/out1"},
            meta={"id": "aaaa1111", "name": "任务一"},
        )
    )
    right = TaskConfig.model_validate(
        _payload(
            input={
                "mode": "image",
                "startStage": "Slim",
                "image": {"path": "E:/b.iso", "editions": [1]},
            },
            fetch={"proxy": "http://proxy-b:8080"},
            output={"dir": "E:/out2"},
            meta={"id": "bbbb2222", "name": "任务二"},
        )
    )
    assert left.fingerprint() == right.fingerprint()


def test_fingerprint_changes_with_business_options() -> None:
    left = TaskConfig.model_validate(_payload())
    right = TaskConfig.model_validate(_payload(output={"type": "wim"}))
    assert left.fingerprint() != right.fingerprint()


def test_fingerprint_is_stable_and_hex() -> None:
    config = TaskConfig.model_validate(_payload())
    first = config.fingerprint()
    assert first == config.fingerprint()
    assert len(first) == 64
    int(first, 16)  # 必须是合法十六进制


# --- 序列化 -----------------------------------------------------------------


def test_dump_roundtrip_is_stable(tmp_path: Path) -> None:
    config = default_task_config(mode=EntryPoint.IMAGE, name="回环")
    config.input.image = None
    text = dump_task_config(config)
    reloaded = TaskConfig.model_validate_json(text)
    assert reloaded.fingerprint() == config.fingerprint()
    assert reloaded.meta.name == "回环"


def test_aliases_are_used_in_json() -> None:
    """落盘用配置规范的驼峰字段名（`startStage` 而不是 `start_stage`）。"""
    text = dump_task_config(default_task_config())
    assert '"startStage"' in text
    assert '"schemaVersion"' in text
    assert '"nameTemplate"' in text
