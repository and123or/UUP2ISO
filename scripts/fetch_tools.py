"""按锁定清单补齐缺失的必需工具（引擎 §9.2）——开发/打包/首启自检都用它。

用法（仓库根目录、`.venv` 内）：

    .venv\\Scripts\\python scripts/fetch_tools.py            # 缺失或哈希不符才下载
    .venv\\Scripts\\python scripts/fetch_tools.py --check    # 只体检，不下载（CI/构建前用）
    .venv\\Scripts\\python scripts/fetch_tools.py --only aria2c

行为与规范一致：

* 只处理**清单里登记**的工具（未登记的一律不碰，不联网猜版本）；
* 下载后用清单哈希校验，不符即失败并删除下载物；
* 只解 zip（7z 官方无 zip，走随包内置）；
* 落位到 ``tools\\<工具名>\\``，先写临时文件再 replace。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


async def run(args: argparse.Namespace) -> int:
    from uup2iso.core.models.enums import ToolKind
    from uup2iso.infrastructure.process.runner import ProcessRunner
    from uup2iso.infrastructure.tools.fetcher import ToolFetcher, sha256_of
    from uup2iso.infrastructure.tools.lock import compute_identity, load_lock
    from uup2iso.infrastructure.tools.provider import ToolProvider

    lock_result = load_lock(TOOLS_DIR / "tools.lock.json")
    for warning in lock_result.warnings:
        print(f"[WARN] {warning}")
    lock = lock_result.lock
    if not lock.tools:
        print("[FAIL] 锁定清单为空，没有可自动获取的工具")
        return 1

    provider = ToolProvider(
        tools_dir=TOOLS_DIR,
        lock=lock,
        runner=ProcessRunner(),
        search_path=False,
        cache_path=ROOT / "config" / "tool_caps.json",
    )

    targets = (
        [kind for kind in ToolKind if kind.value in set(args.only)]
        if args.only
        else [ToolKind(item.name) for item in lock.tools]
    )

    updated = False
    problems: list[str] = []
    fetched = 0

    for kind in targets:
        entry = lock.entry_for(kind.value)
        if entry is None:
            print(f"[SKIP] {kind.value}：未登记在锁定清单")
            continue

        info = provider.locate(kind)
        # 注意：entry.sha256 是下载包哈希，不能拿来比 exe；exe 哈希看 executable_sha256
        exe_expected = entry.executable_sha256
        if info is not None and (not exe_expected or info.sha256 == exe_expected):
            print(f"[OK]   {kind.value} {entry.version} 已就位")
            continue

        state = "缺失" if info is None else "哈希不符"
        print(f"[NEED] {kind.value} {entry.version} {state}")
        if args.check:
            problems.append(f"{kind.value}（{state}）")
            continue

        fetcher = ToolFetcher(
            tools_dir=TOOLS_DIR,
            lock=lock,
            progress=lambda done, total: None,
        )
        try:
            outcome = await fetcher.fetch(kind)
        except Exception as exc:  # 交给上层统一报错
            print(f"[FAIL] {kind.value}: {exc}")
            problems.append(kind.value)
            continue

        exe = outcome.executable
        digest = sha256_of(exe) if exe else ""
        target = exe.parent if exe else "-"
        print(
            f"[DONE] {kind.value} {outcome.version} 下载 {_human(outcome.bytes_downloaded)}"
            f" → {target}（exe sha256={digest[:12]}…）"
        )
        fetched += 1

        if args.update_lock and exe is not None and digest:
            entry.executable_sha256 = digest
            updated = True

    if args.update_lock and updated:
        lock.identity_sha256 = compute_identity(lock)
        (TOOLS_DIR / "tools.lock.json").write_text(
            json.dumps(lock.model_dump(mode="json", by_alias=True), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"[WROTE] tools.lock.json（identity={lock.identity_sha256[:12]}…）")

    print()
    if args.check:
        if problems:
            print(f"结果: 需要下载 {len(problems)} 项 → {problems}")
            return 2
        print("结果: 全部就位")
        return 0

    if problems:
        print(f"结果: 失败 {len(problems)} 项 → {problems}")
        return 1
    print(f"结果: OK（本次获取 {fetched} 项）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按锁定清单获取必需工具")
    parser.add_argument("--check", action="store_true", help="只体检，不下载")
    parser.add_argument("--only", nargs="+", default=[], help="只处理指定工具名")
    parser.add_argument(
        "--update-lock",
        action="store_true",
        help="把落位后可执行文件的哈希回填进 tools.lock.json（发版/打包时用）",
    )
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
