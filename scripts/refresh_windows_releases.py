"""从 GitHub 刷新版本快照（P0-0）。

默认**只打印不写盘**（安全）；确认无误后加 `--write` 覆盖
`src/uup2iso/data/windows_releases.json`。

用法（仓库根目录、`.venv` 内）：

    .venv\\Scripts\\python scripts\\refresh_windows_releases.py
    .venv\\Scripts\\python scripts\\refresh_windows_releases.py --repo owner/name --path releases.md --write

注意：GitHub 上**没有稳定的官方机器可读版本表**（实测多个候选仓库 404），
所以需要显式给 `--repo/--path`；不给则只校验当前快照。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from uup2iso.infrastructure.version import GitHubSource, VersionCatalog

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "src" / "uup2iso" / "data" / "windows_releases.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="刷新 Windows 版本快照")
    parser.add_argument("--repo", default="", help="GitHub 仓库，形如 owner/name")
    parser.add_argument("--path", default="", help="仓库内文件路径（Markdown/JSON 文本均可）")
    parser.add_argument("--branch", default="", help="分支（默认仓库默认分支）")
    parser.add_argument("--write", action="store_true", help="写回快照（默认只打印）")
    args = parser.parse_args(argv)

    catalog = VersionCatalog(snapshot_path=SNAPSHOT)

    if not (args.repo and args.path):
        current = catalog.load()
        print(f"[INFO] 未指定 GitHub 源，仅校验当前快照（{len(current)} 条）")
        for item in current:
            print(f"       {item.family:<6} {item.major}")
        print("[INFO] 用法：--repo owner/name --path <file> [--write]")
        return 0 if current else 2

    source = GitHubSource(repo=args.repo, path=args.path, branch=args.branch)
    remote = VersionCatalog(
        snapshot_path=SNAPSHOT,
        sources=(source,),
        cache_dir=None,
    )
    releases = remote.refresh()
    origin = next((item for item in remote.warnings if item.startswith("version_source_ok")), "")
    print(f"[OK] 从 {source.describe()} 解析到 {len(releases)} 条：{origin}")
    for item in releases:
        print(f"     {item.family:<6} {item.major}")

    if not releases:
        print("[FAIL] 没有解析出任何版本对（检查文件内容是否含 '26H2 … 26300' 这类同行文本）")
        return 2
    if not args.write:
        print("[INFO] 未写盘（加 --write 覆盖快照）")
        return 0

    payload = {
        "schemaVersion": "1.0",
        "updatedAt": datetime.now(UTC).isoformat(),
        "source": {
            "kind": "github",
            "repo": source.repo,
            "path": source.path,
            "branch": source.branch or "default",
            "verified": True,
        },
        "releases": [
            {"family": item.family, "major": item.major, "channel": item.channel}
            for item in releases
        ],
    }
    SNAPSHOT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"[OK] 已写入 {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
