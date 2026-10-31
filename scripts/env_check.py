"""环境一致性自检（项目计划 WP0-T2a）。

必须在工作空间 ``.venv`` 内执行：

    .venv\\Scripts\\python scripts\\env_check.py

校验项：

1. 当前解释器就是 ``<工作空间>\\.venv\\Scripts\\python.exe``（不是系统主环境）；
2. 解释器版本满足 ``requires-python``（3.12–3.13）；
3. ``pyproject.toml`` 声明的依赖全部可解析；
4. ``sys.path`` 中不含系统级 site-packages（未被主环境污染）；
5. 发行版已以可编辑方式安装（``uup2iso`` 可导入）。

退出码：0 全部通过；1 存在失败项。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tomllib
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
PYPROJECT = WORKSPACE / "pyproject.toml"
VENV_PYTHON = WORKSPACE / ".venv" / "Scripts" / "python.exe"
SUPPORTED = {(3, 12), (3, 13)}

DIST_TO_MODULE = {
    "PySide6": "PySide6",
    "PySide6-Fluent-Widgets": "qfluentwidgets",
    "httpx": "httpx",
    "pydantic": "pydantic",
    "typer": "typer",
    "jsonschema": "jsonschema",
    "lxml": "lxml",
    "pytest": "pytest",
    "pytest-qt": "pytestqt",
    "dirty-equals": "dirty_equals",
    "ruff": "ruff",
    "mypy": "mypy",
    "pyinstaller": "PyInstaller",
}


def _dist_name(spec: str) -> str:
    for sep in ("[", ">", "<", "=", "!", "~", ";", " "):
        spec = spec.split(sep)[0]
    return spec.strip()


def _declared_distributions() -> list[str]:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    names = [_dist_name(spec) for spec in project["dependencies"]]
    for specs in project.get("optional-dependencies", {}).values():
        names.extend(_dist_name(spec) for spec in specs)
    return sorted(set(names))


def _leaked_site_packages() -> list[str]:
    """识别系统级 site-packages 泄漏进 sys.path 的情况。"""
    venv_root = WORKSPACE / ".venv"
    leaks = []
    for entry in sys.path:
        if not entry:
            continue
        path = Path(entry)
        if "site-packages" not in {part.lower() for part in path.parts}:
            continue
        if venv_root in path.parents or path.is_relative_to(venv_root):
            continue
        leaks.append(str(path))
    return leaks


def collect() -> dict[str, object]:
    checks: list[dict[str, object]] = []

    interpreter_ok = (
        VENV_PYTHON.is_file() and Path(sys.executable).resolve() == VENV_PYTHON.resolve()
    )
    checks.append(
        {
            "name": "interpreter_is_workspace_venv",
            "ok": interpreter_ok,
            "detail": str(sys.executable),
            "expected": str(VENV_PYTHON),
        }
    )

    version_ok = sys.version_info[:2] in SUPPORTED
    checks.append(
        {
            "name": "python_version_supported",
            "ok": version_ok,
            "detail": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "expected": ">=3.12,<3.14",
        }
    )

    missing = [
        dist
        for dist in _declared_distributions()
        if importlib.util.find_spec(DIST_TO_MODULE.get(dist, dist.replace("-", "_"))) is None
    ]
    checks.append(
        {"name": "declared_dependencies_installed", "ok": not missing, "missing": missing}
    )

    leaks = _leaked_site_packages()
    checks.append({"name": "no_system_site_packages", "ok": not leaks, "leaked": leaks})

    editable = importlib.util.find_spec("uup2iso") is not None
    checks.append({"name": "package_importable", "ok": editable})

    return {
        "workspace": str(WORKSPACE),
        "prefix": sys.prefix,
        "ok": all(bool(c["ok"]) for c in checks),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UUP2ISO 运行环境自检")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（CI 消费）")
    args = parser.parse_args(argv)

    report = collect()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"workspace : {report['workspace']}")
        print(f"prefix    : {report['prefix']}")
        for check in report["checks"]:
            mark = "PASS" if check["ok"] else "FAIL"
            extra = ""
            if check["name"] == "declared_dependencies_installed" and check["missing"]:
                extra = f" missing={check['missing']}"
            if check["name"] == "no_system_site_packages" and check["leaked"]:
                extra = f" leaked={check['leaked']}"
            if check["name"] in {"interpreter_is_workspace_venv", "python_version_supported"}:
                extra = f" detail={check['detail']}"
            print(f"[{mark}] {check['name']}{extra}")
        print(f"\n结果: {'OK' if report['ok'] else 'FAILED'}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
