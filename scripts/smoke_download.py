"""小范围下载联调（WP2-T6/T7）。

默认只下清单里**最小的 2 个文件**，验证 aria2c 链路与 SHA1 校验；确认无误后再考虑全量。

用法（仓库根目录、`.venv` 内）：

    .venv\\Scripts\\python scripts/smoke_download.py
    .venv\\Scripts\\python scripts/smoke_download.py --count 5 --build <uuid>
    .venv\\Scripts\\python scripts/smoke_download.py --names DesktopDeployment.cab

产物落在 `.tmp_dl\\<task_id>\\`（gitignore 的临时目录），可随时删除。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from uup2iso.application.fetch import ManifestRequest, build_manifest
from uup2iso.core.errors import Uup2IsoError
from uup2iso.infrastructure.download import Aria2Downloader
from uup2iso.infrastructure.logging import LogSink
from uup2iso.infrastructure.process.runner import ProcessRunner
from uup2iso.infrastructure.source import SITE_BASE_DEFAULT, UupDumpSource
from uup2iso.infrastructure.tools.lock import load_lock
from uup2iso.infrastructure.tools.provider import ToolProvider

ROOT = Path(__file__).resolve().parents[1]


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


async def run(args: argparse.Namespace) -> int:
    source = UupDumpSource(base_url=args.base)
    work = Path(args.work)
    sink = LogSink.open(work / "logs", entry="cli", level="INFO")
    try:
        builds = source.list_builds(search=args.search)
        build = next((item for item in builds if item.id == args.build), None)
        if build is None:
            clients = [item for item in builds if (item.title or "").startswith("Windows 1")]
            build = (clients or builds)[0]
        print(f"[OK] 构建 {build.build} id={build.id[:8]}")

        payload = source.get_files(build.id, language=args.lang, edition=args.edition)
        manifest, _warnings = build_manifest(
            payload,
            ManifestRequest(
                build=payload.build,
                language=args.lang,
                edition=args.edition,
                base_url=args.base,
                fetched_at=datetime.now(UTC),
            ),
        )

        if args.names:
            chosen = tuple(args.names)
        else:
            smallest = sorted(manifest.files, key=lambda item: item.size)[: args.count]
            chosen = tuple(item.name for item in smallest)
        total = sum(item.size for item in manifest.files if item.name in set(chosen))
        print(f"[OK] 选中 {len(chosen)} 个文件 / {_human(total)}：{', '.join(chosen)}")

        lock = load_lock(ROOT / "tools" / "tools.lock.json")
        provider = ToolProvider(
            tools_dir=ROOT / "tools",
            lock=lock.lock,
            runner=ProcessRunner(),
            search_path=False,
            cache_path=ROOT / "config" / "tool_caps.json",
        )
        downloader = Aria2Downloader(
            provider=provider,
            runner=ProcessRunner(),
            work_dir=work / "task",
            log=sink,
        )
        report = await downloader.download(manifest, dest_dir=work / "uup", names=chosen)

        print(
            f"[OK] 下载完成：{len(report.verified_files)}/{len(report.files)} 通过校验，"
            f"重下 {report.retry_rounds} 轮，{report.elapsed_seconds:.1f}s，"
            f"{_human(report.total_bytes)}"
        )
        for item in report.files:
            flag = "OK  " if item.is_ok else "FAIL"
            print(
                f"     [{flag}] {item.name:<45} {_human(item.size_bytes or 0):>9}  {item.state.value}"
            )
        print(f"[OK] 状态文件：{downloader.state_path}")
        return 0 if report.is_complete else 2
    except Uup2IsoError as exc:
        print(f"[FAIL] {exc.code.value}: {exc.message}")
        return 2
    finally:
        sink.close()
        source.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="uupdump 小范围下载联调")
    parser.add_argument("--base", default=SITE_BASE_DEFAULT)
    parser.add_argument("--search", default="26100")
    parser.add_argument("--build", default="", help="构建 uuid；留空自动挑客户端构建")
    parser.add_argument("--lang", default="zh-cn")
    parser.add_argument("--edition", default="professional")
    parser.add_argument("--count", type=int, default=2, help="按大小取最小的 N 个文件")
    parser.add_argument("--names", nargs="*", default=[], help="直接指定文件名")
    parser.add_argument("--work", default=".tmp_dl")
    parser.add_argument("--clean", action="store_true", help="开始前清空工作目录")
    args = parser.parse_args(argv)

    if args.clean and Path(args.work).exists():
        shutil.rmtree(args.work, ignore_errors=True)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
