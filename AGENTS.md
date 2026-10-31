# AGENTS.md — 自动化代理强制约定

本文件对**所有**在本仓库工作的代理（含 AI Agent、脚本、CI）生效，与
`docs/治理/版本与变更管理.md` §2、§6 一致。冲突时以 `docs/` 为准。

## 1. 运行环境（不可绕过）

1. 一切命令在**工作空间根目录 `.venv`** 内执行：`.venv\Scripts\python ...`。
2. 禁止系统 Python 主环境运行、禁止不带 `.venv` 前缀的全局 `pip install`。
3. 环境不存在时先执行 `scripts/bootstrap.ps1`，不要手工散装安装依赖。
4. `tests/conftest.py` 会校验解释器身份，非 `.venv` 直接 `UsageError`。
   仅在排障时可临时 `UUP2ISO_SKIP_VENV_GUARD=1`，且不得作为提交结果。

## 2. 每次修改的固定顺序

1. 递增版本号，三处同改：`pyproject.toml` `[project] version`、
   `src/uup2iso/__init__.py` `__version__`、`tests/test_version.py` `EXPECTED_VERSION`。
2. 在 `CHANGELOG.md` **顶部**新增记录（新增 / 变更 / 修复 / 已知限制）。
3. 在 `.venv` 内跑门禁：`powershell -ExecutionPolicy Bypass -File scripts\check.ps1`。

## 3. 架构红线

- 依赖方向：`gui`、`cli` → `application` → `core` ← `infrastructure`。
- `core` 不得导入任何外层；`infrastructure` 不得导入 `application`/`gui`/`cli`。
- PySide6 / `qfluentwidgets` **只允许**出现在 `src/uup2iso/gui/` 内。
- 只使用 **PySide6** 系绑定；不得引入 PyQt6 或 `PyQt-Fluent-Widgets`（会与 PySide6 冲突）。
- 外部进程一律经 `ProcessRunner`（WP0-T5），禁止模块内直接 `subprocess`。

## 4. 文档优先级

实现与 `docs/` 冲突时，**先改文档再改代码**（`docs/治理/术语表与文档映射.md` §1.3），
并在 CHANGELOG 记录。已登记的偏差：Fluent 库使用 `PySide6-Fluent-Widgets`、
`requires-python` 上限为 `<3.14`。

## 5. 禁止事项

- 不提交 `.venv/`、`logs/`、`build/`、`dist/`、`requirements.lock.txt` 之外的产物；
- 不把密钥/凭据写入仓库或日志；
- 不实现任何激活/授权绕过逻辑。
