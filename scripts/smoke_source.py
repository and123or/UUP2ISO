"""数据源联调冒烟（WP2-T1）。

真实访问 uupdump（或 `--base` 指定的镜像），打印构建/语言/edition/清单摘要。
**不进 CI**：CI 只跑离线夹具（`tests/fixtures/source/`）。

用法（仓库根目录、`.venv` 内）：

    .venv\\Scripts\\python scripts/smoke_source.py
    .venv\\Scripts\\python scripts/smoke_source.py --search 26100 --lang zh-cn --edition professional
    .venv\\Scripts\\python scripts/smoke_source.py --build <uuid>
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from uup2iso.application.fetch import ManifestRequest, build_manifest, write_manifest
from uup2iso.core.errors import Uup2IsoError
from uup2iso.infrastructure.source import SITE_BASE_DEFAULT, UupDumpSource


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="uupdump 数据源联调冒烟")
    parser.add_argument("--base", default=SITE_BASE_DEFAULT, help="站点基址（可换镜像）")
    parser.add_argument("--search", default="26100", help="构建搜索关键字")
    parser.add_argument("--build", default="", help="直接指定构建 uuid（跳过搜索）")
    parser.add_argument("--lang", default="zh-cn")
    parser.add_argument("--edition", default="professional")
    parser.add_argument("--manifest-dir", default="", help="同时生成 manifest.json 到该目录")
    args = parser.parse_args(argv)

    source = UupDumpSource(base_url=args.base)
    try:
        if args.build:
            build_id = args.build
        else:
            builds = source.list_builds(search=args.search)
            print(f"[OK] 构建 {len(builds)} 个（search={args.search}）")
            clients = [item for item in builds if (item.title or "").startswith("Windows 1")]
            for item in (clients or builds)[:5]:
                print(
                    f"     {item.build:<14} {item.arch.value:<6} {item.title or '-'}  id={item.id[:8]}"
                )
            if not clients:
                print("[WARN] 没有客户端构建，改用第一条（可能是服务端更新，无语言列表）")
            build_id = (clients or builds)[0].id

        langs = source.list_languages(build_id)
        print(f"[OK] 语言 {len(langs)} 个：{', '.join(langs[:8])} …")

        editions = source.list_editions(build_id, args.lang)
        print(
            f"[OK] edition {len(editions)} 个："
            + ", ".join(f"{e.code}({e.display_name})" for e in editions)
        )

        payload = source.get_files(build_id, language=args.lang, edition=args.edition)
        expires = payload.expires_at
        left = "-"
        if expires is not None:
            hours = (expires - datetime.now(UTC)).total_seconds() / 3600
            left = f"{hours:.1f} 小时后过期"
        print(
            f"[OK] 清单 {len(payload.files)} 个文件 / {payload.total_bytes / 1e9:.2f} GB"
            f"（build={payload.build} arch={payload.arch}，URL {left}）"
        )
        for item in payload.files[:3]:
            print(f"     {item.name:<60} {item.size / 1e6:8.1f} MB  sha1={item.sha1[:8]}")
        if payload.unknown_fields:
            print(f"[WARN] 未识别字段：{payload.unknown_fields}")
        for warning in payload.warnings[:5]:
            print(f"[WARN] {warning}")
        manifest, warnings = build_manifest(
            payload,
            ManifestRequest(
                build=payload.build,
                language=args.lang,
                edition=args.edition,
                base_url=args.base,
                fetched_at=datetime.now(UTC),
            ),
        )
        print(
            f"[OK] 清单校验通过：{len(manifest.files)} 项 / "
            f"{manifest.total_bytes / 1e9:.2f} GB（生成告警 {len(warnings)} 条见下）"
        )
        for warning in warnings:
            print(f"[WARN] {warning}")
        if args.manifest_dir:
            path = write_manifest(Path(args.manifest_dir) / "manifest.json", manifest)
            print(f"[OK] 已写出 {path}")
        if source.warnings:
            print(f"[INFO] 累计告警 {len(source.warnings)} 条（示例：{source.warnings[0]}）")
        return 0
    except Uup2IsoError as exc:
        print(f"[FAIL] {exc.code.value}: {exc.message}")
        return 2
    finally:
        source.close()


if __name__ == "__main__":
    sys.exit(main())
