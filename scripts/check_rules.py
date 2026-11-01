"""规则库校验脚本（规则 §8；后续作为 WP10-T4 的 CI 门禁用）。

用法（仓库根目录、`.venv` 内）：

    .venv\\Scripts\\python scripts/check_rules.py
    .venv\\Scripts\\python scripts/check_rules.py --rules-dir path/to/rules

退出码：0 全部通过；2 规则库非法（错误信息含具体规则 id 与原因）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验规则库与依赖矩阵")
    parser.add_argument("--rules-dir", type=Path, default=ROOT / "docs" / "规范" / "rules")
    args = parser.parse_args(argv)

    from uup2iso import __version__
    from uup2iso.core.errors import ConfigError
    from uup2iso.infrastructure.rules import load_library, load_matrix

    try:
        library = load_library(rules_dir=args.rules_dir, engine_version=__version__)
    except ConfigError as exc:
        print(f"[FAIL] {exc.code.value}: {exc.message}")
        return 2

    print(
        f"[OK] 规则库：{len(library.packs)} 个包 / {library.rule_count} 条规则"
        f"（引擎版本 {library.engine_version or '未判定'}）"
    )
    if library.skipped_min_engine:
        print(
            f"[WARN] 因 minEngine 跳过 {len(library.skipped_min_engine)} 条：{list(library.skipped_min_engine)}"
        )

    try:
        matrix = load_matrix(args.rules_dir / "dependency-matrix.json")
    except ValueError as exc:
        print(f"[FAIL] 依赖矩阵：{exc}")
        return 2
    print(
        f"[OK] 依赖矩阵：{len(matrix.capabilities)} 条能力 / {len(matrix.blocked_combos)} 条禁止组合"
    )

    preset_names = sorted({name for rule in library.rules.values() for name in rule.presets})
    for name in preset_names:
        print(f"     preset {name}: {len(library.by_preset(name))} 条规则")
    return 0


if __name__ == "__main__":
    sys.exit(main())
