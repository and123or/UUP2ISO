"""TaskConfig 的读写：V0 语法 → V1 Schema → 迁移 → V2 语义 → 指纹（配置 §8、§9）。

顺序是有意为之：先把「文件根本不是 JSON」「字段类型不对」这类问题挡在最前面，
再谈语义；迁移发生在 V1/V2 之前，因为老版本的字段名可能与当前 Schema 不符。

三条硬规则：

* **V0 拒绝 BOM**（配置 §8.1 明确「UTF-8、无 BOM」）；
* **主版本不匹配直接拒绝**（§7：不进入未知字段逻辑）；
* **迁移失败不写回**，原文件保留（§9）。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import jsonschema
from pydantic import ValidationError

from uup2iso.core.errors import (
    ConfigError,
    ConfigMigrationError,
    ConfigParseError,
    ConfigSchemaError,
    ConfigVersionError,
    ErrorCode,
)
from uup2iso.core.models.config import (
    CONFIG_SCHEMA_VERSION,
    SUPPORTED_MAJOR,
    ConfigIssue,
    TaskConfig,
    iter_unknown_fields,
)

__all__ = [
    "CURRENT_VERSION",
    "LoadedConfig",
    "MIGRATORS",
    "default_schema_path",
    "dump_task_config",
    "load_task_config",
    "register_migrator",
    "save_task_config",
]

_SCHEMA_RESOURCE = "schemas/taskconfig.schema.json"
_BOM = b"\xef\xbb\xbf"

CURRENT_VERSION: tuple[int, int] = tuple(  # type: ignore[assignment]
    int(part) for part in CONFIG_SCHEMA_VERSION.split(".")
)
"""当前配置版本 ``(major, minor)``。"""

MIGRATORS: dict[tuple[int, int], Callable[[dict[str, Any]], dict[str, Any]]] = {}
"""迁移器注册表：键是**源**版本；每个迁移器必须幂等（配置 §9）。"""


def register_migrator(
    source: tuple[int, int],
) -> Callable[
    [Callable[[dict[str, Any]], dict[str, Any]]], Callable[[dict[str, Any]], dict[str, Any]]
]:
    """注册一个迁移器（测试与将来升级都用它，避免散落 if-else）。"""

    def decorator(
        func: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> Callable[[dict[str, Any]], dict[str, Any]]:
        MIGRATORS[source] = func
        return func

    return decorator


def default_schema_path() -> Path:
    """权威 Schema 的包内路径（打包时复制到 ``程序目录\\schemas\\``）。"""
    return Path(str(files("uup2iso").joinpath(_SCHEMA_RESOURCE)))


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """加载结果：配置本体 + 校验过程产生的旁证。"""

    config: TaskConfig
    fingerprint: str
    unknown_fields: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    migrated_from: str | None = None
    source_path: Path | None = None


def load_task_config(
    path: Path,
    *,
    conservative_mode: bool = False,
    known_edition_indexes: set[int] | None = None,
    strict_unknown: bool | None = None,
    schema_path: Path | None = None,
) -> LoadedConfig:
    """加载并校验任务配置。

    Args:
        path: 配置文件路径。
        conservative_mode: 工具版本未知时的保守模式（引擎 §5）。
        known_edition_indexes: ``probe.json`` 的索引集合；``None`` 表示未探测。
        strict_unknown: 覆盖配置内的 ``execution.validation.strictUnknownFields``；
            ``None`` 表示以配置为准（配置 §3.5：严格开关唯一归属 TaskConfig）。
        schema_path: 覆盖权威 Schema 路径（默认用包内副本）。

    Returns:
        加载结果（含指纹与警告）。

    Raises:
        ConfigParseError: V0 失败（非 UTF-8/BOM/JSON 语法）。
        ConfigVersionError: 主版本不受支持。
        ConfigMigrationError: 需要迁移但没有迁移器，或迁移后仍不合法。
        ConfigSchemaError: V1 失败或严格模式下存在未知字段。
        ConfigSemanticError: V2 失败。
    """
    raw = path.read_bytes()
    payload = _parse_json(raw, path)

    version = _read_version(payload)
    payload, migrated_from = _apply_migration(payload, version)

    _validate_schema(payload, schema_path or default_schema_path())

    try:
        config = TaskConfig.model_validate(payload)
    except ValidationError as exc:
        raise ConfigSchemaError(
            f"配置不符合模型：{_format_pydantic(exc)}",
            file=path,
        ) from exc

    unknown = tuple(iter_unknown_fields(config))
    effective_strict = _resolve_strict(config, strict_unknown)
    if effective_strict and unknown:
        raise ConfigSchemaError(
            f"存在 {len(unknown)} 个未知字段（严格模式）：{list(unknown[:10])}",
            file=path,
        )

    issues = config.check_semantics(
        conservative_mode=conservative_mode,
        known_edition_indexes=known_edition_indexes,
    )
    if issues:
        raise _semantic_error(issues, path)

    warnings = list(_path_warnings(config))
    if unknown and not effective_strict:
        warnings.append(f"config_unknown_fields count={len(unknown)}")

    fingerprint = config.fingerprint()

    if migrated_from is not None:
        # 迁移成功才备份原文件（§9：读取 → 迁移 → 重新校验 → 备份 → 写回）
        config.schema_version = ".".join(str(part) for part in CURRENT_VERSION)
        backup = path.with_suffix(".bak")
        backup.write_bytes(raw)
        save_task_config(path, config)

    return LoadedConfig(
        config=config,
        fingerprint=fingerprint,
        unknown_fields=unknown,
        warnings=tuple(warnings),
        migrated_from=migrated_from,
        source_path=path,
    )


def save_task_config(path: Path, config: TaskConfig) -> Path:
    """原子写回配置（先写同目录临时文件，再 ``os.replace``）。"""
    text = dump_task_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path


def dump_task_config(config: TaskConfig) -> str:
    """序列化为 JSON 文本（别名 + 省略 None，便于人读与 diff）。"""
    data = config.model_dump(mode="json", by_alias=True, exclude_none=True)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# 分层实现
# ---------------------------------------------------------------------------


def _parse_json(raw: bytes, path: Path) -> dict[str, Any]:
    """V0：UTF-8、无 BOM、JSON 可解析。"""
    if raw.startswith(_BOM):
        raise ConfigParseError(
            "配置文件带 UTF-8 BOM；配置规范 §8.1 要求无 BOM",
            file=path,
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigParseError(f"配置文件不是合法 UTF-8：{exc}", file=path) from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigParseError(
            f"JSON 解析失败：{exc.msg}（第 {exc.lineno} 行）", file=path
        ) from exc

    if not isinstance(payload, dict):
        raise ConfigSchemaError("配置文件顶层必须是对象", file=path)
    return payload


def _read_version(payload: dict[str, Any]) -> tuple[int, int]:
    raw = payload.get("schemaVersion")
    if not isinstance(raw, str):
        raise ConfigSchemaError("缺少 schemaVersion 或类型不是字符串")
    parts = raw.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ConfigSchemaError(f"schemaVersion 格式非法：{raw}（应为 major.minor）")
    return int(parts[0]), int(parts[1])


def _apply_migration(
    payload: dict[str, Any], version: tuple[int, int]
) -> tuple[dict[str, Any], str | None]:
    """主版本不匹配即拒绝；次版本落后则经迁移器（§9）。"""
    major, minor = version
    if major != SUPPORTED_MAJOR:
        raise ConfigVersionError(
            f"配置主版本 {major} 不受支持（本程序支持 {SUPPORTED_MAJOR}.x），请用新版本重新创建"
        )
    if (major, minor) >= CURRENT_VERSION:
        return payload, None

    migrator = MIGRATORS.get(version)
    if migrator is None:
        raise ConfigMigrationError(f"缺少从 {major}.{minor} 到 {CONFIG_SCHEMA_VERSION} 的迁移器")
    try:
        migrated = migrator(dict(payload))
    except Exception as exc:  # 迁移器内部错误一律归为迁移失败，不写回
        raise ConfigMigrationError(f"迁移失败：{exc}") from exc

    migrated["schemaVersion"] = CONFIG_SCHEMA_VERSION
    return migrated, f"{major}.{minor}"


def _validate_schema(payload: dict[str, Any], schema_path: Path) -> None:
    """V1：对照权威 Schema（draft-07）。"""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    details = []
    for error in errors[:5]:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        details.append(f"{location}: {error.message}")
    raise ConfigSchemaError("配置不符合 Schema：" + "；".join(details))


def _resolve_strict(config: TaskConfig, override: bool | None) -> bool:
    if override is not None:
        return override
    if config.execution is None:
        return False
    return config.execution.validation.strict_unknown_fields


def _semantic_error(issues: list[ConfigIssue], path: Path) -> ConfigError:
    """把首条语义问题映射成具体错误码（如 ``OUTPUT_TYPE_UNSUPPORTED``）。

    语义层不只有一种失败：SWM 输出是「能力不支持」而不是「配置写错」，
    错误码要能让 GUI/CLI 分别呈现。
    """
    first = issues[0]
    try:
        code = ErrorCode(first.code)
    except ValueError:
        code = ErrorCode.CONFIG_SEMANTIC_INVALID
    return ConfigError(_format_issues(issues), code=code, file=path)


def _format_issues(issues: list[ConfigIssue]) -> str:
    head = "；".join(f"{issue.field}: {issue.message}" for issue in issues[:3])
    suffix = f"（共 {len(issues)} 项）" if len(issues) > 3 else ""
    return f"配置语义校验失败：{head}{suffix}"


def _format_pydantic(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:5]:
        location = ".".join(str(item) for item in error["loc"])
        parts.append(f"{location}: {error['msg']}")
    return "；".join(parts)


def _path_warnings(config: TaskConfig) -> list[str]:
    """路径存在性只做提示，不阻断（配置 §6：用户可能马上要插移动硬盘）。"""
    warnings: list[str] = []
    candidates: list[tuple[str, str]] = []

    if config.input.image is not None and config.input.image.path:
        candidates.append(("input.image.path", config.input.image.path))
    if config.input.uup_folder is not None and config.input.uup_folder.path:
        candidates.append(("input.uupFolder.path", config.input.uup_folder.path))
    if config.output.dir:
        candidates.append(("output.dir", config.output.dir))
    if config.convert is not None and config.convert.drivers.source_dir:
        candidates.append(("convert.drivers.sourceDir", config.convert.drivers.source_dir))

    for field, value in candidates:
        path = Path(value)
        if path.is_absolute() and not path.exists():
            warnings.append(f"path_not_portable field={field}")
    return warnings
