"""挂载残留防护（转换器 `%_drv%\\MountUUP` 与 DISM 挂载）。

背景（实测）：`convert-UUP.cmd` 把映像挂到 `%_drv%\\MountUUP`（脚本 4713/4718 行），
中断（关窗口 / 强杀）时走不到 `/Unmount-Wim`，于是：

* 目录残留成"半挂载"状态；
* 下次 `Mount-Wim` 到非空目录会失败；
* DISM 里还留着挂载记录，占用 wim 句柄。

本模块只做两件事，**都不主动删用户的东西**：

1. :func:`residual_mount_dirs` —— 扫描各盘根下的 `MountUUP`，给出"运行前基线"；
2. :func:`cleanup_new_mounts` —— 只在**本次运行新增**的挂载点上做 `Unmount + rmdir`。

「新增」= 结束后存在、运行前不存在。用户手工挂的（基线已有）一律不碰。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "MOUNT_DIR_NAME",
    "MountSnapshot",
    "cleanup_new_mounts",
    "mounted_dirs_from_dism",
    "residual_mount_dirs",
]

MOUNT_DIR_NAME = "MountUUP"
"""转换器的挂载目录名（`convert-UUP.cmd` 4713 行）。"""

_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:$")
_MOUNT_LINE = re.compile(r"Mount Dir\s*:\s*(?P<path>.+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MountSnapshot:
    """一次"运行前/后"的挂载点快照。"""

    dirs: tuple[Path, ...] = ()

    def new_since(self, other: MountSnapshot) -> tuple[Path, ...]:
        """相对 ``other`` 新增的挂载点。"""
        known = {str(item).lower() for item in other.dirs}
        return tuple(item for item in self.dirs if str(item).lower() not in known)

    def __bool__(self) -> bool:
        return bool(self.dirs)


def residual_mount_dirs(drives: Iterable[str] | None = None) -> MountSnapshot:
    """扫描各盘根下的 ``MountUUP``（存在即算，不管是否为空）。"""
    roots = list(drives) if drives is not None else _available_drives()
    found: list[Path] = []
    for drive in roots:
        text = drive if drive.endswith(":") else f"{drive}:"
        if not _DRIVE_PATTERN.match(text):
            continue
        candidate = Path(f"{text}/") / MOUNT_DIR_NAME
        if candidate.exists():
            found.append(candidate)
    return MountSnapshot(dirs=tuple(found))


def mounted_dirs_from_dism(dism_exe: Path, *, timeout: float = 60.0) -> tuple[Path, ...]:
    """从 `dism /Get-MountedImageInfo` 解析当前挂载目录。

    需要管理员权限；拿不到（740）或解析不到就返回空元组——调用方据此**降级为目录扫描**。
    """
    try:
        result = subprocess.run(  # noqa: S603 - 固定参数、shell=False
            [str(dism_exe), "/English", "/Get-MountedImageInfo"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0:
        return ()
    found: list[Path] = []
    for line in (result.stdout or "").splitlines():
        match = _MOUNT_LINE.search(line)
        if match:
            found.append(Path(match.group("path").strip()))
    return tuple(found)


def cleanup_new_mounts(
    baseline: MountSnapshot,
    *,
    dism_exe: Path | None = None,
    log: object | None = None,
) -> tuple[Path, ...]:
    """清理**本次运行新增**的挂载点，返回已清理的路径。

    顺序：先 `dism /Unmount-Image /Discard`（能清 DISM 记录），再 `rmdir`；
    任何一步失败只记 WARN，不抛——清理是尽力而为，不能把主流程带崩。
    """
    current = residual_mount_dirs()
    fresh = current.new_since(baseline)
    cleaned: list[Path] = []
    for mount_dir in fresh:
        if dism_exe is not None and dism_exe.is_file():
            _unmount(dism_exe, mount_dir, log=log)
        try:
            if mount_dir.is_dir():
                shutil.rmtree(mount_dir)
            cleaned.append(mount_dir)
            _warn(log, f"mount_cleanup dir={mount_dir}")
        except OSError as exc:
            _warn(log, f"mount_cleanup_failed dir={mount_dir} reason={type(exc).__name__}")
    return tuple(cleaned)


def _unmount(dism_exe: Path, mount_dir: Path, *, log: object | None) -> None:
    for args in (
        [str(dism_exe), "/English", "/Unmount-Image", f"/MountDir:{mount_dir}", "/Discard"],
        [str(dism_exe), "/English", "/Cleanup-Wim"],
    ):
        try:
            result = subprocess.run(  # noqa: S603 - 固定参数、shell=False
                args, capture_output=True, text=True, timeout=300, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _warn(log, f"mount_unmount_failed dir={mount_dir} reason={type(exc).__name__}")
            return
        if result.returncode == 0:
            _warn(log, f"mount_unmounted dir={mount_dir}")
            return
    _warn(log, f"mount_unmount_rejected dir={mount_dir}")


def _available_drives() -> list[str]:
    letters: list[str] = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:/")
        try:
            if root.exists():
                letters.append(letter)
        except OSError:
            continue
    return letters


def _warn(log: object | None, message: str) -> None:
    if log is None:
        return
    try:
        log.warn("infra.mount", message, event="mount_guard")  # type: ignore[attr-defined]
    except AttributeError:
        return
