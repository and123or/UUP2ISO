# UUP2ISO

把 UUP（Windows 更新下载包）转换成可引导 ISO，并支持精简与系统优化。
技术栈：**Python 3.12–3.13（x64）+ PySide6 + PySide6-Fluent-Widgets**，打包用 PyInstaller。

> 文档基线见 `docs/`（六类目录：需求 / 规范 / 设计 / 计划 / 记录 / 治理）。
> 需求与规范是权威来源，本文只讲「怎么把环境跑起来」。

## Python 版本

| 版本 | 状态 | 说明 |
| --- | --- | --- |
| 3.12 | 支持（下限） | 满足 `requires-python >=3.12` |
| **3.13** | **推荐** | 已用于本仓库 `.venv`（3.13.14），PySide6 有 wheel |
| 3.14 | **不支持** | 暂无 PySide6 wheel，`requires-python` 上限 `<3.14` |

`.python-version` 记录为 `3.13`（供 pyenv 等工具识别）；解释器路径以 `py -0p` 与 `where python` 实测为准。

## 快速开始（干净机器）

前置：Windows 10/11 x64，已安装 **Python 3.13**（3.14 暂无 PySide6 wheel，暂不支持）。

**方式一：一键脚本（联网自动装）**

```bat
git clone <repo> UUP2ISO && cd UUP2ISO
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
```

脚本会一次完成：创建 `.venv` → 升级 pip → `pip install -e ".[dev]"` → 环境自检 → lint/类型检查/测试 → 生成依赖快照。

**方式二：手动安装（依赖清单 + 固定版本）**

工作区已创建 `.venv`（3.13.14）。在仓库根目录按需执行其一：

```bat
:: A. 只装运行期依赖（跑程序）
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt

:: B. 装运行期 + 开发依赖，并把本包以可编辑方式装入（推荐，pytest 才能 import uup2iso）
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e ".[dev]"
```

> **坑位提示**：`pip install -r requirements-dev.txt` 只装**第三方依赖**，不会安装 `uup2iso` 本体，
> 结果 `import uup2iso` 失败、`env_check.py` 报 `package_importable` 失败。补一条即可（不产生新下载）：
>
> ```bat
> .venv\Scripts\python -m pip install -e . --no-deps
> ```

国内网络下载 PySide6（约 150 MB）较慢时可加镜像，仅影响传输、不改包内容：

```bat
.venv\Scripts\python -m pip install -r requirements-dev.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

装完自检：

```bat
.venv\Scripts\python scripts\env_check.py
.venv\Scripts\python -m pytest
```


**所有命令一律走 `.venv`，禁止使用系统 Python 主环境：**

```bat
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format --check .
.venv\Scripts\python -m mypy
.venv\Scripts\python scripts\env_check.py
.venv\Scripts\python -m uup2iso.cli.main version
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

运行 GUI 骨架（离屏亦可）：

```bat
.venv\Scripts\python -m uup2iso.gui.app
set QT_QPA_PLATFORM=offscreen && .venv\Scripts\python -m uup2iso.gui.app
```

## 目录结构

```
pyproject.toml            依赖 / 版本 / ruff / mypy / pytest 配置
requirements.txt          运行期依赖清单（手动安装用，与 pyproject 一致）
requirements-dev.txt      开发依赖清单（-r requirements.txt + 测试/检查/打包）
requirements.lock.txt     由 bootstrap 生成的依赖快照（审计用，不参与解析）
.python-version           推荐解释器版本（3.13）
src/uup2iso/
  core/                   领域模型与接口契约（models/pipeline/options/presets/abstractions/errors）
  application/            编排与用例（pipeline/task_session/settings/progress）
  infrastructure/         外部适配（source/download/imaging/optimize/iso/process/storage）
  gui/                    PySide6 + Fluent（app.py 组合根、views/presenters/controls/navigation/resources）
  cli/                    最小 CLI（run --config / --dry-run / --resume / --debug / version）
tests/                    单元 / 集成 / GUI 冒烟 + 分层与依赖门禁
scripts/                  bootstrap.ps1 / check.ps1 / env_check.py
docs/                     六类文档
```

依赖方向固定为 `gui`、`cli` → `application` → `core` ← `infrastructure`，
由 `tests/test_layering.py`（AST 扫描）与 `tests/test_dependencies.py`（UI 库越界检查）自动把关。

## 门禁

| 门禁 | 命令 | 失败含义 |
| --- | --- | --- |
| 环境一致性 | `scripts/env_check.py` | 不在 `.venv`、版本越界、依赖缺失、被主环境污染 |
| 分层依赖 | `pytest tests/test_layering.py` | 跨层非法导入（如 core → gui） |
| UI 隔离 | `pytest tests/test_dependencies.py` | UI 库出现在 gui 之外，或 PyQt6 与 PySide6 并存 |
| 版本三处同步 | `pytest tests/test_version.py` | `pyproject.toml` / `__init__.py` / 断言不一致 |

CI 见 `.github/workflows/ci.yml`，同样先建 `.venv` 再执行上述命令。

## 打包与分发（属 WP11，尚未实施）

当前仓库只到工程基线（M0）。正式交付时：

- **便携绿色包**：PyInstaller 单目录（`--uac-admin` 提权清单），zip 解压即用，不写注册表、不写 `%LocalAppData%`。
- **安装包**：Inno Setup / NSIS 生成安装向导 + 桌面快捷方式 + 卸载入口。

两者均以 `.venv` 内的 `pyinstaller` 执行，例如：

```bat
.venv\Scripts\python -m PyInstaller --noconfirm --windowed --uac-admin ^
  --name UUP2ISO --paths src src\uup2iso\gui\app.py
```

> 注意：PyInstaller 单文件/单目录产物常被 Windows Defender 或国产杀软**误报**。
> 处置方式：提交微软误报申诉、对 exe 做代码签名（EV 证书最佳）、企业环境加白名单。
> 体积增大与冷启动变慢是既定取舍，靠 `--exclude-module` 精简依赖缓解。

## 已知限制

- 仅支持 Windows 10/11 x64；Python 3.14 需等 PySide6 提供 wheel。
- 当前版本 `0.1.19` 为 M1 起点（WS1）：WS0 底座（配置/进程/工具/日志/单实例）已就绪，编排主链路按 WP 顺序实现。
- GUI 仅骨架窗口，无导航与向导（WP9-T1 起）。
