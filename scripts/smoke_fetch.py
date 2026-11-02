"""`Fetch` 阶段全量联调（WP2-T4 胶水层）。

真实走一遍「清单 → aria2c 下载全部文件 → 逐文件 SHA1 校验」，
用 `build_handlers()` 里的同一条组合链（与 `uup2iso run` 完全一致）。

用法（仓库根目录、`.venv` 内）：

    .venv\\Scripts\\python scripts/smoke_fetch.py --build 26100.9725
    .venv\\Scripts\\python scripts/smoke_fetch.py --build 26100.9725 --limit 20   # 只下前 20 个小文件

产物落在 `.tmp_fetch\\<task_id>\\download\\`（临时目录，可随时删）。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time
import uuid
from pathlib import Path

from uup2iso.application.cli_runner import build_handlers
from uup2iso.application.pipeline import StageContext, plan_pipeline
from uup2iso.application.pipeline.handlers import FetchStageHandler
from uup2iso.core.errors import Uup2IsoError
from uup2iso.core.models import default_task_config
from uup2iso.core.models.enums import EntryPoint
from uup2iso.core.models.pipeline import StageKind
from uup2iso.infrastructure.logging import LogSink
from uup2iso.infrastructure.progress import ProgressHub


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


async def run(args: argparse.Namespace) -> int:
    task_id = uuid.uuid4().hex[:8]
    work = Path(args.work) / task_id
    work.mkdir(parents=True, exist_ok=True)

    config = default_task_config(mode=EntryPoint.ONLINE)
    config.input.start_stage = "Convert"
    config.source = {  # type: ignore[assignment]
        "baseUrl": args.base,
        "build": args.build,
        "revision": args.build.partition(".")[2],
        "language": args.lang,
        "edition": args.edition,
        "channel": "Retail",
    }

    plan = plan_pipeline(config).stage(StageKind.FETCH)
    if plan is None:
        print("[FAIL] 计划里没有 Fetch 阶段")
        return 2

    handlers = build_handlers(config)
    handler = handlers.get(StageKind.FETCH)
    if not isinstance(handler, FetchStageHandler):
        print("[FAIL] 没有可用的 Fetch 处理器")
        return 2
    if args.names:
        handler._names = tuple(args.names)  # 调试：只下指定文件

    sink = LogSink.open(work / "logs", entry="cli", level="INFO")
    hub = ProgressHub(min_interval=5.0)
    last = {"pct": -1.0}

    def _on_progress(update: object) -> None:
        pct = getattr(update, "percent", None)
        if pct is None:
            return
        if pct - last["pct"] >= 5:
            last["pct"] = pct
            print(f"      … {pct:.0f}%", flush=True)

    hub.subscribe(_on_progress)

    ctx = StageContext(
        task_id=task_id,
        config=config,
        stage=StageKind.FETCH,
        plan=plan,
        work_dir=work,
        log=sink,
        progress=hub,
    )

    started = time.monotonic()
    print(f"[OK] 任务 {task_id}｜build={args.build}｜{args.lang}/{args.edition}", flush=True)
    print(f"[OK] 工作目录 {work}", flush=True)
    try:
        await handler.run(ctx)
    except Uup2IsoError as exc:
        print(f"[FAIL] {exc.code.value}: {exc.message}", flush=True)
        return 2
    finally:
        elapsed = time.monotonic() - started
        sink.close()

    outcome = handler.outcome
    if outcome is None:
        print("[FAIL] 处理器没有产出结果")
        return 2
    speed = outcome.total_bytes / elapsed / 1e6 if elapsed > 0 else 0
    print(
        f"[OK] Fetch 完成：{outcome.verified_files}/{outcome.file_count} 通过校验"
        f"｜{_human(outcome.total_bytes)}｜{elapsed:.1f}s｜{speed:.1f} MB/s"
        f"｜重下 {outcome.retry_rounds} 轮｜清单刷新 {'是' if outcome.refreshed else '否'}"
    )
    print(f"[OK] 清单 {outcome.manifest_path}")
    print(f"[OK] 日志 {sink.paths.run}")
    return 0 if outcome.verified_files == outcome.file_count else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch 阶段全量联调")
    parser.add_argument("--base", default="https://uupdump.net/")
    parser.add_argument("--build", default="26100.9725")
    parser.add_argument("--lang", default="zh-cn")
    parser.add_argument("--edition", default="professional")
    parser.add_argument("--work", default=".tmp_fetch")
    parser.add_argument("--names", nargs="*", default=[], help="只下这些文件（调试用）")
    parser.add_argument("--clean", action="store_true", help="开始前清空工作根目录")
    args = parser.parse_args(argv)

    if args.clean and Path(args.work).exists():
        shutil.rmtree(args.work, ignore_errors=True)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
