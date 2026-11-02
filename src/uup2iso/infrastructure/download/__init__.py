"""下载执行（WP2-T6 / T7）：aria2c 输入列表、参数契约、校验与重下。

**只经 `ProcessRunner` 调 aria2c**；网络层重试交给 aria2c，校验层（大小/SHA1）由本包负责。
"""

from __future__ import annotations

from uup2iso.infrastructure.download.aria2 import (
    Aria2Downloader,
    Aria2Options,
    build_input_file,
    sha1_of,
    verify_file,
)

__all__ = [
    "Aria2Downloader",
    "Aria2Options",
    "build_input_file",
    "sha1_of",
    "verify_file",
]
