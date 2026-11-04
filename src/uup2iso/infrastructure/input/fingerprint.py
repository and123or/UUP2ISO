"""输入指纹（D2）：让「换了输入源」也能被续跑判定发现。

背景：`TaskConfig.fingerprint()` **排除路径**（为了跨机比对业务选项），
于是把 `input.uupFolder.path` 从 A 目录改成 B 目录时指纹不变；
而 `input_fingerprint` 在 CLI 里一直是空串 → 换源仍会被判「可续跑」，
复用旧源的 `install.wim`。本模块补上这一层。

指纹构成：**相对路径 + 文件大小**的有序摘要（不读内容，避免几 GB 的哈希开销）。
只要文件集合或大小变化就会变；镜像内容被原地替换但大小不变属极端情况，交给产物哈希兜底。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

__all__ = ["folder_fingerprint", "paths_fingerprint"]


def folder_fingerprint(path: Path, *, limit: int = 50000) -> str:
    """目录指纹：`sha256(相对路径 + 大小 …)`，按路径排序保证稳定。

    Args:
        path: 输入目录。
        limit: 最多统计多少个文件（超出即截断并计入标记，避免超大目录拖慢启动）。

    Returns:
        64 位十六进制摘要；目录不存在时返回空串。
    """
    if not path.is_dir():
        return ""
    files = _collect(path, limit=limit)
    if not files:
        return ""

    digest = hashlib.sha256()
    truncated = False
    for item in files:
        try:
            size = item.stat().st_size
        except OSError:
            continue
        relative = item.relative_to(path).as_posix()
        digest.update(f"{relative}\t{size}\n".encode("utf-8", errors="replace"))
        if len(files) >= limit:
            truncated = True
    if truncated:
        digest.update(b"<truncated>")
    return digest.hexdigest()


def paths_fingerprint(paths: Iterable[Path]) -> str:
    """少量文件的指纹（入口 C 用：直接对给定文件的大小排序摘要）。"""
    digest = hashlib.sha256()
    for item in sorted(paths, key=lambda p: str(p).lower()):
        try:
            digest.update(f"{item.name}\t{item.stat().st_size}\n".encode("utf-8", errors="replace"))
        except OSError:
            continue
    return digest.hexdigest()


def _collect(path: Path, *, limit: int) -> list[Path]:
    collected: list[Path] = []
    try:
        for item in sorted(path.rglob("*")):
            if item.is_file():
                collected.append(item)
                if len(collected) >= limit:
                    break
    except OSError:
        return collected
    return collected
