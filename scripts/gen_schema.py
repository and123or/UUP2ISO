"""生成权威 Schema：`src/uup2iso/schemas/taskconfig.schema.json`（配置 §8.1 V1）。

用法（仓库根目录、`.venv` 内）：

    .venv\\Scripts\\python scripts/gen_schema.py           # 生成/覆盖
    .venv\\Scripts\\python scripts/gen_schema.py --check   # 只校验是否漂移（CI 用）

Schema 由 `TaskConfig` 模型派生，所以**模型是唯一来源**：改了模型却忘了重新生成，
`--check` 会直接失败（`tests/test_config_schema.py` 也守这条）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "src" / "uup2iso" / "schemas" / "taskconfig.schema.json"

DRAFT_07 = "http://json-schema.org/draft-07/schema#"


def build_schema() -> dict[str, Any]:
    """从 :class:`TaskConfig` 派生 JSON Schema（draft-07 兼容）。"""
    from uup2iso.core.models.config import TaskConfig

    schema: dict[str, Any] = TaskConfig.model_json_schema(by_alias=True)
    schema["$schema"] = DRAFT_07
    schema["title"] = "UUP2ISO TaskConfig"
    schema["description"] = (
        "任务配置权威 Schema（配置规范 §3）。由 src/uup2iso/core/models/config.py 生成，请勿手改。"
    )
    return schema


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 TaskConfig 权威 Schema")
    parser.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = parser.parse_args(argv)

    rendered = render(build_schema())

    if args.check:
        if not SCHEMA_PATH.exists():
            print(f"[FAIL] Schema 不存在：{SCHEMA_PATH}")
            return 1
        current = SCHEMA_PATH.read_text(encoding="utf-8")
        if current != rendered:
            print("[FAIL] Schema 与模型不一致，请运行 python scripts/gen_schema.py 重新生成")
            return 1
        print("[OK] Schema 与模型一致")
        return 0

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"[OK] 已写入 {SCHEMA_PATH.relative_to(ROOT)}（{len(rendered)} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
