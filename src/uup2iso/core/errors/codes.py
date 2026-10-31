"""稳定错误码（机器可读，取值全部来自文档，不得临场发明）。

来源分布：

* 数据源 / 清单 → 下载 §2.4、§3.2
* 输入 → 输入 §3、§9
* 配置 → 配置 §2、§3.5
* 阶段 → 输入 §6、编排 §2
* 工具 / 权限 → 进程 §9.1
* 输出 → 配置 §3.4、输出 §4、§6
* 规则 → 规则 §8
* 集成 / 无人值守 → 集成 §5、无人值守 §5
* 单实例 → 日志 §6.1

约定：枚举值是文档里的**大写常量**；对外 JSON 用小写 slug（见 :attr:`slug`），
与日志 §6.1 的 ``{"error":"already_running","code":5}`` 对齐。
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ErrorCode"]


class ErrorCode(StrEnum):
    """错误码。

    新增错误码的前提：先在对应规范里登记，否则视为「文档与实现不一致」。
    """

    # --- 数据源与清单（下载 §2.4、§3.2） ---
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_RATE_LIMITED = "SOURCE_RATE_LIMITED"
    SOURCE_SCHEMA_INVALID = "SOURCE_SCHEMA_INVALID"
    BUILD_NOT_FOUND = "BUILD_NOT_FOUND"
    BUILD_WITHDRAWN = "BUILD_WITHDRAWN"
    MANIFEST_INVALID = "MANIFEST_INVALID"

    # --- 输入识别（输入 §3、§9） ---
    INPUT_AMBIGUOUS = "INPUT_AMBIGUOUS"
    INPUT_ENCRYPTED = "INPUT_ENCRYPTED"
    INPUT_MULTI_VOLUME = "INPUT_MULTI_VOLUME"
    INPUT_FRAGMENTED = "INPUT_FRAGMENTED"
    INPUT_NOT_INSTALL_MEDIA = "INPUT_NOT_INSTALL_MEDIA"
    INPUT_NOT_WINDOWS = "INPUT_NOT_WINDOWS"
    INPUT_UNREADABLE = "INPUT_UNREADABLE"
    INPUT_DAMAGED = "INPUT_DAMAGED"
    INPUT_IMMUTABLE_VIOLATION = "INPUT_IMMUTABLE_VIOLATION"

    # --- 配置（配置 §2、§3.5） ---
    CONFIG_PARSE_ERROR = "CONFIG_PARSE_ERROR"
    """V0 语法层失败：JSON 不可解析、非 UTF-8、带 BOM（配置 §8.1）。"""

    CONFIG_SCHEMA_INVALID = "CONFIG_SCHEMA_INVALID"
    CONFIG_SEMANTIC_INVALID = "CONFIG_SEMANTIC_INVALID"
    CONFIG_VERSION_UNSUPPORTED = "CONFIG_VERSION_UNSUPPORTED"
    CONFIG_MIGRATION_FAILED = "CONFIG_MIGRATION_FAILED"
    CONFIG_CHANGED = "CONFIG_CHANGED"
    """配置变更导致拒绝续跑（编排 §6 续跑四条件）。"""

    STAGE_TOO_EARLY = "STAGE_TOO_EARLY"
    """起始阶段早于输入支持的最早阶段（输入 §6）。"""

    # --- 工具与权限（进程 §9.1） ---
    TOOL_MISSING = "TOOL_MISSING"
    TOOL_START_FAILED = "TOOL_START_FAILED"
    TOOL_FAILED = "TOOL_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    DECODE_ERROR = "DECODE_ERROR"

    # --- 输出（输出 §4、§6；配置 §3.4） ---
    OUTPUT_TYPE_UNSUPPORTED = "OUTPUT_TYPE_UNSUPPORTED"
    OUTPUT_CROSS_VOLUME = "OUTPUT_CROSS_VOLUME"
    DISK_FULL = "DISK_FULL"

    # --- 规则（规则 §8） ---
    RULEPACK_INVALID = "RULEPACK_INVALID"
    RULEPACK_CYCLIC = "RULEPACK_CYCLIC"
    RULE_CONTEXT_UNSATISFIED = "RULE_CONTEXT_UNSATISFIED"

    # --- 集成 / 无人值守（集成 §5、无人值守 §5） ---
    INTEGRATE_LANG_INVALID = "INTEGRATE_LANG_INVALID"
    UNATTEND_XSD_INVALID = "UNATTEND_XSD_INVALID"
    UNATTEND_VERSION_UNKNOWN = "UNATTEND_VERSION_UNKNOWN"
    UNATTEND_SEMANTIC_INVALID = "UNATTEND_SEMANTIC_INVALID"

    # --- 进程级（日志 §6.1、进程 §9.1） ---
    ALREADY_RUNNING = "ALREADY_RUNNING"
    CANCELED = "CANCELED"
    """用户取消（拼写沿用进程 §9.1 原文，仅一个 L）。"""

    INTERNAL_ERROR = "INTERNAL_ERROR"
    """未归类的内部错误：兜底用，出现即应补分类。"""

    @property
    def slug(self) -> str:
        """对外 JSON/日志用的小写形式，如 ``already_running``。"""
        return self.value.lower()
