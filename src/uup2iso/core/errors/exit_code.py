"""进程退出码（GUI 框架 §5.6，唯一来源）。

| 码 | 含义 | UI 表现 |
| --- | --- | --- |
| 0 | 成功 | 绿色完成页 |
| 1 | 完成但有警告 | 黄色提示，可查看原因 |
| 2 | 阶段失败 | 红色错误卡 + 「查看日志」「重试本阶段」 |
| 3 | 用户取消 | 中性提示（保留中间产物，可 `--resume`） |
| 4 | 参数/依赖错误 | 前置校验拦截，指明缺失项 |
| 5 | 已有实例运行 | GUI 激活已有窗口后退出；CLI 输出 `already_running` 后退出 |

注意：**退出码 1 不是异常产物**——任务 `succeeded` 但有警告时由编排层折算，
异常一律 ≥2（见 :mod:`uup2iso.core.errors.base`）。
"""

from __future__ import annotations

from enum import IntEnum

__all__ = ["ExitCode"]


class ExitCode(IntEnum):
    """进程退出码（值即真实返回码，可直接 ``sys.exit(code)``）。"""

    SUCCESS = 0
    """成功，无警告。"""

    WARNING = 1
    """成功但有警告（阶段 `succeeded` + 警告明细）。"""

    FAILURE = 2
    """阶段失败（含输出校验失败，输出 §6）。"""

    CANCELLED = 3
    """用户取消：保留中间产物，可用 `--resume` 继续（编排 §8）。"""

    INVALID_ARGUMENT = 4
    """参数/依赖错误：配置校验失败、输入不支持、工具缺失、非管理员等。"""

    ALREADY_RUNNING = 5
    """已有实例运行（日志 §6.1：冲突实例不建日志、不清理、不写审计）。"""

    @property
    def is_success(self) -> bool:
        """是否算「任务完成」（0 与 1 都算，1 只是带警告）。"""
        return self in (ExitCode.SUCCESS, ExitCode.WARNING)
