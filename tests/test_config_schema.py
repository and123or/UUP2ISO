"""权威 Schema 门禁（配置 §2、§8.1 V1）。

Schema 由 `TaskConfig` 派生并提交到仓库，所以这里守两件事：

1. **不漂移**：模型改了却没重新生成 → `scripts/gen_schema.py --check` 直接失败；
2. **可用**：能被 draft-07 校验器加载，且一份完整配置能通过。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema

from uup2iso.infrastructure.storage import default_schema_path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SCRIPT = ROOT / "scripts" / "gen_schema.py"


def load_schema() -> dict[str, object]:
    return json.loads(default_schema_path().read_text(encoding="utf-8"))


def test_schema_is_in_package() -> None:
    path = default_schema_path()
    assert path.is_file(), f"权威 Schema 缺失：{path}"
    assert path.name == "taskconfig.schema.json"


def test_schema_does_not_drift_from_models() -> None:
    """生成器与提交的文件必须一致——这是「模型是唯一来源」的机器保证。"""
    result = subprocess.run(  # noqa: S603 - 固定参数、可执行文件来自 sys.executable
        [sys.executable, str(SCHEMA_SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_schema_is_valid_draft07() -> None:
    schema = load_schema()
    jsonschema.Draft7Validator.check_schema(schema)
    assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"


def test_required_fields_declared() -> None:
    """配置 §3.1 的四个必填项要在 Schema 里体现。"""
    schema = load_schema()
    assert set(schema["required"]) == {"schemaVersion", "meta", "input", "output"}


def test_unknown_fields_allowed_by_schema() -> None:
    """§7：默认允许未知字段（严格模式由加载器判断，不是 Schema 的职责）。"""
    assert load_schema().get("additionalProperties") is True


def test_sample_config_passes_schema() -> None:
    schema = load_schema()
    sample = {
        "schemaVersion": "1.0",
        "meta": {"id": "abcd1234", "name": "样例"},
        "input": {
            "mode": "image",
            "startStage": "Slim",
            "image": {"path": "D:/a.iso", "type": "iso", "editions": [1]},
        },
        "convert": {"mode": "wim_only", "compression": "LZX", "editions": ["professional"]},
        "output": {"type": "iso", "verifyLevel": 2},
        "execution": {"validation": {"strictUnknownFields": False}},
    }
    jsonschema.Draft7Validator(schema).validate(sample)


def test_schema_rejects_unknown_enum_value() -> None:
    schema = load_schema()
    bad = {
        "schemaVersion": "1.0",
        "meta": {},
        "input": {"mode": "online"},
        "output": {"type": "zip"},
    }
    assert list(jsonschema.Draft7Validator(schema).iter_errors(bad))
