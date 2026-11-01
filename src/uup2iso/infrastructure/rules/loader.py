"""规则库加载与校验（规则 §2、§8）。

加载即校验，**不做部分加载**：任何一条不合规就整体拒绝（`RULEPACK_INVALID`），
否则「一半规则生效」比「没生效」更难排查。

校验项（规则 §8）：

1. JSON Schema（`rules.schema.json`）；
2. id 全局唯一（跨包也算）；
3. `requires` / `conflicts` 指向存在的规则；
4. `requires` 传递依赖无环（否则 `RULEPACK_CYCLIC`）；
5. 高风险项必须有 `verify`；不可逆（`reversible` 缺省为 false）必须有 `recovery`；
6. `minEngine` 高于当前程序版本的规则**跳过并提示**，不算非法（规则 §8）。

查找顺序（支持外置更新）：``程序目录\\rules`` → 包内 ``uup2iso/rules`` → 仓库 ``docs/规范/rules``。
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

import jsonschema

from uup2iso.core.errors import ConfigError, ErrorCode
from uup2iso.core.models.rulepack import Rule, RuleLibrary, RulePack, RulePackIssue
from uup2iso.infrastructure.tools.capabilities import is_version_at_least, parse_version

__all__ = [
    "MATRIX_FILENAME",
    "RULES_GLOB",
    "SCHEMA_FILENAME",
    "default_rules_dir",
    "load_library",
    "load_pack",
]

RULES_GLOB = "*-rules.json"
SCHEMA_FILENAME = "rules.schema.json"
MATRIX_FILENAME = "dependency-matrix.json"


def _repo_rules_dir() -> Path | None:
    """开发期回退：仓库里的 ``docs/规范/rules``。"""
    candidate = Path(__file__).resolve().parents[4] / "docs" / "规范" / "rules"
    return candidate if candidate.is_dir() else None


def _bundled_rules_dir() -> Path | None:
    """包内副本（打包时由 WP11 复制到 ``uup2iso/rules``）。"""
    candidate = Path(__file__).resolve().parents[2] / "rules"
    return candidate if candidate.is_dir() else None


def _program_rules_dir() -> Path | None:
    """程序目录旁的 ``rules\\``（外置更新用，优先级最高）。"""
    try:
        base = Path(sys.executable).resolve().parent
    except OSError:  # pragma: no cover - 极端环境
        return None
    candidate = base / "rules"
    return candidate if candidate.is_dir() else None


def default_rules_dir() -> Path:
    """按优先级找规则目录；都没有时返回仓库路径（便于报错指明期望位置）。"""
    for resolver in (_program_rules_dir, _bundled_rules_dir, _repo_rules_dir):
        found = resolver()
        if found is not None:
            return found
    return Path(__file__).resolve().parents[4] / "docs" / "规范" / "rules"


def load_pack(path: Path, *, schema: dict[str, object] | None = None) -> RulePack:
    """读取并 Schema 校验单个规则包（不含跨包语义校验）。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _invalid(f"{path.name}: 无法解析（{exc}）") from exc

    if schema is not None:
        validator = jsonschema.Draft7Validator(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
        if errors:
            detail = "；".join(
                f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
                for error in errors[:5]
            )
            raise _invalid(f"{path.name}: 不符合 rules.schema.json —— {detail}")

    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise _invalid(f"{path.name}: rules 必须是非空数组")

    rules: list[Rule] = []
    for item in raw_rules:
        rules.append(_to_rule(item, pack=path.name))

    return RulePack(
        path=path,
        schema_version=str(payload.get("schemaVersion", "")),
        pack_version=str(payload.get("packVersion", "")),
        updated=str(payload.get("updated", "")),
        source=str(payload.get("source", "")),
        rules=tuple(rules),
    )


def load_library(
    paths: Iterable[Path] | None = None,
    *,
    rules_dir: Path | None = None,
    schema_path: Path | None = None,
    engine_version: str = "",
) -> RuleLibrary:
    """加载并校验规则库。

    Args:
        paths: 规则包路径；``None`` 时按 :func:`default_rules_dir` 自动发现。
        rules_dir: 规则目录（``paths`` 为 ``None`` 时使用）。
        schema_path: 覆盖 Schema 路径。
        engine_version: 当前程序版本，用于 ``minEngine`` 判定；空串表示不判定。

    Raises:
        ConfigError: 规则库非法（``RULEPACK_INVALID``）或存在环（``RULEPACK_CYCLIC``）。
    """
    directory = rules_dir or default_rules_dir()
    schema = _load_schema(schema_path or directory / SCHEMA_FILENAME)

    pack_paths: Sequence[Path] = (
        sorted(directory.glob(RULES_GLOB)) if paths is None else list(paths)
    )

    if not pack_paths:
        raise _invalid(f"在 {directory} 下未找到任何规则包（{RULES_GLOB}）")

    packs = tuple(load_pack(path, schema=schema) for path in pack_paths)

    issues: list[RulePackIssue] = list(_check_duplicates(packs))
    rules: dict[str, Rule] = {}
    skipped: list[str] = []

    for pack in packs:
        for rule in pack.rules:
            if engine_version and rule.min_engine:
                required = parse_version(rule.min_engine)
                if required is not None and not is_version_at_least(
                    parse_version(engine_version), required
                ):
                    skipped.append(rule.id)
                    continue
            rules[rule.id] = rule

    issues.extend(_check_references(rules))
    issues.extend(_check_high_risk(rules))

    cycle = _find_cycle(rules)
    if cycle:
        raise ConfigError(
            "规则库存在循环依赖（RULEPACK_CYCLIC）：" + " → ".join(cycle),
            code=ErrorCode.RULEPACK_CYCLIC,
        )

    if issues:
        detail = "；".join(issue.as_text() for issue in issues[:8])
        more = f"（共 {len(issues)} 项）" if len(issues) > 8 else ""
        raise _invalid(detail + more)

    return RuleLibrary(
        packs=packs,
        rules=rules,
        skipped_min_engine=tuple(sorted(skipped)),
        engine_version=engine_version,
    )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _load_schema(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _invalid(f"规则 Schema 无法读取：{path}（{exc}）") from exc


def _invalid(message: str) -> ConfigError:
    return ConfigError(message, code=ErrorCode.RULEPACK_INVALID)


def _as_int(value: object) -> int | None:
    """把 JSON 里的数字（可能是 int 或数字字符串）转成 int；其余返回 ``None``。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _to_rule(item: dict[str, object], *, pack: str) -> Rule:
    def _as_tuple(key: str) -> tuple[str, ...]:
        value = item.get(key, [])
        return tuple(str(entry) for entry in value) if isinstance(value, list) else ()

    actions_value = item.get("actions", [])
    actions: tuple[dict[str, object], ...] = ()
    if isinstance(actions_value, list):
        actions = tuple(entry for entry in actions_value if isinstance(entry, dict))

    verify_value = item.get("verify")
    verify: dict[str, object] | None = verify_value if isinstance(verify_value, dict) else None

    return Rule(
        id=str(item["id"]),
        name=str(item.get("name", "")),
        domain=str(item.get("domain", "")),
        group=str(item.get("group", "")),
        level=str(item.get("level", "low")),
        order=_as_int(item.get("order")) or 0,
        default_on=bool(item.get("defaultOn", False)),
        requires=_as_tuple("requires"),
        conflicts=_as_tuple("conflicts"),
        presets=_as_tuple("presets"),
        context=_as_tuple("context"),
        description=str(item.get("description", "")),
        warning=str(item.get("warning", "")),
        actions=actions,
        verify=verify,
        reversible=bool(item.get("reversible", False)),
        recovery=str(item.get("recovery", "")),
        min_engine=str(item["minEngine"]) if item.get("minEngine") else None,
        estimated_bytes=_as_int(item.get("estimatedBytes")),
        pack=pack,
        raw=dict(item),
    )


def _check_duplicates(packs: Sequence[RulePack]) -> list[RulePackIssue]:
    seen: dict[str, str] = {}
    issues: list[RulePackIssue] = []
    for pack in packs:
        for rule in pack.rules:
            if rule.id in seen:
                issues.append(
                    RulePackIssue(
                        where=rule.id,
                        code=ErrorCode.RULEPACK_INVALID.value,
                        message=f"id 重复（{seen[rule.id]} 与 {pack.name}）",
                    )
                )
            else:
                seen[rule.id] = pack.name
    return issues


def _check_references(rules: dict[str, Rule]) -> list[RulePackIssue]:
    issues: list[RulePackIssue] = []
    for rule in rules.values():
        for target in rule.requires:
            if target not in rules:
                issues.append(
                    RulePackIssue(
                        where=rule.id,
                        code=ErrorCode.RULEPACK_INVALID.value,
                        message=f"requires 指向不存在的规则 {target}",
                    )
                )
        for target in rule.conflicts:
            if target not in rules:
                issues.append(
                    RulePackIssue(
                        where=rule.id,
                        code=ErrorCode.RULEPACK_INVALID.value,
                        message=f"conflicts 指向不存在的规则 {target}",
                    )
                )
    return issues


def _check_high_risk(rules: dict[str, Rule]) -> list[RulePackIssue]:
    """高风险硬要求（规则 §2.2）：缺 verify，或不可逆却无 recovery，一律判库无效。"""
    issues: list[RulePackIssue] = []
    for rule in rules.values():
        if not rule.is_high_risk:
            continue
        if rule.verify is None:
            issues.append(
                RulePackIssue(
                    where=rule.id,
                    code=ErrorCode.RULEPACK_INVALID.value,
                    message="level=high 必须提供 verify（如何证明生效）",
                )
            )
        if not rule.reversible and not rule.recovery:
            issues.append(
                RulePackIssue(
                    where=rule.id,
                    code=ErrorCode.RULEPACK_INVALID.value,
                    message="level=high 且不可逆，必须提供 recovery（影响面与恢复方式）",
                )
            )
    return issues


def _find_cycle(rules: dict[str, Rule]) -> list[str]:
    """在 ``requires`` 图上找环；返回环上的 id 序列（含首尾重复）。"""
    white, grey, black = 0, 1, 2
    color = dict.fromkeys(rules, white)
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = grey
        stack.append(node)
        for target in rules[node].requires:
            if target not in rules:
                continue
            if color[target] == grey:
                start = stack.index(target)
                return [*stack[start:], target]
            if color[target] == white:
                found = visit(target)
                if found:
                    return found
        stack.pop()
        color[node] = black
        return None

    for rule_id in rules:
        if color[rule_id] == white:
            found = visit(rule_id)
            if found:
                return found
    return []
