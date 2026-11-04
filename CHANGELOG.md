# CHANGELOG

## 0.1.49 — 修「交付了错误 ISO」+ 支持集成后的产物名

> 用户反馈：进程已跑完、产物在 `output`，但命名仍有 `unknown`。
> 复盘日志 `run_20261104-232018.log`（用最新分类器过滤进度后）发现**两个真问题**。

### 问题 1（严重）：多份 ISO 时选错了产物
`work\c14e0cad\convert\` 里有**两份** ISO：

```
4.3 GB  22:33  26100.1.240331-1435.GE_RELEASE_CLIENTPRO_OEMRET_X64FRE_ZH-CN.ISO   ← 未集成（旧）
8.9 GB  00:07  26300.9818.261025-0959.26H2_GE_RELEASE_SVC_PROD3_CLIENTPRO_...      ← 集成后（新）
```

`_locate_source` 用 `_first()`（`rglob` 排序取第一个）→ 按名字 `26100` < `26300` 选中**旧的那份**，
于是把 4.3 GB 的未集成镜像当成交付物，用户花 47 分钟集成的 8.9 GB 成果被忽略。

**修复**：新增 `_newest_with_count()`，按 **mtime 取最新**；候选 >1 时记 `iso_source_selected`。

### 问题 2：集成后的产物名解析不了（两个 unknown）
集成后的命名多了版本族标签和下划线变体：
`26300.9818.261025-0959.26H2_GE_RELEASE_SVC_PROD3_CLIENTPRO_OEMRET_X64FRE_ZH-CN`
旧正则要求 `<tag>_<type>_<edition>` 且 type 无下划线 → 匹配失败 → `{edition}`/`{arch}` 双双 unknown。

**修复**：正则改为**右锚定**（`channel/arch/lang` 固定在尾部，向左回溯切 edition，其余归 prefix），
再从前缀提取可选版本族（`26H2`）、tag、发布类型（`RELEASE_SVC_PROD3`）。

### 顺带修 readme
| 字段 | 之前 | 现在 |
| --- | --- | --- |
| `edition` | `-`（只读 version，未填充） | `professional`（取产物名解析值） |
| `版本族` | `26H2`（数据源）与产物版本矛盾 | 拆成 `版本族（产物）` + `版本族（数据源）`，前者按产物 build 反查 |

### 修复后的真实产物
```
output\Win11_26300.9818_professional_x64_zh-cn.iso        9,543,989,248 字节（8.9 GiB）
  .sha256 / .readme.txt
readme：产物版本 26300.9818 ｜ 镜像基线 26300.9818.261025-0959 ｜
        版本族（产物）26H2 ｜ edition professional ｜ 语言 zh-cn
```

### 日志分析结果（用最新分类器过滤进度后）
```
run_20261104-232018.log：2881 行
  转换器输出 2863 行 → progress 2636（92%，本不该落盘）｜ line 226 ｜ result 1
  框架日志 18 行：startup → task_started → 各阶段 start/finish → task_finished（干净）
  阶段耗时：Prepare 0.6s ｜ Convert 2817.0s（集成 LCU）｜ Output 9.2s
```
修复前那 2636 行进度会被采集成 `sampled=true`，但仍占 92% 篇幅；0.1.48 之后只保留 227 行有效信息。

### 测试
- `test_image_name.py`（7）：新增「集成后命名（含 `26H2` 族标签 + `RELEASE_SVC_PROD3`）」。
- `test_output_handler.py`（26）：新增「多份 ISO 取最新（而不是按名字排序）」。
- 全量：`pytest 全绿`；`mypy 124 files`；`ruff`/`format` 全绿。

### 已知限制
- 多产物目前按 mtime 取最新；若用户想要"指定某一份"，需要后续加显式输入选择。
- 旧的 4.3 GB 产物（`professional_26100.1_x64_zh-cn.iso`）仍在 `output\`，确认后可删。

## 0.1.48 — 进度不再写入日志文件（日志 §5.2）

> 用户指出：规范明确「转换进度不得写入日志」，但日志文件里仍有大量进度条目。

### 根因（三层，缺一都会漏）
| # | 位置 | 问题 |
| --- | --- | --- |
| 1 | `convert.py::_relay` | `if kind == "progress"` 分支只做了 `progress.report(...)`，**没有 return**，随后无条件 `sink.info(...)` → 进度行照样落盘。这是主因 |
| 2 | `classifier.py` | `DEFAULT_PATTERN_SOURCE` **没有 `ToolKind.CONVERTER`**，转换器的 `48% complete`、`Archiving file data: … (16%) done` 全被归为 `line`；`OSCDIMG` 的正则 `^\s*\d+%` 也匹配不到 `62% complete` |
| 3 | `LogSink` | 没有兜底：上游若把进度按 `info` 传来，照写不误 |

### 修复
- `_relay`：`progress` → `ProgressHub.report()`（GUI）+ `LogSink.progress()`（CLI stderr 渲染）后**立即返回**，不落盘。
- `LogSink.progress()`：只走 mirror（控制台），**绝不写文件**；`log()` 增加兜底——字段 `kind=progress` 一律转 `progress()` 并返回。
- 分类器新增 `ToolKind.CONVERTER` 的进度/结果正则，并补 `OSCDIMG` 的 `N% complete`。
  结果正则只收 `Finished.` / `Done.` / `=== Done ===`，其余仍走普通行（可定位性优先）。

### 验证（真实工具 + 旧日志重放）
```
真实 oscdimg 打 ISO：日志 13 行，全部是抬头/摘要/结果；进度行 0 行（控制台镜像里仍可见）
旧日志 run_20261104-222955.log 重放 278 行 converter 输出：
  判为 progress（不再落盘）174 行（62%）
  仍落盘 104 行（配置、KB 版本、组件名、Finished.）
  其中含百分比的仅 1 行：`Storage optimization saved 67 files, … (1% of image)`（结果摘要，允许）
```

### 规范澄清（日志 §5.2 / §9.4）
「不含任何百分比」改为「不含**刷新型**进度」：判据是「同一内容是否随进度反复重写」，
不是「是否出现 `%`」。一次性摘要（`saved … (1% of image)`、`image file is … bytes`）允许落盘。

### 测试
- `test_process_classify.py`（26）：新增 8 条转换器进度行 + 6 条非进度行 + 1 条「ERROR 含百分比不得丢」。
- `test_convert_handler.py`（28）：新增「进度行不落盘而普通/结果行落盘」「`kind=progress` 兜底不落盘」。
- `test_logging_sink.py`：`progress()` 签名兼容 `percent`（GUI 用）。
- 全量：`pytest 全绿`；`mypy 124 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `日志` v0.7 → **v0.8**：§5.2 增加边界澄清、§9.4 验收措辞同步。

## 0.1.47 — 产物名描述「装出来的系统」（默认模板 + build 语义调整）

> 用户决策：数据源是 26H2（26300.9818），但**未集成更新**，所以产出其实是 24H2（26100.1）。
> 转换器给的名称更权威——产物名应说明「装完是什么版本」，不是「数据源是什么版本」。

### 变更
- **默认 `nameTemplate`**：`{edition}_{build}_{arch}_{lang}` → **`Win11_{build}_{edition}_{arch}_{lang}`**
  （`config.py` 与 `taskconfig.schema.json` 同步再生）。
- **`{build}` 语义改为「产物版本」**：取镜像基线 `build.rev`（`26100.1`），
  来源优先级 = 产物名解析 → 版本判定 → 任务配置。
- **新增 `{sourceBuild}`**：保留数据源构建号（`26300.9818`），需要区分时才用。
- 伴生 `readme` 拆成三行：`产物版本` / `数据源构建` / `镜像基线`。

### 效果（真实样本，数据源 26300.9818 + integrate=false）
```
旧：professional_26300.9818_unknown_zh-cn.iso
新：Win11_26100.1_professional_x64_zh-cn.iso
readme：产物版本 26100.1 ｜ 数据源构建 26300.9818 ｜ 镜像基线 26100.1.240331-1435
```
（开 `updates.integrate=true` 后 `{build}` 会变成集成后的版本，产物名自动跟随。）

### 测试
- `test_image_name.py`：命名联动断言改为「以 `Win11_26100.1_` 开头」。
- `test_output_handler.py`：新增「默认模板」断言。
- 全量：`pytest 全绿`；`mypy 124 files`；`ruff`/`format` 全绿；schema 无漂移。

### 变更（文档）
- `输出` v0.6 → **v0.7**：§5.1 明确「产物名描述装出来的系统」，并给出 5 个变量的取值来源表。

## 0.1.46 — 从产物文件名提取镜像信息（消除 unknown）

> 用户提问：`26100.1.240331-1435.GE_RELEASE_CLIENTPRO_OEMRET_X64FRE_ZH-CN.ISO` 这名字怎么来的？
> 能不能直接用 / 提取信息，避免 `unknown`？

### 文件名结构与来源
转换器（`convert-UUP.cmd:1374-1400` 的映射表）按
`<build>.<rev>.<date>-<time>.<tag>_<TYPE>_<EDITION>_<CHANNEL>_<ARCH>FRE_<LANG>` 拼接：

| 段 | 示例 | 含义 |
| --- | --- | --- |
| build.rev | `26100.1` | **镜像基线**版本（未集成 LCU 时等于原始 build） |
| date-time | `240331-1435` | 编译日期时间 |
| tag_TYPE | `GE_RELEASE` | 发布标记/类型 |
| EDITION | `CLIENTPRO` | 版本（映射：CLIENTPRO/PRO→professional、CORE→home…） |
| CHANNEL | `OEMRET` / `VOL` | 零售 / 批量授权 |
| ARCH | `X64` / `X86` / `ARM64` | 架构 |
| LANG | `ZH-CN` | 语言 |

### 新增
- `infrastructure/input/image_name.py`：`parse_converter_iso_name()` → `ImageNameInfo`
  （build/revision/date/tag/release_type/edition_token/edition/channel/arch/language/baseline）。
  保守映射：认不得的 token **原样小写保留**（宁可 `clientprox`，也不退回 `unknown`）。
- `Output._compose_name()` 取值优先级改为：版本判定 → 配置 → **产物名解析** → `unknown`。
- 伴生 `readme` 增加「镜像基线」一行（`26100.1.240331-1435`）。

### 效果（真实样本）
```
旧：professional_26300.9818_unknown_zh-cn.iso
新：professional_26300.9818_x64_zh-cn.iso
readme：构建 26300.9818 ｜ 镜像基线 26100.1.240331-1435
```

### 为什么不直接用文件名当产物名
- 它是**镜像基线**（26100），而数据源是 26300.9818；直接用会让用户误以为下的是 24H2。
- 它带 `OEMRET`/`RELEASE` 等渠道标记，属于 UUP 转换语义，不适合当交付文件名。
- 结论：**提取字段用于命名与 readme，但不整段照抄**。

### 测试
- `test_image_name.py`（6）：真实样本解析、VOL 渠道、HOME 映射、未知 token 保留、
  非转换器命名返回 None、与 `Output` 命名联动（断言不再出现 `unknown`）。
- 全量：`pytest 全绿`；`mypy 124 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `输出` v0.5 → **v0.6**：§5.1 增加「变量取值优先级」与产物名解析说明。

## 0.1.45 — 修 full 模式误判 + ISO 校验依赖

> 用户跑 `task-26300-iso.json`（`convert.mode=full`）：转换器**成功产出了 4.3 GB ISO**，
> 但我们把阶段判成了失败。

### 修复
| # | 问题 | 根因 | 处理 |
| --- | --- | --- | --- |
| 1 | `full` 模式报 `INPUT_DAMAGED：skeleton 子步骤缺少 ISO 骨架目录` | `AutoStart=1/2` 时转换器把 `ISOFOLDER` 打成 ISO 后**删除骨架**（`convert-UUP.cmd:709`），而校验只认目录 | `skeleton`/`bootwim` 改为「有 ISOFOLDER **或** 已产出 ISO」都算合格 |
| 2 | ISO 校验 `L2 失败：7z 无法列出 ISO（exit=2）` | `tools\7zr.exe` 是 7-Zip **精简版**，不支持 ISO/UDF | L2 只认完整版 `7z.exe`/`7za.exe`；只有 `7zr` 时降级跳过（L1 签名仍然保留）；另外在**打包前**校验骨架里有 `setup.exe` 与 `sources\install.*`（文件系统层面，可靠） |

### 真实结果（同一份数据）
```
转换器日志：After optimization, image file is 4544223232 bytes / Finished.
产物：output/professional_26300.9818_unknown_zh-cn.iso
      4,544,223,232 字节（4.23 GB），CD001 签名 ✓，SHA256 cae764c4…，伴生 .sha256 + .readme.txt
```

### 需要用户知道的一点
ISO 内部文件名是 `26100.1.240331-1435.GE_RELEASE_CLIENTPRO_OEMRET_X64FRE_ZH-CN.ISO` ——
因为示例配置 `convert.updates.integrate=false`（不集成 4.6 GB 的 `KB5127936`），
`install.wim` 仍是 **26100 基线**（24H2）。要得到 26300（26H2）需要开 `AddUpdates=1`，
集成耗时会明显变长。

### 测试
- `test_output_handler.py`（25）通过；`test_convert_handler.py`（26）通过。
- 全量：`pytest 全绿`；`mypy 123 files`；`ruff`/`format` 全绿。

### 已知限制
- 没有完整版 `7z.exe` 时，ISO 的 L2 退化为「打包前骨架检查 + L1 签名」；
  想更严可把 `7z.exe` 放进 `tools\`。
- `.gitignore` 增加 `output/`（默认输出目录不入库）。

## 0.1.44 — 修 UUP 目录统计漏计（Apps 子目录）

> 现象：同一份数据，`Prepare` 输出 `99 个文件`，实际是 2947 个。
> 原因：转换器把 2848 个 appx 收进了 `UUPs26300.9818/Apps/<分类>/`，
> 而 `uup_folder_evidence` 只统计**根目录直接子文件**。

### 修复
- `_classify_uup_folder` 改为**递归统计**（`rglob`）：
  `files_count` / `total_bytes` / `appx_count` 现在覆盖 `Apps/` 等子目录。
- 实测：`2947 文件 / 2797 appx / 10.39 GB`，耗时 0.21 s（可接受）。

### 测试
- `test_input_sniff.py`（23）：新增「appx 在 `Apps/<分类>/` 时也要计入」。

### 已知限制
- 递归统计对超大目录（>10 万文件）会变慢；当前样本 2947 文件 0.21 s，暂不优化。

## 0.1.43 — 挂载残留防护 + ISO 输出（P2-2）

> 两件事：① 用户要求的 `D:/MountUUP` 类残留防护；② 把 ISO 输出做完（`oscdimg` 已就位）。

### 新增：挂载残留防护（`infrastructure/wim/mount_guard.py`）
- `residual_mount_dirs()`：扫描各盘根下的 `MountUUP`，得出**运行前基线**；
- `cleanup_new_mounts(baseline)`：结束后只清理**本次新增**的挂载点
  （`dism /Unmount-Image /Discard` → `rmdir`），失败只 WARN；
- Convert 在调用转换器前后使用：基线里已有的挂载点**一律不碰**（可能是用户自己的），
  新增的才清；清理异常绝不掩盖原始异常。
- 依据：`convert-UUP.cmd:221/4713` 把 scratch 设为 `%_drv%\W10UIuup`、挂载点设为 `%_drv%\MountUUP`，
  中断时走不到卸载段（实测 2026-11-02 的残留即如此）。

### 新增：ISO 输出（`OutputStageHandler`）
- 来源优先级：① 转换器自产的 `convert/**.iso`（`AutoStart=1/2`）→ 直接复制；
  ② `convert/ISOFOLDER` → 调 `oscdimg` 打包。
- `oscdimg` 参数：`-b<etfsboot.com> -u2 -yo<efisys.bin> -m -o -h -l<卷标> <folder> <staging>`，
  **所有路径绝对化**（进程 cwd 是任务工作目录，相对路径会解析错）。
- 缺引导文件 → `INPUT_DAMAGED`（不产半成品）；卷标取 `convert.volume_label`，空则 `UUP2ISO_<name>`（≤32）。
- 校验：L1 = 偏移 32769 的 `CD001` 签名 + 非空；L2 = `7zr l` 列出且含 `setup.exe` 与
  `sources\\install.wim|esd`（缺 7zr 降级 WARN）。
- 提交后缀按类型：`.iso` / `.esd` / `.wim`；`describe`/`file_operations` 同步展示 oscdimg 命令与 `.iso` 目标。

### 真实验证
```
oscdimg 2.56 + 4 KB 假引导文件 + 1 MB 假 install.wim
→ exit 0 "Done."，产物 1,734,656 字节，CD001 签名 ✓
```

### 测试
- `test_output_handler.py`（25）：新增 ISO 分支 4 项（无源提示、打包并校验签名、缺引导拒绝、
  `build_oscdimg_args` 形状），替换旧的「ISO 未实现」用例。
- `test_convert_handler.py`（26）：新增挂载防护 2 项（基线不碰、失败也清理）。
- 全量：`pytest 全绿`；`mypy 123 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `输出` v0.4 → **v0.5**：新增 §6.2 ISO 产出方式（来源优先级、oscdimg 参数、校验判据）。
- `现状与下一阶段计划` v1.6 → **v1.7**：P2-2 标 ✅；`术语表 §1.2` 同步。

### 已知限制
- ISO 输出**需要 `convert.mode=full`**（`AutoStart=1/2`）才能拿到 ISOFOLDER 或 ISO；
  当前示例配置是 `wim_only`，出 ISO 前要改。
- 挂载防护只认 `%_drv%\MountUUP` 这一种目录名（转换器当前命名）；转换器改版需同步。
- `oscdimg` 打包 3.5 GB 约 1–3 分钟，进度只在日志（stderr 的 `% complete` 被驱动捕获）。
- ISO 的 L2 只查关键文件存在，不校验卷标与引导扇区内容（L3 需挂载，未做）。

## 0.1.42 — 路径审计：清除写死路径与 CWD 依赖

> 用户要求「检查程序中是否还有其它写死的路径」。按 6 类模式全量扫描 `src/**/*.py` 并逐个修复。

### 修复（6 处）

| # | 位置 | 问题 | 处理 |
| --- | --- | --- | --- |
| 1 | `application/cli_runner.py`（3 处） | `Path(__file__).resolve().parents[3]` 推程序目录，**打包成 exe 后 `__file__` 在 exe 内部，parents[3] 无意义** → tools/ 定位会错 | 统一改用 `startup.program_dir()`（已处理 frozen） |
| 2 | `handlers/convert.py`（2 处） | `self._tools_dir or Path("tools")` —— 兜底依赖**当前工作目录** | 新增 `_default_tools_dir()` → `program_dir()/tools` |
| 3 | `handlers/output.py` | `base = tools_dir or Path("tools")` 同上 | 同上 |
| 4 | `orchestrator.py` | dry-run 兜底 `Path("work")/"dry-run"` 依赖 CWD | `_default_dry_run_dir()` → `program_dir()/work/dry-run` |
| 5 | `infrastructure/tools/provider.py` | `Path(os.environ.get("SystemRoot", r"C://Windows"))` —— **盘符写成 `C://`**（笔误） | 改为 `or r"C:////Windows"` |
| 6 | `infrastructure/rules/loader.py` | 开发期回退 `parents[4]/docs/...` 在打包后可能指向 `_MEIPASS` 上级 | 加 `if frozen: return None`，打包时只走「程序目录 rules → 包内 rules」 |

### 确认「无需改」的 3 处（审计结论）

| 位置 | 写法 | 为什么可以 |
| --- | --- | --- |
| `handlers/prepare.py` | `Path("input")/"probe.json"` | 相对的是 `ctx.work_dir`（`work_dir / _PROBE_RELATIVE`），不是 CWD |
| `application/elevation.py` | `cwd or Path.cwd()` | 提权重启时**应该**继承启动目录，属预期行为 |
| `infrastructure/version/catalog.py`、`schemas`、`tools.lock` | `Path(__file__).parents[2]` | 包内相对路径，随包分发，改不了也不需要改 |

### 测试
- `test_elevation.py`（16）：新增 4 项——`program_dir()` 开发期=仓库根、frozen 时=exe 目录、
  `tools` 兜底忽略 CWD、dry-run 目录忽略 CWD。
- 全量：`pytest 全绿`；`mypy 122 files`；`ruff`/`format` 全绿。

### 已知限制
- 打包成单文件（onefile）后 `sys.executable` 仍指向 exe，但 `__file__` 在 `_MEIPASS` 临时目录；
  以上修改已按此假设处理，**仍需在真实 PyInstaller 产物上验证一次**（WP11）。
- `scripts/*.py` 是开发脚本（手工运行），保留相对路径；不属分发代码。

## 0.1.41 — 输出目录默认落在程序目录（不再要求用户先建目录）

> 用户指出：输出不应在磁盘根创建文件夹，应该是程序目录或用户指定。

### 修复
- **`output.dir` 三种写法**（`输出 §5.0`）：
  - 空（默认）→ `<程序目录>\output`
  - 相对路径 → 相对程序目录
  - 绝对路径 → 原样
- 目录不存在**自动创建**（此前直接报 `CONFIG_SEMANTIC_INVALID`，用户得先手工建目录）；
  创建失败（无权限/只读）→ `PERMISSION_DENIED` 并提示改位置。
- `application/startup.py` 抽出 `program_dir()`（打包后=exe 目录，开发期=仓库根），
  `default_log_dir()` 与新增 `default_output_dir()` 共用同一语义。

### 测试
- `test_output_handler.py`（22）：新增「空 dir → 程序目录\output 且自动创建」「相对路径相对程序目录」
  「绝对路径原样」，替换原先的「缺目录报错」用例。
- 全量：`pytest 全绿`；`mypy 122 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `输出` v0.3 → **v0.4**：新增 §5.0 输出目录解析规则；`术语表 §1.2` 同步。

### 已知限制
- 默认目录在程序目录下：若程序装在 `Program Files`（不可写），会得到 `PERMISSION_DENIED`，
  需显式指定 `output.dir`——GUI 阶段会在设置页给出默认值提示。
- 之前生成的 `task-26300.json` 里是绝对路径 `D://UUP2ISO-Out`（属"用户指定"），仍按原样生效；
  想改用默认行为，把该字段清空即可。

## 0.1.40 — 修 Output 崩溃：日志字段撞名 + extras 不共享 + resume 假复用

> 排查结论：**不是意外中断**。日志显示 Convert 成功（3 分 25 秒，`Finished.`），
> 随后 Output 在 5.6 秒内以 `TypeError` 失败——产物其实已完整落到输出目录。

### 修复（四个）
| # | 问题 | 根因 | 处理 |
| --- | --- | --- | --- |
| 1 | Output 崩在 `TypeError` | `LogSink.log()` 的形参叫 `level`，Output 又传了业务字段 `level=2` → `got multiple values for argument 'level'` | 形参改名 `severity`；并给所有保留名（`level/stage/message/logger_name`）加 `field_` 前缀兜底，**以后同类撞名不再崩** |
| 2 | 同上（调用方） | Output 的校验级别字段叫 `level` | 改为 `verify_level`（语义也更清楚） |
| 3 | 产物名里 `edition` 是 `unknown` | 每个阶段的 `StageContext.extras` 都是**新建的空字典**，Prepare 解析的 version 传不到 Output | 编排器持有共享 `extras`，跨阶段复用 |
| 4 | `--resume` 只"判定"不"跳过" | `plan_resume()` 的结果只写进 JSON，编排器完全不知情 → 仍重跑 Convert（撞管理员前置） | 编排器新增 `reuse_stages`，命中记 `skipped reason=resumed` 并跳过 |

### 另外
- 阶段失败日志新增 `detail=<异常消息>`：此前只记 `reason=TypeError`，无法定位（这次就是靠它复现的）。

### 真实产物验证
```
install.wim 3.5 GB → 产物 professional_26300.9818_unknown_zh-cn.wim  3.71 GB
伴生：.wim.sha256（<hex> *<name>）+ .readme.txt
自算 SHA256 与伴生一致：d2254124…96d2
```

### 测试
- `test_logging_sink.py`（21）：新增「保留字段名不抛错且写成 `field_level=2`」。
- `test_pipeline_session.py`（14）：新增「extras 跨阶段共享」「`reuse_stages` 命中的阶段被跳过且记 `resumed`」。
- 全量：`pytest 全绿`；`mypy 122 files`；`ruff`/`format` 全绿。

### 已知限制
- `{arch}` 仍是 `unknown`（配置未声明架构；可从 UUP 文件名 `-amd64-` 推断，待做）。
- 复用阶段不校验产物是否还在（只信检查点）；产物被手删会在下游报"未找到 install.wim"。
- 上一次 `--resume` 把 Convert 记录覆盖成了 `failed`，因此下次 `--resume` 会重跑 Convert（3–4 分钟）。

## 0.1.39 — 自动 UAC 提权（非管理员不再直接失败）

> 用户提问：普通权限明明能弹 UAC，为什么要失败、能不能提权后继续？
> 结论：Windows 不允许**原地**提升当前进程权限，但可以**提权重启自己并等待**——本轮就这么做。

### 新增
- **`application/elevation.py`**
  - `is_admin()`：`IsUserAnAdmin()`。
  - `relaunch_elevated(argv, cwd, on_wait)`：用 `ShellExecuteExW(verb="runas")` 重启自身，
    **分片等待**（每 60 s 回调一次提示）+ `GetExitCodeProcess` **原样返回子进程退出码**；
    用户在 UAC 点「否」→ 返回 `ELEVATION_CANCELLED`（退出码 3）。
  - `_command_for()`：console-script/打包 exe 直接启动自己；源码运行则 `python -m uup2iso.cli.main`。
- **CLI `run` 自动提权**：非管理员且非 `--dry-run` → 提示后提权重启，父进程等待并透传退出码；
  `version` 与 `--dry-run` **不提权**；`UUP2ISO_NO_ELEVATE=1` 可跳过（CI/调试用）。
- 父窗口等待期间每 60 s 打印「…仍在管理员窗口中运行」，避免再次被误判卡死。

### 与「让转换器自己提权」的区别
| 方案 | 输出可见 | 退出码 | 用户体验 |
| --- | --- | --- | --- |
| 转换器自提权（`start ... -verb runas`） | ❌ 在我们看不见的新窗口 | ❌ 父进程拿到 0，误判成功 | 像卡死/假成功 |
| **本轮：提权重启自己** | ✅ 新窗口是我们的 CLI，有实时镜像 | ✅ 完整透传 | 一次 UAC，之后一切照旧 |

### 测试
- `test_elevation.py`（12）：`_command_for` 两条启动路径与含空格路径、`dry-run`/环境变量/管理员三种不提权、
  非管理员透传退出码、UAC 取消→3、提权失败→4、`run` 先提权不执行、`version` 不提权。
- `tests/conftest.py` 默认设 `UUP2ISO_NO_ELEVATE=1`，**测试进程永不真弹 UAC**。
- 全量：`pytest 871 passed`；`mypy 122 files`；`ruff`/`format` 全绿。

### 已知限制
- 提权后是**新控制台窗口**：父窗口只等待并透传码；要实时看进度请看新窗口（或看 `logs\run_*.log`）。
- GUI 尚未接自动提权（WP9 会复用同一 `relaunch_elevated`）。
- `run` 无条件要求管理员（即使入口 C 只做只读处理），偏保守；后续可按阶段裁剪。
- 打包成单文件 exe 后 `_command_for` 走 exe 分支，**需在真实打包产物上再验一次**。

## 0.1.38 — 修「长任务黑屏」：控制台实时镜像 + 失败原因可见

> 触发：用户用管理员终端跑 `run`，**转换器其实在跑**（日志里有 `Running UUP Converter v128m`、
> `Checking LCU MumX version`、`AutoStart 3`），但控制台**没有任何输出**，用户误判卡死并 Ctrl+C。

### 新增
- **控制台镜像（`LogSink.mirror`）**：CLI 把关键日志行**实时**打到 stderr：
  阶段事件（`stage_started/finished/skipped/retry`、`task_started/finished/cancelled`）、
  **转换器输出**（`event=converter`，逐条组件名）、以及所有 `WARN/ERROR`。
  其余 INFO 只落盘（避免刷屏）；镜像回调抛错不影响落盘。
- **计划先打印**：任务开始前把「入口/起点/各阶段 run-skip」打到 stderr，不再等结束才一次性输出。
- **失败原因可见**：阶段失败时镜像显示 `（原因码）`；结束时追加 `失败原因：<异常>`，JSON 也带 `error`。
- **抑制 Ctrl+C 噪音**：CLI 过滤 `ResourceWarning`（asyncio proactor "unclosed transport"）。

### 实测（非管理员重跑，验证提示与镜像）
```
任务 654843e0：入口 uupFolder，起点 Convert
Prepare    run (implied)  substeps=probe,selfcheck
Fetch      skipped  reason=input_satisfied
Convert    run  substeps=config,cab,aggregate,skeleton,bootwim
...
任务开始
[Prepare] 阶段开始
[Prepare] 阶段完成
[Fetch] 阶段跳过（input_satisfied）
[Convert] 阶段开始
```

### 测试
- `test_logging_sink.py`（20）：新增镜像 5 项（阶段事件可见、转换器输出可见、噪音不刷屏、
  可卸载、回调抛错不影响落盘）。
- 全量：`pytest 859 passed`；`mypy 121 files`；`ruff`/`format` 全绿。

### 已知限制
- 镜像只输出**关键行**：转换器若长时间无输出（单个大组件聚合），控制台仍会安静一阵，
  此时看 `logs\run_*.log` 尾部即可判断是否在跑。
- 没有 TUI 进度条；百分比进度要等 GUI 阶段接 `ProgressHub`。
- `ResourceWarning` 是**过滤**而非根治：Ctrl+C 时子进程管道仍未优雅关闭（不影响功能）。

## 0.1.37 — 仅映像模式放宽产物校验（修 0.1.36 之后仍会失败的一处）

> 背景：用户按 0.1.36 修复后重跑前，复盘日志发现 Convert 在 2.5 s 内以 `INPUT_DAMAGED` 失败——
> 解包其实成功、`convert-UUP.cmd` 与 `bin\` 都在，失败发生在**后续的 `skeleton` 子步骤**。

### 修复
- **`skeleton` / `bootwim` 按模式放宽**：`AutoStart=3/4`（仅出映像）时转换器可能不生成 `ISOFOLDER`
  与 `boot.wim`，此前一律要求存在 → 误判 `INPUT_DAMAGED`。现在：
  - 仅映像模式：缺失只记 `skeleton_absent` / `bootwim_absent` 事件（WARN 语义），**不失败**；
  - 出 ISO 模式（`AutoStart=1/2`）：仍然必须存在，否则 `INPUT_DAMAGED`。
  依据：`install.wim` 才是 `Output` 的唯一依赖，ISO 树只对 ISO 输出有意义。
- **修 `self._log` 用法错误**：`ConvertStageHandler._log` 是**注入的日志对象**而非方法，
  新增 `_emit(ctx, event, **fields)` 统一写事件（此前调用会 `TypeError: 'NoneType' not callable`）。

### 测试
- `test_convert_handler.py`（24）：新增「仅映像模式缺 ISOFOLDER 不失败」「出 ISO 模式缺 ISOFOLDER 必失败」。
- 全量：`pytest 854 passed`；`mypy 121 files`；`ruff`/`format` 全绿。

### 已知限制
- 仅映像模式下若转换器连 `install.wim` 都没产出，`Convert` 仍会"成功"（只是 `outcome["install_wim"]=None`），
  真正的失败会延迟到 `Output` 报"未找到 install.wim"。这是有意的分层，但日志里应同时看 `skeleton_absent`。

## 0.1.36 — 修复转换器集成：ini 顺序 / BOM / 管理员前置

> 触发：用户首次真跑 `run`，Convert 阶段立刻失败，并弹出一个停在交互菜单的管理员窗口。
> 依据：`docs/记录/转换路线验证.md` 新增「2026-11-04 转换器集成实测（v128m）」附节。

### 修复（三个真原因，缺一都不会成功）
| # | 现象 | 根因 | 处理 |
| --- | --- | --- | --- |
| 1 | 转换器进入交互菜单（`UUP -> ISO v128m`，等输入） | 子步骤顺序是 `config → cab`：写入的 `AutoStart=3` 被**解包自带的 `ConvertConfig.ini`（`AutoStart=0`）覆盖** | `run()` 改为**先解包再写 ini**（`config` 分支内先 `unpack_converter`，幂等） |
| 2 | 同上（潜在第二个坑） | ini 以 **UTF-8 BOM** 写入，转换器用 `findstr` 读首行 `[convert-UUP]` 时匹配失败 → 整份配置被忽略 | ini 改为**纯 ASCII、无 BOM**（键值全英文，无乱码风险） |
| 3 | 弹出另一个管理员 cmd 窗口，原进程失败 | 转换器**自提权**（`start cmd.exe ... -verb runas`），非管理员时它在新窗口重启自己，原进程随即退出 | 调用 `aggregate` 前检查 `IsUserAnAdmin()`，非管理员报 `PERMISSION_DENIED` 并提示"请以管理员身份运行" |

### 验证
```
包内默认：  [convert-UUP] / AutoStart    =0
写入之后：  [convert-UUP] / AutoStart    =3
有 BOM：    False
关键键：    AutoStart=3  AddUpdates=0  SkipISO=1  SkipApps=1
```

### 测试
- `test_convert_handler.py`（22）：`write_config_ini` 断言**无 BOM 且首行为段落名**；
  新增「非管理员 → `PERMISSION_DENIED`」「先解包后写 ini 不被包内默认覆盖」两项。
- 全量：`pytest 852 passed`；`mypy 121 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `转换路线验证.md`：追加「2026-11-04 转换器集成实测（v128m）」三条事实（不改动原结论）。
- `现状与下一阶段计划` v1.5 → **v1.6**：§4.4 补三条集成实测补丁；`术语表 §1.2` 同步。

### 已知限制
- **必须以管理员运行**（不只是 Convert 阶段）：CLI 自身不提权，需要用户从管理员终端启动；
  GUI 阶段会做成启动时检测并提示（UAC 清单已规划）。
- ini 只写 `[convert-UUP]` 与 `[Store_Apps]` 两段；`[create_virtual_editions]` 保持转换器默认。
- 转换器内部日志不落我们的 `logs\`（其输出走 stdout 被我们记录），独立日志文件未接。

## 0.1.35 — P1-2 补齐 W1 四缺口（检查点 / 心跳 / 取消 / 预览）

> 依据：`现状与下一阶段计划` §3.2 的 G1–G4；`编排 §4.1/§9`（检查点与续跑）、`进程 §7.1`（取消）。
> 验收：检查点随阶段落盘、心跳按 60 s 触发、Ctrl+C 退 3、各阶段 dry-run 都有文件操作。

### G1 编排器持有会话
- `PipelineOrchestrator(session=...)`：每个执行阶段 `begin_stage`、结束按结果 `end_stage`
  （`succeeded/failed/skipped/cancelled`，跳过带 `skip_reason`）；整条跑完 `session.finish(...)` 落终态。
- `StageContext.session`：处理器可拿会话登记产物（当前 Output 尚未调用，见已知限制）。
- CLI：`session.start()`（新任务）/ `load()+begin_resume()`（`--resume`）二选一，语义明确。

### G2 心跳
- `TaskSession.heartbeat()`：只刷新 `updatedAt` 并落盘。
- 编排器启动 60 s 循环（`heartbeat_interval` 可注入）；**没有会话就不启动**；结束 `cancel()`。

### G3 Ctrl+C 取消
- 新增 `application/cancel.py` 的 `SignalCancelToken`：实现 `is_cancelled()`，
  **第一次 Ctrl+C 只设标志**（编排器在阶段边界退出、保留中间产物），**第二次交还默认处理**（强退不被拦）。
- `installed()` 上下文管理器负责安装/恢复 SIGINT；非主线程装不上时静默退化为主动 `cancel()`。
- CLI 捕获 `KeyboardInterrupt` → 退出码 **3**、`result=cancelled`、检查点落 `cancelled`。

### G4 dry-run 文件操作
- Prepare / Convert / Output 都已提供 `file_operations()`；新增测试守着「三个阶段都不为空」，
  防止将来新处理器漏实现。

### 测试
- `test_pipeline_session.py`（11）：阶段 begin/end 序列（含 skipped 带原因）、失败落 `failed`、
  无会话可用、心跳按注入间隔触发、无会话不心跳、令牌标志与首次/二次行为、`installed()` 恢复原 handler、
  取消后立即退出（退出码 3 + `finish(cancelled)`）、三阶段 `file_operations` 非空。
- `test_cli_run.py` 增 2 项：真实 `execute_run` 后 `checkpoint.json` 有阶段记录；
  `KeyboardInterrupt` → 退出码 3 且检查点为 `cancelled`。
- 全量：`pytest 850 passed`；`mypy 121 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `现状与下一阶段计划` v1.4 → **v1.5**：G1–G4 全部标 ✅ 并写明实现；`术语表 §1.2` 同步。

### 已知限制
- **子步骤级检查点尚未接**：编排器只写阶段级；"子步骤完成即写"需要各处理器显式调用
  `session.end_substep(...)`，当前仅阶段边界可靠（强杀丢的是当前阶段进度，不丢已完成阶段）。
- **Output 未登记产物**：`session.record_artifact()` 已具备，但 Output 还没调，续跑的"产物复用"仍靠文件存在判定。
- **取消依赖处理器让出控制权**：编排器在阶段边界检查；长时 `await` 阶段（如大文件复制）不会即时中断。
- **GUI 未接**：本轮只做 CLI 路径；GUI 的取消按钮与心跳复用同一 session，留待 WP9。
- 真实 `Convert` 未跑过（需 UAC + 用户手工），因此"检查点在真实阶段内落盘"只在测试与 Prepare 上验证。

## 0.1.34 — P2-1 `Output` 阶段（先出 WIM，原子提交 + 伴生）

> 依据：`输出 §3`（唯一合法路径）、`§5`（重名）、`§6`（校验分级）、`§8`（伴生）。
> 验收：产物**先落暂存区**、校验通过才 `rename`；失败不产最终文件、保留暂存。

### 新增
- **`application/pipeline/handlers/output.py`**：`OutputStageHandler`（四子步骤）
  - `assemble`：定位 `install.wim`（ISO/ESD 类型分别报 `OUTPUT_TYPE_UNSUPPORTED`/走 esd 分支）；
    按 `{edition}_{build}_{arch}_{lang}` 渲染文件名（非法字符替换、连续下划线合并、≤200 字符）；
    **空间预检**（`size×1.05+16MB`）与**同卷检查**（`OUTPUT_CROSS_VOLUME`），失败**不创建暂存区**；
  - `package`：分块复制 + 同步算 SHA256 + `flush/fsync` 到 `.uup2iso-tmp\<task>\<name>.part`；
  - `verify`：L1（大小一致）→ L2（`wimlib info` 可解析 + `Image Count ≥1`）→ L3（可选 `wimlib verify`）；
  - `commit`：重名解析（`_1`..`_999`，之后时间戳）→ `Path.replace` 原子落位 → **重算 SHA256 复核** →
    写伴生（`.sha256` 用 `<hex> *<name>` 格式、`.readme.txt`）→ 清理本次暂存。
  - `describe()` / `file_operations()` 供 `--dry-run`（后者明确列出 `.part` 暂存，体现"不走最终名"）。
- 组合根：`build_handlers()` 对所有入口装配 `Output`。
- 错误码：登记 `OUTPUT_VERIFY_FAILED`（**先改文档** `输出 §6` 再改代码，符合 AGENTS §4）。

### 真实演练（wimlib capture 造的 1.6 KB 小 WIM，全链路真跑）
```
final:    professional_26300.9818_unknown_zh-cn.wim   size=1648  sha256=3c0c0ab8…
sidecars: .readme.txt + .wim.sha256（"3c0c0ab8… *professional_26300.9818_unknown_zh-cn.wim"）
暂存残留: 无（.uup2iso-tmp 已清空）
```

### 修的两个真问题（都是"跑起来"才发现的）
| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `'ProcessResult' object has no attribute 'stdout'` | `ProcessResult` 只留最后若干行，取不到完整输出 | 改用 `on_event` 收集全部行再解析 `Image Count` |
| 显式 `handlers={}` 仍走默认处理器 | `execute_run` 用 `handlers or build_handlers()`，空 dict 是 falsy | 改为 `if handlers is not None` |

### 测试
- `test_output_handler.py`（21）：模板渲染（变量/非法字符/下划线/超长/unknown）、重名 `_1`/`_2`/overwrite、
  缺 WIM、ISO 类型拒绝、缺输出目录、磁盘不足（且不建暂存）、端到端提交（含伴生与暂存清理）、
  二次运行加后缀、**L2 失败保留暂存且不产最终文件**、L3 调用顺序、L1 不调 wimlib、缺 runner、
  `describe` 两种情形、`file_operations` 含 `.part`、`Image Count` 解析。
- 另更新 `test_cli_run.py`：缺处理器用例改为显式空处理器（并新增「默认处理器覆盖四阶段」用例）。
- 全量：`pytest 836 passed`；`mypy 120 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `输出` v0.2 → **v0.3**：§6 登记 `OUTPUT_VERIFY_FAILED`。
- `现状与下一阶段计划` v1.3 → **v1.4**：新增 §4.5（Output 落地表 + 真实演练），P2-1 标 ✅；术语表同步。

### 已知限制
- **只支持 WIM**：ISO（P2-2，需 `oscdimg`）与 ESD/SWM 未实现，配置成这些类型会明确报错。
- **`{arch}` 是 `unknown`**：配置未声明架构时按规范填 `unknown`；从文件名（`-amd64-`）推断属后续优化。
- **L2 不校验索引名/压缩类型**：只查可解析与映像数；索引名唯一性与压缩类型比对留待后续。
- **不写 `.manifest.json`**：规范列为可选（用户勾选才生成），当前未接。
- 跨卷分支无单测（需真实另一块盘），靠同卷预检保证正常路径不触发。
- 续跑复用：暂存区在失败时保留，但**尚未接入启动时的残留检测与三选提示**（属 T3 的 G1 缺口）。

## 0.1.33 — P0-0 版本目录（GitHub 动态解析，代码零版本常量）

> 依据：`docs/计划/现状与下一阶段计划.md` §5；用户要求「以 GitHub 为可信来源、动态获取、不得硬编码」。
> 本轮**只做版本数据链路**，不改识别/转换行为（仅把结果写进 `probe.json`）。

### 新增
- **`infrastructure/version/catalog.py`**：`VersionCatalog` —— 三层来源（缓存 24h → GitHub → 随包快照），
  `resolve(family)` / `family_of(major)` / `resolve_build(build)` / `refresh()`；
  `parse_releases(text)` 只认**同一行**里 `NNH2` 与 5 位主版本号的配对（跨行不配、噪声数字排除、同族冲突取多数）。
- **`core/models/version.py`**：`WindowsRelease`、`VersionResolution`（含 `source`/`stale`/`warnings`）。
- **`application/input/version_hint.py`**：`build_from_directory()`（`UUPs26300.9818` → `26300.9818`）、
  `language_from_names()`（`professional_zh-cn.esd` → `zh-cn`）、`resolve_input_version()`（构建号 + 反查版本族）。
- **`src/uup2iso/data/windows_releases.json`**：随包快照（23H2/24H2/25H2/26H2），`pyproject` 的 package-data 已含 `data/*.json`。
- **`scripts/refresh_windows_releases.py`**：从配置的 GitHub 源刷新快照（默认不写盘，`--write` 才覆盖）。
- `Prepare` 写 `probe.json` 时附带 `version` 块（`文件 family/major/build/revision/language/source/stale`）。

### 实测结论：GitHub 没有稳定的官方机器可读版本表
| 候选 | 结果 |
| --- | --- |
| `MicrosoftDocs/windows-itpro-docs` | 404 |
| `MoralCode/WindowsVersions` | 404 |
| `uup-dump/api` | 404 |
| `AshleyHow/WindowsOSBuild` | 存在，但是爬 HTML 的脚本仓库，无静态版本表 |

→ 因此实现为「**可配置仓库 + 运行时发现路径 + 快照兜底**」，不做任何仓库名/路径硬编码；
未配置或抓取失败时用快照并标 `stale=true` + `version_catalog_offline`。

### 真实数据链路（无清单 UUP 目录）
```
目录名 UUPs26300.9818 → build=26300.9818, revision=9818
catalog.family_of(26300) → 26H2
文件名 professional_zh-cn.esd → language=zh-cn
probe.json.version = {family:26H2, major:26300, build:26300.9818, revision:9818,
                      language:zh-cn, source:"directory-name+snapshot", stale:true}
```

### 防回归
- `test_no_version_constants_in_code`：用 AST 收集**真正参与运算的数字字面量**（排除 docstring/注释），
  断言 `22631/26100/26200/26300` 不出现在 `src/**/*.py`。版本号只允许存在于 `data/*.json`。

### 测试
- `test_version_catalog.py`（23）：同行配对、跨行不配、多数取胜、噪声排除、多族解析、
  快照查询/反查/revision 拆分、未登记 major 告警、快照缺失与损坏降级、
  GitHub 解析与 `github:<repo>@<sha>` 来源记录、缓存命中不重复请求、缓存过期重取、
  404/不可解析回退快照、目录名解析、语言解析、端到端推断、无 build 时告警、AST 常量断言（4 个版本号）。
- 全量：`pytest 814 passed`；`mypy 119 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `输入` v0.4 → **v0.5**：§4 登记 `probe.json.version` 扩展块。
- `现状与下一阶段计划` v1.2 → **v1.3**：§5.5 补落地状态、实测结论与数据链路；P0-0 标 ✅；术语表同步。

### 已知限制
- **GitHub 侧仍无稳定源**：默认只能用快照；要"真正动态"必须由用户提供可解析的仓库路径（`--repo/--path`）。
- **快照尚未被 GitHub 数据校准**（`source.verified=false`）：26H2=26300 目前来自用户给定事实。
- 解析是**文本启发式**（同行配对）：表格跨度大、跨行书写会解析不到（记为 `version_source_unparsed`）。
- `edition` 仍为空：它来自 `wimlib info`（转换后才知道），不由版本目录推断。
- 版本目录尚未接入无人值守映射与 ISO 卷标（等 P3 阶段）。

## 0.1.32 — P0-1 无清单 UUP 识别 + P0-2 Prepare 自检换判据

> 依据：`docs/计划/现状与下一阶段计划.md` §4.2 的 P0-1/P0-2；实测样本 `UUPs26300.9818`（无清单）。

### P0-1 输入识别放宽（清单不再是必要条件）
- **`is_uup_folder` 改为结构判定**：清单存在且含归档 → 命中（原逻辑保留）；
  清单缺失时满足任一即命中：
  1. 存在 `*AggregatedMetadata.cab`（UUP dump 标志）；
  2. `.esd` ≥ **3** 且至少 1 个 `.cab`。
  两条都不满足仍判「不是 UUP 目录」——宁可拒绝、不可猜。
- 新增 `UupEvidence`（`uup_folder_evidence()`）：清单是否存在、AggregatedMetadata、esd/cab/msu/appx 计数、
  总字节、`.part/.aria2` 残留数、`complete_enough`。
- **UUP 目录内的 `.wim`/`.esd` 不再作为独立候选**：否则 `Edge.wim` 会让输入变成 `INPUT_AMBIGUOUS`；
  只有目录里真存在 `.iso` 时才判歧义。
- `probe.json` 新增扩展字段 `uup{manifestMissing, hasAggregatedMetadata, files, esd, cab, msu, appx,
  totalBytes, partials}`；命中但无清单时记 `WARN uup_manifest_missing`，有残留记 `uup_partial_files`。

### P0-2 Prepare 自检换判据 + 内置实现
- 新增 `BuiltinPrepareOps`（`Prepare` 默认实现，不再要求外部注入）：
  - `probe` 复用 `InputProber`；
  - `selfcheck` 按入口分支：**UUP 入口改为结构自检**（≥1 个 `.esd`、无 `.part/.aria2` 残留、目录非空），
    通过时返回统计（`2895 个文件（19 个 .esd / 76 个 .cab / 2797 个 appx，10.39 GB）`）；
    空目录、无 `.esd`、有残留分别给出具体拒绝原因；
  - `materialize`/`extract` **明确报「引擎层尚未接线」**（不静默成功，避免误以为已解包）。
- `Prepare.describe()` 按入口分支：UUP 入口输出「结构自检 + 写 probe.json」，不再给假的 `image/install.wim` 命令。

### 真实数据验证（`UUPs26300.9818`，只读）
```
识别：candidates = [uup_folder]（此前会因 Edge.wim 判成歧义）
统计：2895 文件 / 19 .esd / 76 .cab / 2 .msu / 2797 appx / 10.39 GB / 0 残留 / 无清单
自检：(True, "2895 个文件（19 个 .esd / 76 个 .cab / 2797 个 appx，10.39 GB）；无清单文件，按结构特征识别")
probe.json：type=uup_folder，warnings=[editions_pending_convert, uup_manifest_missing]
```

### 测试
- 新增/调整 24 项：无清单 + AggregatedMetadata 命中、无清单需 ≥3 ESD、单 ESD 不命中、
  evidence 计数与残留、组件不成为候选、混入 ISO 仍歧义、probe 的 `uup` 块与告警、
  Prepare 内置自检（通过/空目录/有残留）、内置 ops 拒绝未接线的物化与解包。
- 全量：`pytest 786 passed`；`mypy 114 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `输入` v0.3 → **v0.4**：§2.2 补「清单缺失时的判定」与「组件不是候选」；
  §4 登记 `probe.json` 扩展字段 `uup`；`术语表 §1.2` 同步。
- `现状与下一阶段计划` v1.1 → **v1.2**：P0-1/P0-2 标记为 ✅。

### 已知限制
- **`.msu`/`.msix` 未做完整性校验**：自检只看数量与残留，不校验内容（清单缺失时无从比对哈希）。
- **无清单时无法判断构建号/语言/edition**：目前只能从文件名推断（如 `professional_zh-cn.esd`），
  版本目录（P0-0）尚未实现，`probe.json` 的 build/language 字段仍为空。
- `BuiltinPrepareOps.extract/materialize` 未实现：ISO 解包与 ESD→WIM 仍待引擎层。
- 无清单目录的识别阈值（≥3 个 `.esd`）是经验值，未在大样本上统计过误判率。

## 0.1.31 — WP3 前哨：`Convert` 阶段编排（仅预演）+ 入口 B 计划修正

> 依据：`docs/规范/映像引擎与转换规范.md`（路线 A）、`docs/记录/转换路线验证.md`（实测结论）、
> 用户放入 `tools\` 的 `uup-converter-wimlib`（`convert-UUP.cmd` + `ConvertConfig.ini` + 7z 包）。
> 本轮**只做代码与预演，不实际执行转换**（实跑由用户手工进行）。

### 新增
- **`application/pipeline/handlers/convert.py`**：`ConvertStageHandler`
  - 五个子步骤与编排一致：`config`（生成 `ConvertConfig.ini`）→ `cab`（`7zr x` 解包转换器）
    → `aggregate`（调转换器脚本）→ `skeleton`/`bootwim`（产物验收）；
  - **不自建组件聚合**：把 `install.wim` 交给已验证转换器（路线 A）；
  - **输入目录以参数传入**（转换器支持第 1 个参数为 UUP 目录）→ **不复制 9.7 GB 数据**；
  - `ConvertOptions`：`TaskConfig.convert` → ini 键的强类型映射（AutoStart/SkipISO/wim2esd/Cleanup/…），
    ini 用 **UTF-8 BOM + CRLF** 原子写（转换器用 `findstr` 逐行读）；
  - `describe()` 输出 5 条真实命令（解包 / 写 ini / 调转换器 / 校验产物 / 可选 cdimage），
    `file_operations()` 列出将创建的 ini、`bin\`、`ISOFOLDER\`（`mode=full` 时加 ISO）。
- 组合根 `cli_runner.build_handlers()`：入口 A/B 自动装配 `Convert`（注入 `tools` 目录与 `ProcessRunner`）。

### 修正（本轮预演暴露的真 bug）
- **`StagePlanner` 入口 B 误排 `Fetch`**：原实现把入口 A/B 归为一类，只有「起点晚于 Convert」才跳过 Fetch，
  于是入口 B（素材已在本地）仍排上 `Fetch`，dry-run 显示「有活但没处理器」，实跑必然卡住。
  现改为：**入口 B 的 `Fetch` 一律 `input_satisfied`**（与 FR0.3「入口 B 从 Convert 开始」一致），
  入口 A 保持原语义（起点为 Convert 时仍要下载）。

### 预演证据（真实数据 `UUPs26300.9818`）
```
[run ] Prepare  substeps=probe,selfcheck
[skip] Fetch    reason=input_satisfied          ← 修正后
[run ] Convert  substeps=config,cab,aggregate,skeleton,bootwim  commands=5
  "tools\7zr.exe" x "tools\uup-converter-wimlib.7z" -o"work\<task>\convert" -y
  # 写入 work\<task>\convert\ConvertConfig.ini（AutoStart=3 SkipISO=1 AddUpdates=1 Cleanup=1 wim2esd=0 SkipApps=0）
  <shell> /c "convert-UUP.cmd" "…\UUPs26300.9818"   # cwd=work\<task>\convert
  wimlib-imagex info "…\work\<task>\convert\ISOFOLDER\sources\install.wim"
```

### 测试
- `test_convert_handler.py`（23）：选项映射（wim_only/full/LZMS/updates/cleanup/apps、缺省最保守）、
  ini 段落与 CRLF/BOM、原子写、解包幂等与缺包报错、`describe` 五条命令与缺目录报错、
  `file_operations` 两种模式、缺 runner/缺输入/入口 C 三种拒绝、可复用产物、
  入口 B 跳过 Fetch / 保留 Convert / 入口 A 仍执行 Fetch。
- 全量：`pytest 773 passed`；`mypy 114 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `docs/计划/现状与下一阶段计划.md` v1.0 → **v1.1**：新增 §4.4（转换编排、转换器事实、预演输出、
  默认值提醒）、P0-3/P1-1 状态更新、环境快照改为「转换器已就位」；`术语表 §1.2` 同步。

### 已知限制
- **未实跑**：`aggregate` 未真正调用转换器，`install.wim` 未产出；`skeleton`/`bootwim` 目前只做**产物验收**
  （转换器内部完成这两步，我们没有独立命令）。
- **转换器未入册** `tools.lock.json`（无版本/SHA256 记录），当前按 `tools\` 目录约定直接引用。
- **产物路径为宽松探测**（`rglob("install.wim")`）：转换器若改变输出布局，验收会失败并提示看日志。
- `ConvertSection()` 默认 `AddUpdates=1 Cleanup=1 SkipApps=0`，首轮实跑建议显式关闭（否则会集成 4.6 GB KB 与 2227 个 msix）。
- 非管理员：转换器内部若调用 DISM 挂载会失败，实跑需 UAC 提权。

## 0.1.30 — 新增《现状与下一阶段计划》+ 版本号动态获取方案

> 依据：用户提供的本地 26H2（26300.9818 zh-CN Professional）UUP 数据集；`docs/治理/版本与变更管理.md`。
> 本轮**只写文档，不改代码**。

### 新增
- **`docs/计划/现状与下一阶段计划.md`（v1.0）**：
  - 已完成内容：W0 11/12、W1 7/7、W2 5/6，逐任务给交付物与证据；
  - 未完成内容：W0 的 `WP0-T8a`（工具版本对齐，默认关闭）；W1 无未开工任务但列出 **4 处交付缺口**
    （编排未接检查点、心跳定时器、Ctrl+C 取消、dry-run 文件操作仅 Prepare）；
  - 新计划 P0-0…P3-1：版本目录 → 无清单识别 → Prepare 自检换判据 → 工具补位 → Convert 实测 →
    Output（WIM→ISO）→ 挂载类阶段；`WP2-T2` 降级；
  - **版本号自动获取方案**：GitHub 为可信来源，仓库与路径**可配置 + 运行时发现**，解析 `26H2 → 26300`，
    缓存 24 h、快照兜底、冲突告警；并明确「GitHub 只回答主版本号，修订号由输入数据自己回答」；
  - 附环境与数据快照（wimlib 1.14.4、7-Zip 已装未入册、无转换器/oscdimg、非管理员、D 盘 334 GB）。
- `docs/治理/术语表与文档映射.md`：登记新文档（短名「现状」，23 → **24 份**）。

### 关键判定（写进文档，供后续执行）
| 事实 | 影响 |
| --- | --- |
| 本地数据无 `files.txt`/`uup-info.json`/`ConvertConfig.ini` | 现版 UUP 目录特征**判不中** → 必须先放宽识别（P0-1） |
| 数据含 19 个 `.esd`、2227 个 `.msix`、4.6 GB 的 `KB5127936.msu` | 转换时长与 appx 集成策略需先定（首轮建议跳过更新与 appx） |
| 工具链只有 aria2c + wimlib；7-Zip 已装未入册；无转换器/oscdimg | `Convert` 与 ISO 输出被前置阻塞（P0-3） |
| 非管理员 | DISM 挂载类阶段需 UAC 提权 |
| GitHub 上三个候选「官方清单」仓库均 404（实测） | 版本目录必须**可配置仓库 + 运行时发现路径**，不能写死 |

### 变更（文档）
- `术语表` v0.3 → **v0.4**（登记表 24 份）。

### 已知限制
- 文档中的「26H2 = 26300」是**待解析目标**，尚未落地为代码；实现前不得在任何代码/规则里写死该数字。
- P0-3/P1-1 依赖用户对「转换器来源」与「是否实跑」的决策（文档 §6 D1–D4）。

## 0.1.29 — WP2-T5 清单缓存（10 分钟 TTL + 过期容错）

> 依据：下载 §2.3。验收：TTL 生效、过期容错可用且可见、键唯一、作废可用。

### 新增
- **`infrastructure/source/cache.py`**：`SourceCache` —— 位置 `{程序目录}\cache\source\<host>\<name>-<key>.json`，
  键为 `host + build + revision + language + edition` 的 SHA256 前缀；只存元数据、原子写、支持按 host/全部作废。
- **`infrastructure/source/cached.py`**：`CachedSource` 装饰器 —— 包装任意 `UupSource`：
  未命中→联网；新鲜→**不联网**；过期→刷新；**过期且刷新失败→降级使用 + `manifest_stale_used` 告警**。
  构建列表一并缓存；语言/edition 列表**不缓存**（体积小且必须与当前构建一致）。
- `core/models/source.py`：`CacheState`（miss/fresh/stale）+ `SourcePayload.from_cache` / `cache_age_seconds`。
- 组合根（`cli_runner._make_fetch_handler`）自动包上缓存；`FetchStageHandler` 把 `from_cache` 透传进清单。
- `.gitignore` 忽略 `cache/`（运行期缓存，不入库）。

### 两处比规范更严（实测驱动，已回写 下载 §2.3）
1. **按 URL 寿命提前失效**：清单 URL 实测只有 ~12 分钟寿命，剩余不足 **120 s** 即判过期强制刷新，
   否则会命中「清单还在、URL 已死」的缓存，下载必然全失败；
2. **降级必须可见**：返回过期缓存时附 `manifest_stale_used age_minutes=<n>` 与 `fromCache=true`，
   不许静默。

### 真实联调
```
第一次：Fetch 完成 2/97｜14.2MB｜2.4s（联网拉清单）
第二次：Fetch 完成 2/97｜14.2MB｜0.9s（命中缓存，未请求 get.php）
缓存文件：cache/source/uupdump.net/{buildlist,manifest}-<hash>.json
```

### 测试
- `test_source_cache.py`（15）：空缓存 miss、TTL 内 fresh、超时 stale、**剩余寿命不足强制 stale**、
  URL 已过期标记、键按 edition/语言区分、文件结构含 meta、损坏缓存按 miss（带告警）、
  按 host/全部作废、装饰器命中不联网、过期刷新、**刷新失败降级 + 告警**、无缓存时失败要抛错、
  构建列表缓存、语言/edition 不缓存。
- 全量：`pytest 752 passed`；`mypy 113 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `下载` v0.6 → **v0.7**：§2.3 后补落地状态与两处加严说明；`术语表 §1.2` 同步。

### 已知限制
- **全量 9 GB 下载仍未跑通**：即便命中缓存（不请求 `get.php`），97 个 URL 的首批仍被 CDN 集体 403；
  同一批 URL 手工 aria2c 下载可成功 → 属按 IP 的瞬时限流。T5 只消除了「重复拉清单」这一半原因，
  另一半（下载突发）需冷却重试或换网络环境验证。
- 缓存**没有大小上限与清理策略**：条目按构建累积（每个约 20–40 KB），长期使用需加 LRU 或按天数清理。
- `invalidate()` 已实现但**没有 UI/CLI 入口**（手动刷新入口属 WP9/设置页）。
- 语言/edition 不缓存，因此每次仍会各自请求一次（这两步很轻，但严格说不是零请求）。

## 0.1.28 — WP2-T4 `Fetch` 阶段处理器（接线到编排）

> 依据：编排 §3、下载 §4/§5、`下载 §6` 目录结构。
> 验收：`manifest → download → verify` 三子步骤可独立执行；`uup2iso run` 能走到下载；小范围真实下载通过。

### 新增
- **`application/pipeline/handlers/fetch.py`**：`FetchStageHandler`
  - 子步骤严格按计划裁剪：`manifest`（拉清单 + 五项校验 + 落盘）/`download`（aria2c）/
    `verify`（独立重算 SHA1，不信任 aria2c 退出码）；
  - **分批下载**（`_BATCH_SIZE = 8`）：实测一次性把 97 个签名 URL 交给 aria2c 会被 CDN 集体 403；
  - **两级失败兜底**：同清单退避 5 s 重试 → 仍失败才刷新清单（刷新对 403 无效，URL 相同）；
  - `describe()` / `file_operations()` 供 `--dry-run`；`reusable_artifacts()` 报 `manifest.json`。
- `application/fetch/manifest_service.py`：新增 `load_manifest()`（续跑读回清单）。
- `Aria2Downloader.bind()`：组合根先建对象、后接日志/进度。
- `cli_runner.build_handlers(config)`：入口 A 自动装配 Fetch（数据源 + aria2c 下载器）。
- `scripts/smoke_fetch.py`：Fetch 阶段联调（`--names` 可只下指定文件）。

### 真实联调
```
✅ 2 个文件 / 47.4KB   → 2/2 通过校验，0.1s
✅ 2 个文件 / 14.2MB   → 2/2 通过校验，0.5s（AggregatedMetadata 3.0MB、DesktopDeployment 11.2MB）
✅ Fetch 阶段（2 文件范围）：清单→下载→校验 全链路通过
❌ 全量 97 文件 / 9.09 GB：被 CDN 限流（get.php 400、URL 403）
```

### 全量未通过的原因（已核实，非代码缺陷）
| 现象 | 证据 |
| --- | --- |
| `get.php` 返回 400 | 连续多轮探测后出现，冷却后恢复 |
| 97 个 URL 集体 403 | 同一批 URL 隔几分钟手工 aria2c 下载即成功（3/3、8/8） |
| `--split=1/4/8`、批大小 3/4/5/8 均正常 | 排除参数与并发因素 |

→ 结论：**按 IP 的瞬时限流**。批次级退避重试已实现；全量需在冷却后重跑
（命令：`.venv\Scripts\python scripts\smoke_fetch.py --clean --build 26100.9725`）。

### 测试
- `test_fetch_handler.py`（8）：三子步骤顺序与清单落盘、下载失败后「退避→刷新」两级兜底、
  独立校验发现缺文件、缺数据源/缺下载器/非入口 A 的明确报错、dry-run 命令与文件操作、
  可复用产物、磁盘已有清单时不重复联网。
- 全量：`pytest 735 passed`；`mypy 111 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `下载` v0.5 → **v0.6**：§6 前补 Fetch 落地状态、分批策略与限流实测遗留；`术语表 §1.2` 同步。

### 已知限制
- **全量下载尚未在本机跑完**：受 CDN 限流影响，需冷却后重试；代码路径与小范围已验证。
- `verify` 子步骤会重算**范围内全部文件**的 SHA1（9 GB 约需 30–60 s），暂未按块并行/缓存。
- 批次大小与退避时长是经验值（8 / 5 s），未做成配置项。
- `Fetch` 只在入口 A 装配；入口 B（UUP 文件夹）若目录已完整，按编排应记 `skipped` 而非走下载。

## 0.1.27 — WP2-T6/T7 aria2c 下载与校验（含 T3 决策修正）

> 依据：下载 §4.1、§4.2、§5、§5.2、§5.4。
> 验收：只经 `ProcessRunner` 调 aria2c；校验顺序「存在 → 大小 → SHA1」；哈希不符重下 ≤2 次；
> **已用真实构建下载并校验通过**。

### 新增（`infrastructure/download/aria2.py`）
- `Aria2Options` —— §4.2 参数契约：`--continue`、`--max-concurrent-downloads=1`、`--split`（1–16 夹取）、
  `--min-split-size=20M`、`--max-tries=3`、`--retry-wait=2`、`--max-file-not-found=1`、
  `--summary-interval=1`、`--console-log-level=warn`、`--check-certificate=true`；代理/限速可选（空则不带参数）。
- `build_input_file()` —— §4.1 输入列表（`url` / `  dir=` / `  out=`），**全部绝对路径**。
- `verify_file()` / `sha1_of()` —— §5.1 顺序校验；SHA1 按 1 MB 流式计算。
- `Aria2Downloader.download()` —— 校验 → 坏文件删掉（含 `.aria2`）→ 退避 2 s 重下，最多 2 轮；
  成功写 `download\download.state.json`；仍失败抛 `DOWNLOAD_CORRUPT` 并列出「名称/期望/实际」。
- `scripts/smoke_download.py` —— 小范围真实下载联调（默认只下最小的 2 个文件）。

### 真实下载结果（2026-11-02，26100.9725 zh-cn professional）
```
[OK] 选中 2 个文件 / 47.4KB → 2/2 通过校验，0.1s
[OK] 选中 2 个文件 / 14.2MB → 2/2 通过校验，0.5s
     [OK] AggregatedMetadata.cab        3.0MB  verified
     [OK] DesktopDeployment.cab        11.2MB  verified
```

### 三个实测结论（两个纠正了上一轮的判断）
| # | 现象 | 处理 |
| --- | --- | --- |
| 1 | **`https` 下载必然失败**：TLS 证书与主机名不匹配（`目标主要名称不正确`），返回 22 | **推翻 0.1.26 的「http→https 升级」**：清单**保留源给的 scheme**，记 `url_kept_http`；校验放行白名单域名的 http，非白名单仍拒绝 |
| 2 | `aria2c --out` 只是**文件名**，不是路径 | 路径走 `--dir`；输入列表里 `dir=`/`--input-file` 必须**绝对路径**，否则 cwd 基准错位（`Failed to open ... input.txt`） |
| 3 | 下载 URL 寿命极短（~12 分钟） | 清单→下载必须**连续完成**；过期后需重新拉清单（T5 缓存要加「按 expire 提前失效」） |

### 测试
- `test_download_aria2.py`（15）：输入列表绝对路径、参数契约（含 split 夹取、代理/限速可选）、
  校验三分支（缺失/大小不符不算哈希/哈希不符）、成功写状态、坏文件重下 1 轮成功、
  3 次机会耗尽抛 `DOWNLOAD_CORRUPT` 并列名、`names` 过滤、未知名称报 `MANIFEST_INVALID`、进度只进通道。
- `test_manifest_service.py` 调整（21）：改为「保留 scheme + 白名单 http 放行 + 非白名单/非法协议拒绝」。
- 全量：`pytest 726 passed`（新增 15 项）；`mypy 110 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `下载` v0.4 → **v0.5**：§3.2 的「升级 https」**更正**为「保留原 scheme + 白名单 http 放行」；
  §5 后补 T6/T7 落地状态与 aria2c 的两个实测坑；`术语表 §1.2` 同步。
- `core/errors`：登记 `DOWNLOAD_CORRUPT`（下载 §5.2 原有码，此前未落地）。

### 已知限制
- **还没接进编排**：`Fetch` 阶段处理器未写，`uup2iso run` 仍走不到下载（下一步）。
- **缓存未做**（T5）：清单 URL 只有 ~12 分钟寿命，缓存必须按 `expire` 提前失效。
- **多文件并发固定 1**（按规范）；单文件 8 连接；`--max-overall-download-limit` 已支持但 CLI 未暴露。
- **不做 `--file-allocation` 调优**（规范要求保持默认）；OneDrive 等同步目录下大文件预分配可能变慢。
- **HTTP 401/403（URL 过期）目前表现为「文件缺失 → 重下 → 仍失败」**，未针对性提示「请重新拉清单」。

## 0.1.26 — WP2-T3 下载清单生成与五项自校验

> 依据：下载 §3.1、§3.2、§3.3。
> 验收：清单结构符合 §3.1；五项自校验逐条生效；**已用真实构建生成 97 项 / 9.09 GB 清单**。

### 新增（`application/fetch/manifest_service.py`）
- `build_manifest(payload, request, debug_dir=None)` → `(Manifest, warnings)`：
  - `http → https` 升级（记 `url_upgraded_to_https count=<n>`）；
  - 与配置选择不一致记 `manifest_mismatch`；
  - 校验失败先落 `debug\source-response.json`（**脱敏**：去 URL query）再抛错。
- `validate_manifest(manifest, request=...)` —— §3.2 五项逐条检查，报错信息带**具体文件与字段**：
  非空/字段齐全、名称唯一、`totalBytes` 等于大小之和、`sha1` 40 位 hex、`url` 为 https 且在白名单、必需文件 ≥1。
- `manifest_to_json()` / `write_manifest()`（原子写，`tmp → replace`）。
- `redact_payload()` —— 原始响应脱敏视图（只留 host+path）。
- 白名单：官方后缀（`uupdump.net` / `microsoft.com` / `windowsupdate.com`，含子域）
  **∪ 当前数据源 host ∪ `extra_hosts`**——否则用户配的镜像会被自己的校验判死。

### 真实联调（2026-11-02）
```
[OK] 清单校验通过：97 项 / 9.09 GB（生成告警 1 条）
[WARN] url_upgraded_to_https count=97
[OK] 已写出 manifest.json
```
落盘结构核对：`schemaVersion/parserVersion/source/build/revision/language/edition/totalBytes/files/unknownFields`
齐全，`totalBytes=9093627232` 与 97 个文件大小之和一致，全部 URL 为 https。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | `http → https` 升级而不是拒绝 | 拒绝等于「uupdump 源不可用」；微软 CDN 通常两者都通 |
| D2 | 白名单包含「当前数据源 host」 | 规范明文要求，否则自定义镜像必被自己的校验拦住 |
| D3 | 校验失败**先落脱敏响应再抛错** | 事后排查需要原始形状，但不能把签名 query 写进磁盘 |
| D4 | 升级/不一致等记**汇总类**告警（count=n） | 97 条逐文件告警会把日志刷满 |
| D5 | 清单落盘用原子写 | 与检查点/配置同口径，避免半截 JSON |

### 测试
- `test_manifest_service.py`（19）：生成时 https 升级与计数、配置不一致告警、https 不误报、
  五项自校验各一条负例（空清单/缺字段/重名/总字节不符/SHA1 非法/http URL/非白名单 host/无必需文件）、
  镜像 host 与 extra_hosts 放行、官方后缀放行、JSON 形状、原子写、失败脱敏 dump、redact 去 query。
- 全量：`pytest 708 passed`（新增 19 项）；`mypy 109 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `下载` v0.3 → **v0.4**：§3.2 后补落地状态与「http→https 升级」的偏差登记；`术语表 §1.2` 同步。

### 已知限制
- **没有缓存**（T5）：每次生成都真实联网；10 分钟 TTL 与过期容错未做。
- **不重取过期 URL**：清单里 `expire` 只剩 ~0.2 小时，下载前不校验就会拿到死链（属 T6/T7）。
- **`required` 恒为 true**：站点没有「可选文件」概念，全部按必需处理；将来若区分需数据源支持。
- **无清单读回**：只有写，没有 `load_manifest()`；续跑复用清单属 T5/T6。

## 0.1.25 — WP2-T1 数据源抽象与 uupdump 实现

> 依据：下载 §2.1、§2.2、§2.4。
> 验收：地址可配（含备用列表）、必需/可选字段分级、错误分类准确；**已用真实构建联调通过**。

### 新增
- **`infrastructure/source/base.py`**：`UupSource` 协议（`list_builds` / `list_languages` /
  `list_editions` / `get_files`）+ `SourceError`（退出码 4）。
- **`infrastructure/source/uupdump.py`**：`UupDumpSource` ——
  - 站点地址自动映射 API 域（`uupdump.net` → `api.uupdump.net`），可换镜像/自建；
  - 礼貌约束：请求间隔 ≥300 ms、带产品 UA、同源串行（锁）；
  - 失败切换：同一地址**连续失败 3 次**才切备用，记 `source_failover`；
  - 错误分类：超时/非 200 → `SOURCE_UNAVAILABLE`，429 → `SOURCE_RATE_LIMITED`，结构不符 → `SOURCE_SCHEMA_INVALID`；
  - 字段分级：文件名/大小/SHA1/URL 缺失或非法 → 致命；`sha256`/`expire` 缺失 → 告警；未知字段收进 `unknown_fields`。
- **`core/models/source.py`**：`EditionInfo`、`SourcePayload`（含 `total_bytes`、`url_expired`）。
- `BuildInfo` 增加 `id`（数据源内部标识，换取详情用）。
- **`scripts/smoke_source.py`**：真实联调脚本（不进 CI）。
- **`tests/fixtures/source/`**：真实响应夹具（listid/langs/eds/get，共 17 KB），离线测试用。

### 真实联调结果（2026-11-02）
```
[OK] 构建 500 个（search=26100）
     26100.9725 x64  Windows 11, version 24H2 (26100.9725)  id=4ef76f77
[OK] 语言 39 个：en-us, ar-sa, ko-kr, …
[OK] edition 3 个：PROFESSIONAL(Windows Pro), CORE(Windows Home), CORECOUNTRYSPECIFIC(Windows Home China)
[OK] 清单 97 个文件 / 9.09 GB（build=26100.9725 arch=amd64，URL 0.2 小时后过期）
```

### 实测发现（已按此实现，并回写 下载 §2.1）
| # | 现象 | 处理 |
| --- | --- | --- |
| 1 | 个别文件名带 **UUID 前缀**（`AggregatedMetadata.cab`），且前缀**不等于**当前 build 的 uuid | 按 UUID 形态正则归一化 + `file_name_normalized` 告警 |
| 2 | 站点给的是 **http 直链**（微软 CDN），与 §3.2-4 的 https 要求冲突 | 先记 `insecure_url`；是否 http→https 升级留给清单生成（T3）决定 |
| 3 | 下载 URL **有效期极短**（实测 ~0.2 小时） | 解析出 `expires_at` + `url_expired`；T5 的 10 分钟 TTL 已接近上限，下载前必须校验 |
| 4 | 服务端累积更新（`Update for Microsoft server operating system`）**没有语言列表** | 返回空 + `no_languages` 告警，由调用方跳过（不是站点故障） |

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 站点地址 → API 域自动映射 | 配置里写用户能看到的站点，实现里用真实 API，业务代码不感知 |
| D2 | 空语言/edition 返回空而非报错 | 站点上确有「非完整 OS 构建」，报错会让整条列表不可用 |
| D3 | 坏构建条目跳过并告警，全坏才报错 | 一条脏数据不该让 500 条列表整体失败 |
| D4 | `insecure_url` 只告警不拒绝 | 直接拒绝等于「这个源不能用」；升级 scheme 属清单生成职责 |
| D5 | 首次请求不等待 | 300 ms 只约束连发，不该让第一次调用白等 |

### 测试
- `test_source_uupdump.py`（23）：构建解析/搜索参数/UA/架构映射/坏条目跳过/全坏报错、
  语言小写与空列表、edition 显示名与空列表、清单解析与名称归一化/http 告警/缺字段致命/SHA1 非法/未知字段、
  500 与 429 分类、坏 JSON、三次失败后回退、无备用不回退、300 ms 间隔、API 域映射。
- 全量：`pytest 687 passed`；`mypy 107 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `下载` v0.2 → **v0.3**：§2.1 补落地状态与四条实测发现；`术语表 §1.2` 同步。

### 已知限制
- **缓存未做**（WP2-T5）：现在每次调用都真实联网；10 分钟 TTL 与过期容错在 T5。
- **无过滤能力**（WP2-T2）：`list_builds` 只支持关键字，通道/架构/语言/版本过滤与 KB 详情在 T2。
- **不解析 `updateInfo`**：`get.php` 的 `updateInfo` 目前为 null；KB 列表解析属 T2。
- **URL 过期不自动重取**：`SourcePayload.url_expired` 只是标记，重取策略在 T5/T6。

## 0.1.24 — WP1-T6 CLI 输出与退出码 + WP1-T7 dry-run 预览

> 依据：GUI框架 §5.6；日志 §6.1；编排 §9；输出 §10（不建暂存区）。
> 验收：JSON 可被 CI 消费；退出码区分结果；`--dry-run` 不实际改动磁盘。

### 新增
- **T6 — CLI 输出契约**：`run` 的 **stdout 只输出一行 JSON**（`ok` / `code` + 结果字段），
  **stderr 输出人读文本**。CI 直接 `json.loads(stdout)` 即可，**不新增 `--json` 开关**
  （配置 §11 的最小命令集只有 `--dry-run`/`--resume`/`--debug`）。
  JSON 字段：`ok`、`code`、`task`、`dryRun`、`logLevel`、`stages[]`、`commands[]`、`files[]`、
  `decision`/`reuseStages`/`redoStages`（续跑）、`error`/`message`/`missingStages`（失败）。
- **T7 — dry-run 预览增强**：`DryRunStage` 增加 `file_ops`（`FileOp(action, path, note)`），
  新增 `DryRunReport.file_lines()` 与 `file_operations`；`PrepareStageHandler.file_operations()`
  给出「读输入 / 建解包目录 / 写 probe.json」三类操作。阶段状态映射为稳定字符串
  `run` / `skip` / `todo`（`todo` = 该跑但没处理器）。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 不新增 `--json`，靠 stdout/stderr 分工 | 最小命令集不该再长一个开关；CI 与人都能各取所需 |
| D2 | JSON 固定带 `ok` 与 `code` | 消费方不必自己推断退出码语义 |
| D3 | 阶段 action 用 `run`/`skip`/`todo` 三值 | `todo` 明确区分「没选项跳过」与「有活但没实现」 |
| D4 | 文件操作由处理器声明（`file_operations`） | 只有处理器知道真实路径，编排层猜不出来 |
| D5 | 文件操作只描述不执行 | dry-run 的底线：不建目录、不写文件（输出 §10-10） |

### 测试
- `test_cli_run.py` 增至 15 项，新增：dry-run payload 的**阶段明细与 files**（且不建 work 目录）、
  stdout 是**单个可解析 JSON**（且人读信息在 stderr）、失败时 JSON 带 `ok=false` 与具体 `error`。
- 真实 CLI 冒烟：`run --dry-run 2>/dev/null` 输出单行 JSON（`ok=true`、8 个阶段、3 条命令、3 条文件操作）；
  `2>&1 >/dev/null` 得到人读的阶段表 + 命令 + 文件预览。
- 全量：`pytest 661 passed`；`mypy 104 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `GUI框架` v0.11 → **v0.12**：§5.6 补「CLI 输出契约」与 dry-run 文件预览、以及「不新增 --json」的理由。

### 已知限制
- **`files[]` 目前只有 `Prepare` 的条目**：其它阶段没有处理器，自然也没有文件操作可报；
  `todo` 阶段只会出现在 `stages[]` 里。
- **JSON 没有 schema 版本号**：字段可能随 WS2 演进（会增加 `tool`/`bytes`/`duration` 等）；需要稳定契约时再引入 `schemaVersion`。
- **stdout 只有「一行 JSON」**：长任务执行期间不输出进度（进度只走日志/GUI），CI 要实时输出需自行看 stderr。
- `stderr` 的人读文本无颜色与对齐优化；终端宽度窄时会换行错位。

## 0.1.23 — WP1-T5 最小 CLI

> 依据：配置 §11；日志 §3；FR9.2。
> 验收：仅接受配置文件与运行开关；`--debug` 仅本次生效；与 GUI 共用同一配置契约。

### 新增
- **`application/cli_runner.py`**：`run` 的业务编排（CLI 只解析参数、调它、把结果变退出码）：
  加载配置（V0/V1/V2）→ 抢单实例 + 建日志 → 算阶段计划与续跑判定 → `--dry-run` 或执行。
  - 配置错误 → 退出 4，`payload={"error": <CONFIG_*>, ...}`，**不建日志**；
  - 单实例冲突 → 退出 5，`payload=already_running`，**不建日志、不清理**；
  - 续跑：配置变了 → 退出 4 `CONFIG_CHANGED`；上次已完成 → 退出 0 并提示；
  - 非 dry-run 且处理器不全 → 退出 4 `not_implemented` 并**列出缺哪几个阶段**；
  - `dry_run` 打印阶段（`[run]/[skip]/[TODO]`）与将执行的命令，**不建 work 目录**。
- **`cli/main.py`**：`run --config <file> [--dry-run] [--resume] [--debug]`（参数改 `Annotated`，
  去掉 `typer.Option` 作为默认值触发的 B008）；`--config` 必填；根回调对 `run` 跳过互斥（由 runner 自己抢）。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | `run` 自己抢互斥，根回调跳过它 | 日志必须在「抢到 + 建会话」之后才建；回调里抢会导致重复抢同一把锁 |
| D2 | 处理器不全时**明确拒绝**（`not_implemented` + 阶段列表） | 比跑到一半崩在缺处理器上清楚得多 |
| D3 | 「上次已完成」退 0 而不是报错 | 它不是失败，只是没活干（编排 §9） |
| D4 | `--debug` 只影响本次日志级别 | 日志 §3：一次性开关，不写配置、不改设置 |
| D5 | 用 `Annotated[...]` 声明参数 | `typer.Option` 作默认值会触发 B008；Annotated 是 Typer 现行推荐写法 |

### 测试
- `test_cli_run.py`（12）：dry-run 打印计划且不建 work 目录、`--debug` 仅本次（配置字节不变）、
  坏 JSON/语义错误退 4 且不建日志、冲突退 5 且不建日志、续跑配置变更退 4、无检查点判 new_task、
  非 dry-run 报 `not_implemented` 列阶段、缺 `--config` 退 2、单横线参数退 2、参数透传、`version` 仍可用。
- 真实 CLI 冒烟：`run --config <tmp> --dry-run` → 打印 8 阶段与 3 条 Prepare 命令，退出 0；
  坏配置 → 退出 4；非 dry-run → 退出 4 并列出 `Slim、Output`。
- 全量：`pytest 658 passed`；`mypy 104 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `配置` v0.4 → **v0.5**：§11 补落地状态（参数写法、`--debug` 不写回、`run` 自己抢互斥）。

### 已知限制
- **还不能真正执行任务**：`Fetch`/`Convert`/`Slim`/`Optimize`/`Integrate`/`Unattend`/`Output` 都没有处理器，
  `Prepare` 的底层也缺 `PrepareOps`，所以非 dry-run 一律以 `not_implemented` 退出 4。
- **JSON 输出的正式形态属 WP1-T6**：现在只在 `payload` 里备好数据，CLI 仍打印人读文本。
- **Ctrl+C 取消未接线**：`orchestrator.run(cancel=None)`，取消语义（退出 3）要等长驻任务循环。
- `--dry-run` 的「文件操作预览」属 WP1-T7；当前只到「阶段 + 命令 + 可复用产物」。

## 0.1.22 — WP1-T4 输入识别与兼容校验

> 依据：输入 §2、§3、§4、§5、§7。验收：扩展名误标可识别；拒绝时给出具体项；`INPUT_*` 退出码 4。

### 新增
- **`infrastructure/input/sniff.py`**：64 KB 读头判类型 —— WIM 魔数 `MSWIM\0\0\0`、
  ISO 的 `CD001`@**32769**（读 32 KB 会差 6 字节，所以固定 64 KB）、UUP 文件夹特征
  （清单 + ≥1 个 `.cab/.esd/.msu`）；**头优先于扩展名**，不符记 `extension_mismatch`。
- **`infrastructure/input/wiminfo.py`**：解析 `wimlib info`（XML 优先、文本兜底），
  edition 归一化（整名表 + 去前缀表两级），分片（`PART TOTAL>1` / `Part Number: n of m`）与
  LZMS（→ ESD）识别；`classify_wimlib_failure()` 把 `encrypted` 归为 `INPUT_ENCRYPTED`。
- **`infrastructure/input/probe.py`**：`InputProber.probe()` —— 把候选收敛成一个输入，
  按 §3 拒绝加密 ESD / 多卷 SWM（附 DISM 合并指引）/ 分片 ESD / 非 Windows / 多候选歧义；
  `ProbeReport.to_probe_json()` 输出 §4 的 `probe.json` 结构。
- **`application/pipeline/handlers/prepare.py`**：`PrepareStageHandler` —— 按入口裁剪执行
  `probe → materialize|extract → selfcheck`，原子写 `input\probe.json`，`selfcheck` 失败抛
  `INPUT_DAMAGED`；提供 `describe()`（dry-run 命令）与 `reusable_artifacts()`。
- `core/errors`：新增 `InputError(DependencyError)`（退出码 4），具体码由调用方给出。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 读头固定 64 KB | ISO 的 `CD001` 在 32769，32 KB 差 6 字节永远判不出（输入 §2.2） |
| D2 | 头优先、扩展名只做告警 | 扩展名误标是真实场景；按真实类型处理才安全 |
| D3 | 多候选一律 `INPUT_AMBIGUOUS` | 猜错代价是跑完几十分钟才发现输入错了 |
| D4 | 加密/多卷/分片/非 Windows **早拒绝** | 不尝试、不产半成品（输入 §3） |
| D5 | `Prepare` 的具体动作走 `PrepareOps` 注入 | 引擎层未就绪也能测顺序、产物与错误分类 |
| D6 | `probe.json` 原子写 | 与检查点同口径，避免半截 JSON |

### 测试
- `test_input_sniff.py`（15）：魔数/ISO 偏移/扩展名误标（两个方向）/ESD 家族/SWM（按名与按兄弟卷）/
  UUP 文件夹三态/目录多候选/顶层扫描不递归。
- `test_input_wiminfo.py`（14）：XML 双 edition、分片、LZMS、文本兜底、空输出与坏 XML、
  edition 归一化六例、加密分类。
- `test_input_probe.py`（14）：ISO 待解包、`probe.json` 字段、LZMS 细化、未知 edition 告警、
  分片/加密/SWM/非 Windows 四类拒绝、目录歧义列候选、UUP 接受、缺失与未知文件、扩展名误标透传。
- `test_prepare_handler.py`（9）：ISO 走 extract、ESD 走 materialize、`probe.json` 落盘且无 tmp、
  自检失败 → `INPUT_DAMAGED`、缺 ops → `INTERNAL_ERROR`、dry-run 命令、可复用产物、入口 A 无本地输入。
- 全量：`pytest 645 passed`；`mypy 103 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `输入` v0.2 → **v0.3**：§7 前补落地状态（已实现范围 + 未接线项）；`术语表 §1.2` 同步。

### 已知限制
- **真实 `PrepareOps` 未实现**：wimlib/7z 调用、ISO 解包、ESD 物化都要等引擎层（WS2）。因此
  `Prepare` 现在能跑通「顺序 + 产物 + 错误分类」，但解不出真实文件。
- **ISO 的 edition 枚举要解包后二次探测**：现在只记 `images_pending_extract`，`probe.json.images` 为空。
- **`bootable` 恒为 false**：没有解析 El Torito，`warnings` 里已标 `bootable_unknown`。
- UUP 文件夹特征是**启发式**（清单名 + 归档后缀），未做 `ConvertConfig.ini` 内容校验。
- `InputProber` 的 `wim_info` 默认未注入：不注入时只做头嗅探，不做加密/分片/edition 判定。

## 0.1.21 — WP1-T3 检查点与续跑

> 依据：编排 §4.1、§4.2、§5、§6、§9、§10。
> 验收：子步骤级续跑；配置/输入变更时拒绝续跑；清理只删中间物。

### 新增
- **`core/models/checkpoint.py`**：`Checkpoint` / `StageRecord` / `SubstepRecord` / `ArtifactRecord` /
  `TaskRuntime` / `ResumePlan` / `ResumeDecision`。状态取值**复用** `core/models/enums.py` 的
  `TaskState` / `StageState` / `SubstepState`（不另造一套）。
- **`infrastructure/storage/checkpoint_store.py`**：`checkpoint.json` 原子写（`tmp` → `flush` → `fsync` → `replace`）、
  `heartbeat()` 心跳、损坏识别（`corrupt=True` 而不是抛到启动路径）。
- **`application/task_session/session.py`**：`TaskSession` —— 状态迁移（`begin_stage` / `end_stage` /
  `begin_substep` / `end_substep` / `record_artifact` / `finish` / `interrupt`）+ `plan_resume()`：
  - 四条件（配置指纹 / 输入指纹 / 状态 / 产物大小与 SHA256）判定复用，`.part`/`*.tmp` 永不参与；
  - 五个分支：`NEW_TASK` / `ALREADY_SUCCEEDED` / `RESUME` / `REJECT_CONFIG_CHANGED` / `REJECT_INPUT_CHANGED`，
    `running` 则判 `INTERRUPTED`；
  - **子步骤级续跑**：给出「第一个非 done 的子步骤」（`resume_from_substep`）。
- **`infrastructure/storage/task_record_store.py`**：`runtime` 段合并写入（保留 config 等字段）、
  `summaries()` 列表、`prune()` 保留最近 50 个（删记录 + 删 `work\<id>\`，**运行中永不清理**，
  **绝不碰输出目录**）。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 状态枚举复用 `enums.py`，只新增「记录结构」 | 术语表 §2.2 是唯一写法来源，再造一套必然漂移 |
| D2 | 检查点写用 `fsync` + `replace` | 断电时要么旧文件要么新文件，编排 §12-8 要求「不出现半截 JSON」 |
| D3 | 损坏检查点按「新任务」处理并告警 | 让程序起不来比丢一次续跑更糟 |
| D4 | 阶段状态未知（缺条目）即判重做 | 宁可重做，不可把不确定状态当成可复用 |
| D5 | 删除前校验 `work_root/<合法id>` | 防路径穿越误删（`prune` 会 `rmtree`） |
| D6 | 只删记录与 `work\<id>\`，不碰输出目录 | 编排 §10：最终产物属于用户 |

### 测试
- `test_checkpoint_store.py`（8）：初始化字段（驼峰）、读写回环、原子写无残留 tmp、缺失/损坏/结构非法三态、
  心跳更新、未知字段前向兼容。
- `test_task_session.py`（19）：状态迁移持久化、子步骤即时写入、产物 size+sha、`interrupt` 幂等、
  五个续跑分支、子步骤级续跑点、四条件（大小不符/产物缺失/`.part` 不复用）、跳过阶段视为可复用。
- `test_task_record_store.py`（10）：runtime 合并保留 config、列表跳过坏文件、保留 50 个删最旧、
  **不碰输出目录**、运行中永不清理、`keep` 参数、无 work_root 时只删记录、空目录无害。
- 全量：`pytest 587 passed`；`mypy 98 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `编排` v0.3 → **v0.4**：§10 后补落地状态（已实现范围 + 未接线项）。

### 已知限制
- **编排器还不会写检查点**：`PipelineOrchestrator.run()` 目前不持有 `TaskSession`；接线在 WP1-T5（`run --resume`）
  与 WP9-T12（任务列表）时完成。因此「强杀后续跑」现在只有会话层可用，端到端还没跑通。
- **主键心跳未启动定时器**：`heartbeat()` 已实现，但没有 60 秒定时器调用它（需要长驻任务循环，属 T5）。
- 子步骤级重试（下载 3 次 / 校验 2 次）仍在各阶段处理器内，本层只做阶段级。
- `.part`/`staging.json` 的**产物侧**状态机（输出规范 §7）尚未实现，本层只保证「不把 `.part` 当可复用」。

## 0.1.20 — WP1-T2 PipelineOrchestrator

> 依据：编排 §2、§7、§8、§11、§12。验收：`--dry-run` 列出阶段、命令与将复用产物。

### 新增（`application/pipeline/`）
- **`orchestrator.py`**：`PipelineOrchestrator.run()` —— 按 T1 的计划推进 8 阶段：
  跳过阶段记 `stage=skipped reason=…`；执行阶段按需重试；取消在两处检查（阶段之间、尝试之间）；
  事件按编排 §11 落日志（`task_started` / `stage_started` / `stage_finished` / `stage_skipped` /
  `stage_retry` / `task_cancelled` / `task_finished`）。
  `PipelineRun.exit_code` 直接可交进程：成功 0、取消 3、失败按错误码（配置 4 / 兜底 2）。
- **`retry.py`**：`RetryPolicy` + `is_retryable()` —— **只按白名单重试**
  （`SOURCE_UNAVAILABLE` / `SOURCE_RATE_LIMITED` / `TOOL_FAILED`），阶段级兜底 2 次、默认 30s 退避；
  取消、`CONFIG_*`、`INPUT_*`、`DISK_FULL`、跨卷、未知异常一律不重试。
- **`handlers.py`**：`StageHandler` 协议、`StageContext`（含 `attempt` / `cancel` / `log` / `progress`）、
  `StageOutcome`；`describe()` / `reusable_artifacts()` 为可选，供 dry-run。
- **`dry_run()`**：输出 `DryRunReport`（`lines()` 与 `command_lines()`），列出「将执行/跳过 + 子步骤 + 命令 + 将复用产物」；
  **不建目录、不写检查点**；缺处理器的阶段标 `[TODO] … (无处理器)` 而不是假装能跑。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 编排器只管「跑不跑/跑几次/怎么记账」，不碰具体命令 | 命令随工具与版本变化，属阶段处理器；编排只管流程 |
| D2 | 非白名单异常**不重试** | 盲目重试会把确定性问题变成「绕 3 次再失败」，还拖长日志 |
| D3 | 清理钩子只在「阶段含挂载子步骤」且要重试时调用 | `Output` 之类阶段没有挂载，无需 `Cleanup-Wim` |
| D4 | 清理失败即停止重试 | 残留清不掉还重跑，只会把脏状态叠得更深 |
| D5 | 取消落在「下一个阶段」而不是当前阶段 | 当前阶段已 `succeeded`，标 cancelled 会篡改事实 |
| D6 | dry-run 对缺处理器标 `[TODO]` | 诚实显示「这阶段还跑不了」，比列空命令好 |

### 测试
- `test_orchestrator.py`（18）：按序执行、跳过阶段不调处理器、白名单重试后成功（3 次尝试 / 2 条 `stage_retry`）、
  重试耗尽退出 2、配置错误不重试退出 4、取消前不跑、取消中保留产物且下一个阶段标 cancelled、
  处理器抛 `UserCancelledError` 按取消处理、挂载阶段重试前清理、清理失败中止、非挂载阶段不清理、
  缺处理器失败且不继续、进度只覆盖执行阶段、日志事件齐全、dry-run 列命令与复用、dry-run 不建目录、缺处理器标 TODO。
- 全量：`pytest 546 passed`（新增 18 项）；`mypy 94 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `编排` v0.2 → **v0.3**：§4.2 后补落地状态（已实现范围 + 未实现项：检查点/续跑、输入识别、阶段处理器）。

### 已知限制
- **没有任何真实阶段处理器**：仓库里只有协议与测试替身。真正干活的是 WP1-T4（`Prepare`）与 WS2/WS3
  （`Fetch`/`Convert`/`Slim`/`Optimize`/`Integrate`/`Unattend`/`Output`），所以现在还跑不出一条真实任务。
- **检查点与续跑未实现**（WP1-T3）：`checkpoint.json`、`.part`/`staging.json` 状态机、任务记录保留 50 个。
- `--dry-run` 尚未接到 CLI（WP1-T5）；目前只有 `orchestrator.dry_run()` 这个编程入口。
- 重试的**子步骤级**次数（下载 3 次、校验 2 次等）不在本层：由各阶段处理器内部实现。

## 0.1.19 — WP1-T1 阶段模型与 StagePlanner（M1 起点）

> 依据：编排 §2、§2.1、§3；架构 §10；流程图 §2.1；输入 §6。
> 验收：阶段名为 8 个固定值；三入口映射正确；跳过阶段记原因。

### 新增
- **`core/models/pipeline.py`**：`StageKind`（8 个固定值 + `PIPELINE_ORDER`）、
  `STAGE_SUBSTEPS`（每阶段子步骤 + **幂等性/残留清理标注**：`mount`/`unmount`/`inject` 非幂等）、
  `PlannedSubstep` / `StagePlan` / `PipelinePlan`（含 `summary_lines()` 供 `--dry-run` 打印）。
- **`application/pipeline/planner.py`**：`plan_pipeline(config)` —— 把 `TaskConfig` 裁剪成确定计划：
  - `Prepare` **永不整段跳过**，起点晚于它时标 `implied`；子步骤按入口裁剪
    （A 只 `probe`；B `probe+selfcheck`；C-ISO `probe+extract+selfcheck`；C-ESD 换 `materialize`；C-WIM 只 `probe+selfcheck`）；
  - 入口 C 的 `Fetch`/`Convert` 记 `reason=input_satisfied`；
  - 未选精简/优化/集成 → `reason=no_options`；未启用无人值守 → `reason=not_enabled`；`dryRun` → `reason=dry_run`；
  - 起始阶段晚于 `Convert` 时，入口 A/B 的 `Fetch`+`Convert` 记 `reason=user_start_stage`；
  - 早于输入支持的最早阶段 → `STAGE_TOO_EARLY`（退出码 4）。

### 决策 / 规范澄清
| # | 项 | 结论 |
| --- | --- | --- |
| D1 | 规范冲突：编排 §2 的跳过条件不含 `startStage`，配置 §3.2 的举例含 | 取可产品化的折中并在 `配置 §3.2` 登记：**只有 `startStage` 晚于 `Convert` 才跳过 `Fetch`+`Convert`**；`startStage=Convert` 时 `Fetch` 仍执行，否则入口 A 没有素材可用 |
| D2 | `Fetch` 是否可作为起点 | **不可以**：配置 §3.2 的枚举只有 7 个值；V2 从「允许」收紧为「拒绝」，并给出可选清单 |
| D3 | 跳过原因取值 | 沿用文档的 `no_options`/`input_satisfied`/`not_enabled`/`dry_run`/`user_start_stage`，另加 `merged_into_fetch_verify`（入口 A 的 `selfcheck` 并入 `Fetch.verify`） |
| D4 | 计划是否做运行期判定 | 不做：`uup\` 已全部 verified 导致的跳过、清单有效性等由编排层（WP1-T2）决定，计划期不猜 |

### 测试
- `test_pipeline_planner.py`（26）：8 阶段唯一与固定顺序、历史名降级为子步骤、三入口的 `Prepare` 子步骤裁剪、
  C 的 `input_satisfied`、A/B 起始阶段语义、`Prepare` 永不跳过、`STAGE_TOO_EARLY`、未知阶段名、
  选项阶段开关、`dryRun` 跳过 `Output`、挂载类非幂等标注、`aggregate`/`commit` 幂等、跳过阶段子步骤全带原因、
  摘要可读、计划确定性、压缩选项不影响裁剪。
- `test_config_validation.py`（+1）：`startStage=Fetch` 被 V2 拒绝。
- 全量：`pytest 525 passed`（新增 27 项）；`mypy 91 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `配置` v0.3 → **v0.4**：§3.2 补「落地澄清」（`startStage` 与 `Fetch` 的真实关系、`Fetch` 不可作起点）。
- `术语表 §1.2` 同步；README 的 M1 描述更新为「WS0 底座已就绪」。

### 已知限制
- 计划只覆盖「该不该跑、跑哪些子步骤」，**不产出命令行与将复用产物**——那是 WP1-T2 的 `--dry-run` 输出。
- 阶段进度权重（`stage_percent = base + weight × tool_percent`）未定义：属 GUI/编排（WP9-T13 / WP1-T2）共同决策。
- 入口 A 的 `Prepare` 只有 `probe`：与 `Fetch.verify` 的合并顺序在 T2 落实前不保证。
- `plan_pipeline` 不读磁盘，因此无法判断「`uup\` 是否已下载完」这类运行期事实。

## 0.1.18 — WP0-T11 单实例守卫（WS0 收尾）

> 依据：日志 §6.1、§6.2；流程图 §19；需求 FR9.4。
> 验收：冲突实例不产生日志文件、不执行清理、不写审计；GUI 激活已有窗口退 0，CLI 退 5。

### 新增
- **`infrastructure/single_instance.py`**：`SingleInstanceGuard` —— 命名互斥量
  `Local\UUP2ISO.SingleInstance`；`acquire()` 返回布尔（不抛错，由调用方决定「激活」还是「退 5」）；
  冲突时主动关闭自己那份句柄，避免「续命」别人的互斥量。
- **`application/startup.py`**：`bootstrap()` —— 单实例 → 建日志 → 补审计只读 → 清理 → 写启动日志，
  **顺序即契约**；`StartupConflict`（退出码 5，带 `{"error":"already_running","code":5}` 载荷）。
- **`gui/activation.py`**：`ActivationServer` / `activate_running_instance()` —— 基于 `QLocalServer`/`QLocalSocket`
  的窗口激活通道（放 gui/ 是因为 PySide6 只允许出现在该层）。
- **`gui/app.py`**：接线完整启动序列 —— 抢不到守卫就激活已有窗口并退 **0**（不建日志）；
  抢到后开监听、走 bootstrap、进事件循环，退出时先关日志（审计置只读）再释放守卫。
- **`cli/main.py`**：根回调为每个命令抢守卫；冲突时向 stderr 输出 JSON 并 `Exit(5)`。
- `PruneReport.changed` 语义修正为「运行池或审计池任一有删除」（此前只算运行池，
  导致「只删审计」时启动摘要不写日志）。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 用内核互斥量而非锁文件 | 进程被强杀时内核自动释放，不留僵尸锁 |
| D2 | `acquire()` 返回布尔而不是抛异常 | GUI 需要「失败→激活→退 0」，CLI 需要「失败→JSON→退 5」，策略在调用方 |
| D3 | 启动日志的级别字段命名为 `log_level` | `LogSink.log()` 已有同名参数 `level`，撞名会 TypeError（本轮踩到） |
| D4 | **`version` 不参与互斥**（已登记的偏差） | 只读、不写日志、不争资源；否则「开着窗口就查不了版本」 |
| D5 | 激活通道用 Qt 本地套接字 | 跨进程、无端口冲突；`WM_COPYDATA` 需要窗口句柄，跨会话不可靠 |

### 测试
- `test_single_instance.py`（8）：首抢成功/二抢失败、重复 acquire 幂等、释放后可再抢、
  不同名互不影响、上下文管理、`already_running` 标志、无 acquire 直接 release 安全。
- `test_startup.py`（9）：建日志与级别来源、GUI 源码模式 DEBUG、**冲突不建日志**、
  **冲突不清理**（旧日志必须还在）、补审计只读、清理并写 `audit_pruned`、目录不可写时抛错且释放守卫、复用已持有的守卫。
- `test_cli_guard.py`（6）：`version` 豁免（且不尝试抢）、冲突输出 JSON 且退 5、成功返回会话、无子命令也守卫、释放回调。
- 顺带修掉一处**分层违规**：`cli`/`gui` 原先直接 import `infrastructure`，现在改为只依赖 `application`
  （新增 `StartupSession` + `acquire_instance()` / `release_instance()` 外观），分层门禁恢复绿。
- `test_gui_activation.py`（4）：激活往返、每次请求都触发、无服务端返回 False、停止后可同名重启。
- 全量：`pytest 496 passed`（新增 27 项）；`mypy 89 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `日志` v0.6 → **v0.7**：§6.1 补落地状态与「`version` 不参与互斥」的偏差登记；`术语表 §1.2` 同步。

### 已知限制
- **CLI 的 `run` 还不存在**（WP1-T5）：退出码 5 的行为已在 `acquire_cli_guard` 与 `bootstrap` 两层实现并测试，
  接命令时只需调用它们。
- **激活通道只做「置前」**：不带参数、不做任务交接；多实例协同（把待办任务递给主实例）不在本期范围。
- 僵死实例的互斥量由内核回收，但**残留的本地套接字名**靠 `QLocalServer.removeServer` 清理；
  若主实例被强杀，第二次启动的激活尝试可能失败（返回 False，仍按「已在运行」退 0）。
- 跨用户互斥不在本期范围（互斥量在 `Local\` 会话命名空间下）。

## 0.1.17 — WP0-T10 日志与进度上报模型

> 依据：日志 §2、§3、§4、§5、§7.3；进程 §5、§6、§10。
> 验收：符合日志 §2–§6；进度不入日志；审计退出后只读、启动只补最近 3 个；索引可支撑诊断包精确取文件。

### 新增（`src/uup2iso/infrastructure/logging/`）
- **`format.py`**：行格式 `<ts> [<LEVEL>] [<stage>] <logger>: <msg> | k=v`；级别 5 字符宽；
  无阶段写 `[-]`；消息强制单行（异常续行靠 `  | ` 缩进，故 `single_line` 保留两空格前缀）。
- **`sink.py`**：`LogSink` —— 每进程一 `run_*.log` + 一 `audit_*.log`（同时间戳，同秒冲突加 `_1` 且两侧同步）；
  级别过滤（WARN/ERROR 恒写）；审计写失败抛 `AuditWriteError`；关闭时审计置只读；
  `tool_started/tool_finished` 产出 `event=start|finish`；**采样兜底**（同前缀 >50 行时留「首 1 + 每 100 行 1 条 + 末 1」，末行带 `sampled=true total=<n>`）。
- **`levels.py`**：exe→INFO / 源码 GUI→DEBUG / CLI→INFO / `--debug`→DEBUG，并回报级别来源。
- **`cleanup.py`**：三条件清理（大小/天数/数量，任一超限即删最旧），运行池含 `diag_*.zip`、审计池独立；
  当前文件永不删；审计删除前解除只读并回报 `audit_deleted`；`ensure_audit_readonly` **只处理最近 3 个**。
- **`index.py`**：`TaskLogIndex`（写 `tasks\<id>.json` 的 `logFiles`/`auditFiles`，去重保序、保留其它字段）+
  `collect_task_log`（只按索引取文件、逐行过滤 `task=<id>`、记 `matched_lines`+SHA256、合并行数必须等于来源之和）。
- **`redact.py`**：代理凭据、URL 内嵌口令（含含 `@` 的密码）、产品密钥（留末 5 位）、URL 签名参数；幂等。
- **`src/uup2iso/infrastructure/progress/hub.py`**：`ProgressHub` —— 单调不减（丢弃回退）+ 节流（200ms/键 ≈ 5 次/秒）+
  订阅/快照；`attach_log()` 故意抛错，谁想把进度接日志会被当场拦住。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 采样按「同前缀 + 抹掉数字」分组 | 工具刷屏行只有数字不同；按原文分组等于不分组 |
| D2 | 只读用文件属性（`chmod` 去写位）实现 | 与 `attrib +R` 等价、可测；文档已声明「防误改而非安全边界」 |
| D3 | 审计写失败抛 `AuditWriteError`（退出码 4） | 日志 §5.3「不得静默放行」，由调用方阻断高风险操作 |
| D4 | 索引写在任务文件内而非单独文件 | 与 `tasks\<id>.json` 同生命周期，少一个可能丢的文件 |
| D5 | 进度独立成 hub，不接日志 | 规则相反（日志求少、进度求频），物理隔开最省心 |

### 测试
- `test_logging_format.py`（19）：行结构/级别宽度/单行压平/字段跳过 None/脱敏四类/级别判定矩阵。
- `test_logging_sink.py`（15）：同秒冲突成对、级别过滤、**进度不落盘**、审计分流与只读、采样封顶、异常续行、脱敏落盘前生效。
- `test_logging_cleanup.py`（16）：只读往返、只补最近 3 个、按数量/天数/大小清理、诊断包同池、当前文件保护、审计解除只读后删除、阈值下限。
- `test_logging_index.py`（11）：去重保序、保留其它字段、过滤不多不漏、缺失标 `exact=false`、索引缺失回退。
- `test_progress_hub.py`（8）：单调、节流、未定态、订阅/取消、快照、reset、禁止接日志。
- 全量：`pytest 467 passed`；`mypy 86 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `日志` v0.5 → **v0.6**：§9 前补「落地状态」，明确已实现范围与未接线项；`术语表 §1.2` 同步。

### 已知限制
- **尚未接线**：`LogSink` 还没有注入 `ProcessRunner` / CLI / GUI（属 WP1-T2、WP1-T6、WP9-T17）。
  现在它能用，但没有任何主流程在调它。
- **诊断包未实现**：`collect_task_log` 已提供"按任务合并"的原料，zip 打包与 `manifest.json` 属 WP9-T19。
- **单实例未实现**：日志 §2.2 要求"单实例校验通过后才建日志"，这个顺序要等 WP0-T11 才能落。
- **只读不是安全边界**：管理员仍可改回；更强不可篡改（ACL/只读归档）是日志 §10 的开放问题。
- 审计只读失败（如文件被占用）目前静默忽略（`set_readonly` 吞 OSError），诊断包生成前需要复核属性时再收紧。

## 0.1.16 — WP0-T9 规则库 Schema 与加载器

> 依据：规则 §2、§4、§8。验收：非法规则被拒绝并报错；支持外置更新。

### 新增（`src/uup2iso/infrastructure/rules/`）
- **`loader.py`**：`load_library()` —— 逐包 JSON Schema 校验 → 跨包 id 唯一 → `requires`/`conflicts`
  引用存在 → 无环（单独报 `RULEPACK_CYCLIC` 并给出环路 `A → B → A`）→ 高风险必备字段 → `minEngine` 过滤。
  **整体拒绝、不做部分加载**（规则 §8）。
  查找顺序支持外置更新：`程序目录\rules` → 包内 `uup2iso/rules` → 仓库 `docs/规范/rules`。
- **`matrix.py`**：`dependency-matrix.json` 模型 + `merge_observed()` —— 手写硬约束为准，
  实测数据只补全；`providedBy` 冲突记 `matrix_conflict capability=<n> keep=hard`，且**观测数据不参与阻断**。
- **`selection.py`**：`evaluate_selection()` —— 规则 §4.3 的逐项阻断（缺依赖 / 互斥 / 禁止组合 / 高风险未确认 /
  未知 id），`allowed` 按 `(order, id)` 排序；`warning` 级组合放行但进 `requires_confirmation`。
- **`scripts/check_rules.py`**：独立校验入口（退出码 2 表示规则库非法），后续可直接接 WP10-T4 的 CI 门禁。
- `core/models/rulepack.py`：`Rule` / `RulePack` / `RuleLibrary` / `RulePackIssue`。

### 数据修复（按规则 §2.2 的硬要求）
| 规则 | 问题 | 处理 |
| --- | --- | --- |
| `FR4-SL-P01` 移除 Defender | `level=high` 但没有 `verify` / `recovery` | 补 `verify: capabilityAbsent(Windows-Defender)`、`reversible: false`、不可逆恢复说明 |
| `FR5-OP-T60` UAC 级别 | 同上 | 补 `verify: regValue(ConsentPromptBehaviorAdmin=0)`、`reversible: true`、恢复步骤 |

> 这两条此前按 §2.2 属「规则库无效」，现在四个字段补齐后整体通过校验。
- 新增 `docs/规范/rules/dependency-matrix.json` 骨架（`capabilities` / `blockedCombos` 暂为空）。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 任何一条非法即整体拒绝 | 「一半规则生效」比「完全没生效」更难排查 |
| D2 | 环单独用 `RULEPACK_CYCLIC` | 与普通非法区分开，报错里直接给环路，便于修数据 |
| D3 | 实测观测数据不参与阻断 | 规则 §4.1：只有手写硬约束能阻断，实测只补全 |
| D4 | `minEngine` 只跳过不报错 | 规则 §8 明确「跳过并提示」 |
| D5 | 高风险口径 = `level=high`；`recovery` 仅在不可逆时必填 | 与 §2.2 原文一致（`reversible` 缺省按 false 处理） |

### 测试
- `test_rules_loader.py`（15）：随包 30 条规则可加载、preset 归属、高风险可验证、重复 id / 悬空引用 /
  Schema 违规 / 环（含自环）/ 空包一律拒绝、`minEngine` 跳过与放行。
- `test_rules_matrix.py`（9）：缺文件回退空矩阵、非法结构报错、补全、冲突保留手写并告警、观测不参与阻断。
- `test_rules_selection.py`（10）：缺依赖、互斥二选一、高风险确认、未知 id、`block`/`warning` 两种组合、
  排序、空选择、真实预设回归。
- 真实校验：`scripts/check_rules.py` → `2 个包 / 30 条规则`，preset `aggressive` 30 / `standard` 22 / `safe` 12。
- 全量：`pytest 389 passed`；`mypy 77 files`；`ruff`/`format` 全绿。

### 变更（文档）
- `规则` v0.2 → **v0.3**：§8 增「校验落地」一行，并补落地状态与两条高风险数据补齐的说明。
- `术语表 §1.2` 同步。

### 已知限制
- **依赖矩阵内容为空**：`capabilities` / `blockedCombos` 的实质条目随 WP4-T1（规则全量化）与 WP4-T2 填充，
  所以当前阻断只看 `requires`/`conflicts`/高风险确认，禁止组合暂不生效。
- **幂等无法静态校验**：规则 §5.2 的「幂等是入库准入条件」目前只能靠评审，加载器无法自动判断
  `runCommand` 之类动作是否幂等。
- **外置规则包未签名**：支持从 `程序目录\rules` 加载，但只做 Schema + 语义校验，不校验来源可信性；
  规则包信任模型（是否随包/是否允许用户导入）属 WP11-T5。
- 尚未与 `TaskConfig.slim.ruleIds` 打通：选择器的输入由调用方提供，接入编排在 WP1-T2 / WP4-T3。

## 0.1.15 — WP0-T8 工具缺失自动获取

> 依据：引擎 §9.1、§9.2；进程 §2.1。验收：干净机器可自动补齐；哈希不符即拒绝；失败给手动指引。

### 新增
- **`infrastructure/tools/fetcher.py`**：`ToolFetcher` —— 按清单 URL 下载 → SHA256 校验 → 解压到暂存 →
  原子落位（`tools\<工具名>\<exe>`，同时带上同目录 DLL）。
  - **只解 zip**：7-Zip 官方只发 `.exe/.msi/.7z`，解 `.7z` 需 7z 自身（引导悖论），故 7z 走随包内置；
  - **域名白名单**（HTTPS 限定）：`github.com` / `release-assets.githubusercontent.com` /
    `objects.githubusercontent.com` / `wimlib.net` / `7-zip.org`；
  - **手工放置通道**：受限网络下把 `<name>-<version>.zip` 放进 `tools\.download\`，哈希一致即安装
    （引擎 §9.2 要求失败时给 URL + 哈希指引，这条让指引可落地）；
  - zip-slip 防护与「找不到目标 exe」显式报错。
- **`scripts/fetch_tools.py`**：`--check` 只体检、`--only` 指定工具、`--update-lock` 回填 exe 哈希。
- **`tools/tools.lock.json` 填充真实条目**（哈希由我们下载后自算，未采用任何上游校验值）：

| 工具 | 版本 | 下载包 sha256 | exe sha256 | 许可 |
| --- | --- | --- | --- | --- |
| aria2c | 1.37.0 | `67d015301eef…` | `be2099c214f6…` | GPL-2.0 |
| wimlib-imagex | 1.14.4 | `6d99e242bfbc…` | `401bf99d6dec…` | LGPL-3.0-or-later |

- `ToolProvider.ensure()` 接入 fetcher：缺失时先尝试自动补齐，再定位；仍失败才抛 `TOOL_MISSING`。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 只有 aria2c / wimlib 进清单 | 只有它们官方提供 zip；7z 走内置、DISM 系统自带、oscdimg 属 ADK、转换器随包 |
| D2 | 清单**同时**记 `sha256`（下载包）与 `exeSha256`（可执行文件） | 两者混用会把刚装好的工具判成被篡改（本轮实测踩到） |
| D3 | 手工放置压缩包即视作合法来源 | 哈希仍是唯一信任根，文件从哪来不影响判定 |
| D4 | 工具二进制**不入库**（`.gitignore`），清单入库 | 体积与许可；分发包由 WP11-T2 统一携带 |

### 真实联调发现（本轮首次跑真工具）
1. **aria2c 的版本号是输出的第一行**，而 `ProcessResult.last_lines` 只留最后 20 行 → 被截掉。
   修法：探测时用 `on_event` 另外收集**前 5 行**，与前缀一起匹配。
2. **非管理员下 `dism /Version` 直接返回 740**（需要提升权限），不是「版本解析失败」。
   修法：识别 `740` → 记 `capability_degraded capability=admin` + `WARN tool_admin_required`，
   对应进程 §2.4「非管理员明确拒绝，而不是跑到一半才失败」。
3. **httpx 经 HTTP 代理访问 GitHub release 资产不稳定**（`RemoteProtocolError` / `ProxyError 502` /
   `ReadTimeout`，同一 URL 用 curl 正常）→ 加缓冲回退 + 换新连接 + 手工放置通道三重兜底。
   属环境问题，已在「已知限制」记录。

### 测试
- `test_tools_fetcher.py`（12）：域名白名单三种非法 URL、哈希不符不落位、zip-slip 拒绝、
  嵌套目录找 exe、DLL 一起落位、手工放置跳过下载、过期中转包被丢弃。
- `test_tools_lock.py`（+2）、`test_tools_provider.py`（+2）：zip/exe 哈希分离、exe 哈希不符即拒、
  `ensure` 走 fetcher 补齐。
- 全量：`pytest 350 passed`；`mypy`/`ruff`/`format` 全绿。

### 变更（文档）
- `引擎` v0.4 → **v0.5**：§9.1 示例补 `exeSha256`，并写明「下载包哈希 ≠ exe 哈希」的原因。
- `.gitignore`：忽略 `tools/.download/` 与各工具目录，`tools.lock.json` 仍入库。

### 已知限制
- **7z / oscdimg / 转换器未自动获取**：7z 随包内置（官方无 zip）、oscdimg 属 ADK（不自动获取）、
  转换器随包且不参与对齐。它们的 `exeSha256` 只能在打包流程（WP11-T2）里回填。
- **WP0-T8a（按清单对齐版本）未做**：现在只在「缺失」时补齐，不做「已存在但版本/哈希不符」的对齐。
- 手工放置通道要求文件名严格为 `<工具名>-<版本>.zip`，且哈希必须在清单里（不认任意文件）。
- 本轮联调环境为**非管理员**，DISM 的版本探测未能取到真版本号；管理员下的实际版本需在你的目标机实测。

## 0.1.14 — WP0-T7 工具提供与自检

> 依据：引擎 §3.1、§3.2、§4、§5、§9.1；进程 §2.1–§2.4。
> 验收：缺失与降级均可在日志解释；`capability_degraded` 可查。

### 新增（`src/uup2iso/infrastructure/tools/`）
- `lock.py`：`tools\tools.lock.json` 解析（`LockedTool`/`ToolLock`）、**按 §9.1 自指定义计算 `identitySha256`**
  （置空该字段 → 规范化序列化 → SHA256）、篡改检出后回退内置只读副本。
- `capabilities.py`：版本 → `EngineCapabilities` 推导；阈值表标注「待实测校准」；**版本未知即保守**
  （无 LZMS、无 `--ref`）。
- `route.py`：引擎 §3.2 的 R1–R11 静态路由表 + `select_route()`；R3 明确**不降级**、R5 无备用、R6/R7 仅 DISM。
- `provider.py`：`ToolProvider` 实现 `IToolProvider` —— 定位（`tools\ → 设置 → PATH → System32`）、
  白名单与 SHA256 校验、版本探测（10s 超时）、`probe_all()` 自检报告、能力缓存、
  管理员判定；`ensure()` 缺失时抛 `TOOL_MISSING` 并给手动指引。
- `tools/tools.lock.json`：随包**空注册表**（见决策 D1），含 `note` 说明填充计划。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 清单先发空注册表，工具记 `unmanaged`（可定位、不校验哈希） | 真实 SHA256 必须自算，绝不编造；填充属 WP0-T8 / WP11-T2 |
| D2 | 清单自校验严格按 §9.1 自指定义 | 文档明确禁止对含当前值的文本求哈希 |
| D3 | 版本阈值写成常量表并标注待校准，未知即保守 | 引擎 §4.1 原文即「阈值待实测校准」 |
| D4 | 降级以结构化 `ToolReport` 承载，`.log_lines()` 输出 `event=tool_check` / `capability_degraded` | LogSink 在 T10，先产出可直接写日志的形态 |
| D5 | `ensure()` 只做「找不到→抛错+指引」，不实现下载 | 自动获取是 WP0-T8 的职责，不在此半实现 |

### 自检输出（可 grep）
```
event=tool_check tool=aria2c found=yes source=packaged version=1.37.0 managed=no path=...\tools\aria2c.exe
event=tool_check tool=wimlib-imagex found=yes source=packaged version=1.11.0 managed=no
capability_degraded tool=wimlib-imagex capability=esd_output reason=wimlib 版本低于写 LZMS 的阈值，ESD 输出不可用
WARN tool_unmanaged tool=aria2c
```

### 测试
- `test_tools_lock.py`（9）：自指哈希与写入值无关、篡改检出、缺失/损坏回退、写读回环、随包清单自洽。
- `test_tools_capabilities.py`（8）：版本解析、比较、wimlib 新旧两档、未知版本保守、DISM 挂载/不校验。
- `test_tools_route.py`（7）：11 条路由齐备、主备切换、**R3 不降级**、R5 无备用、R6 仅 DISM。
- `test_tools_provider.py`（20）：定位三级优先级、哈希不符即拒、转换器目录+`version.txt`、
  版本探测与能力联动、无法解析即降级、必需缺失阻断、`oscdimg` 缺失不阻断且提示 ADK、
  `ensure` 抛错带指引、能力缓存落盘、`select_route` 联动、管理员注入。
- 全量：`pytest 334 passed`；`mypy 71 files`；`ruff` / `format` 全绿。

### 变更（文档）
- `引擎` v0.3 → **v0.4**：§9.1 补「清单落地状态」（空注册表 + unmanaged + 填充计划）；`术语表 §1.2` 同步。

### 已知限制
- **清单为空**：当前没有任何哈希校验，用户名下被替换的工具不会被发现，只记 `tool_unmanaged`。
- **`oscdimg` 版本恒为 unknown**：它没有版本参数，需读 PE 版本资源（未实现），因此恒进保守模式且不参与
  ESD/LZMS 判定（它本来也只做封装）。
- **未实现自动获取**（`ensure` 不下载）、**未实现「按清单对齐」**：均属 WP0-T8 / WP0-T8a。
- 能力缓存只按 `size/mtime` 失效，未在缓存里存版本比对逻辑；缓存损坏直接忽略（下次重探）。
- 真实工具联调未做：`probe_all` 已具备真跑能力，但 M0 环境没有这些 exe，本轮全部用夹具回放验证。

## 0.1.13 — WP0-T6 设置/预设/任务配置存储

> 依据：配置 §2、§3、§4、§8、§9；架构 §9。验收：符合配置 §3–§4、未知字段可忽略可严格、指纹排除路径。

### 新增
- **`core/models/config.py`（pydantic v2）**：`TaskConfig` 及 15 个配置段模型，覆盖配置 §3.1–§3.5 的全部字段
  （含 FR3 的 CV-01…CV-29 承载字段），并提供：
  - `TaskConfig.fingerprint()`：`sha256(canonical_json)`，**排除 meta / `input.*.path` / `output.dir` / `fetch.proxy`**（§8.2）；
  - `TaskConfig.check_semantics()`：V2 语义校验（20+ 条规则，见下）；
  - `iter_unknown_fields()`：递归收集未知字段路径（§7）。
- **`core/models/preset.py`**：`PresetDocument` + `check_preset()`；规则类预设按 `ruleId` 校验，
  未知 id 整份拒绝；配置类预设走片段模型校验（§4.1–§4.2）。
- **`infrastructure/storage/config_loader.py`**：V0（UTF-8/无 BOM/JSON 语法）→ 版本与迁移 → V1（draft-07 Schema）
  → 未知字段策略 → V2 语义 → 指纹；`save_task_config()` 原子写（临时文件 + `Path.replace`）。
- **`infrastructure/storage/settings_store.py`**：`config\settings.json`，点分键读写、读永不抛错、
  保存原子、`as_mapping()` 对含 passwd/token/secret 的键脱敏。
- **`infrastructure/storage/preset_store.py`**：`presets\<kind>\<id>.json` 的增删查与导入导出。
- **`src/uup2iso/schemas/taskconfig.schema.json`**：由模型生成的权威 Schema（draft-07，22 KB），
  作为包数据随包发布；`scripts/gen_schema.py --check` 与测试共同守护「模型改了却没重新生成」。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | `TaskConfig` 用 pydantic v2（core 的纯值对象仍是 dataclass） | T3 已预留；配置要 Schema/序列化/校验 |
| D2 | 数值**范围**放 V2 而非 `Field(ge=..)` | 配置 §5 要求超范围报 `CONFIG_SEMANTIC_INVALID`，pydantic 约束会落成 Schema 错误 |
| D3 | `extra="allow"` + 路径收集；严格模式由加载器判定 | §7 默认忽略并收集，严格开关唯一归属 TaskConfig（§3.5） |
| D4 | Schema 单一来源放包内，打包时复制到 `程序目录\schemas\` | 避免仓库根与包内两份副本互相漂移；已回写 配置 §2 |
| D5 | 语义错误按**首条问题**的具体码抛出（如 `OUTPUT_TYPE_UNSUPPORTED`） | SWM 是「能力未验证」不是「配置写错」，GUI 要能分开呈现 |
| D6 | `startStage` 默认 `Convert`；在线入口可选最早阶段取 `Convert` | 配置 §3.2 的枚举不含 `Fetch`，否则默认配置一创建就判自己语义非法 |

### V2 语义规则（`check_semantics`）
`startStage` 合法性与「不早于输入支持的最早阶段」、`mode=online` 必须有 `source`、
`mode=image` 必须有 `path`+`editions`（且索引在探测表内）、`mode=uupFolder` 必须有路径、
`virtualEdition.enabled`/`esdDecrypt.enabled` 一律拒绝（MVP）、`resetBase` 依赖 `componentCleanup`、
保守模式下禁 `LZMS` 与 ESD 输出、`output.type=swm` 拒绝、`fetch.split`/`timeout`/`apps.level`/
`hangWarnMinutes`/`verifyLevel` 范围检查（**不夹紧**）。

### 测试
- `test_config_models.py`（10）：必填项、§5 默认值逐条对照、指纹排除路径/meta/代理、未知字段收集、别名序列化。
- `test_config_validation.py`（30）：V0 三种失败（BOM/坏 JSON/非 UTF-8）、V1（缺段/错枚举/错类型）、
  V2 全部规则、未知字段松紧两模式与参数覆盖、迁移（备份、幂等、缺迁移器不改原文件）、原子保存。
- `test_config_schema.py`（7）：Schema 不漂移、draft-07 合法、必填项、未知字段放行、样例配置通过。
- `test_settings_store.py`（7）+ `test_preset_store.py`（12）：默认值、点分键、脱敏、预设增删查与导入导出、
  未知 ruleId 拒绝。
- 全量：`pytest 286 passed`；`mypy 66 files`；`ruff` / `format` 全绿。

### 变更（文档）
- `GUI框架` v0.10 → **v0.11**：`core` 的「无外部依赖」注明 pydantic 例外。
- `配置` v0.2 → **v0.3**：§2 补 Schema 单一来源与打包位置说明。
- `术语表 §1.2` 同步两处版本。

### 已知限制
- **`UnattendSection` 只落核心字段**（`computerName`/`productKey`/分区/账户等完整字段属 WP7-T2），
  当前靠 `extra="allow"` 保证预设互导不失败。
- **迁移器注册表为空**：只有 1.0 一个版本，暂无需要迁移的旧格式；迁移路径已用测试注入的
  迁移器验证（备份、幂等、失败不改写）。
- **未实现 ConfigResolver**（架构 §9 的 TaskConfig > settings 合并）：留给 WP1-T2/T5，本轮只做两套存储。
- 路径存在性只提示不阻断（§6）；`path_not_portable` 仅在绝对路径不存在时产生。

## 0.1.12 — WP0-T5 ProcessRunner 与解析器

> 依据：进程 §3、§4、§5、§7、§9；流程图 §17。验收：外部进程不进 UI 线程、错误对象含原码/通道/最后 20 行、
> 并发固定镜像 1 / 下载 1、夹具回放测试通过。

### 新增（`src/uup2iso/infrastructure/process/`，9 个模块）
- `runner.py`：`ProcessRunner` —— 并发闸门 → 启动 → 双路读取 → 解码/分类 → 取消阶梯 → 看门狗 → 错误组装。
  取消按 §7.1 四步（Ctrl-Break 5s → terminate 2s → `taskkill /T /F`），**无控制台时跳过第一步**。
- `backend.py`：`RealBackend`（`asyncio.create_subprocess_exec` + `CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP`）
  与 `FakeBackend`（`.jsonl` 夹具回放，§11 要求的 `FakeProcessBackend`）；`RunSpec` 承载工具/参数/工作目录。
- `decoder.py`：`LineSplitter` 兼容 `\n`/`\r\n`/**单独 `\r` 重绘**、64 KB 截断、无换行残留（`partial`）；
  `decode_line` 走「首选 → utf-8 → cp936 → latin-1」回退并回报实际编码。
- `classifier.py`：`LineClassifier` 按 §5.2 四步判定；版本未登记时严格兜底（含 `%`/`\r` 且不含错误词才算进度）。
- `profiles.py`：工具画像（编码 / IO 画像 / 并发资源），编码按 §4.2（DISM/7z/aria2c/oscdimg=cp936，wimlib=utf-8）。
- `watchdog.py`：四信号联合 + 窗口数由参数算出（默认 20 个）；**只告警不杀**；采样失败按「有活动」。
- `sampler.py`：ctypes 调 `GetProcessTimes` / `GetProcessIoCounters` / `GetIfTable`，
  按**进程树**累加 CPU 与 IO，网络取系统级收发（§7.2），不引入 psutil。
- `errors.py`：`ToolExecutionError`(2) / `ToolStartError`(4) / `PermissionDeniedError`(4) / `ToolMissingError`(4)，
  以及 `build_tool_failure()`（工具、返回码、耗时、通道、HRESULT、末 20 行、脱敏命令）与 `redact_command()`。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 正则先内置为 Python 常量，保留 `from_mapping()` | YAML 解析需额外依赖，而 WP0-T2 清单未含；已回写 进程 §5.3 说明 |
| D2 | 进程树 CPU/IO 用 ctypes 枚举子进程累加，网络用 `GetIfTable` | 规范要求「进程树累计」且禁 psutil；老 API 结构简单、够用 |
| D3 | Ctrl-Break 仅在 `GetConsoleWindow()!=0` 时执行 | 进程 §7.1：GUI/pythonw 下第一步必然空转 |
| D4 | 采样失败一律按「有活动」 | 宁可漏报也不误杀（§7.2 的取向） |
| D5 | 并发锁固定 `image`=1 / `download`=1，注入并发则忽略并记 `concurrency_ignored` | 进程 §8「不提供可配置性」 |

### 测试
- 夹具 `tests/fixtures/process/{dism,wimlib,aria2c}/*.jsonl`（真机输出形态，含进度行、HRESULT 失败、正常完成）。
- `test_process_decode.py`（13）、`test_process_classify.py`（10）、`test_process_watchdog.py`（11）、
  `test_process_runner.py`（20）：成功回放、失败现场信息、取消两条分支、并发固定、超长行/无换行残留、编码回退、脱敏。
- 全量：`pytest 217 passed`；`mypy 61 files`；`ruff` / `format` 全绿。

### 变更（文档）
- `进程` v0.3 → **v0.4**：§5.3 补「正则表落地状态」——M0 用 Python 常量，YAML 待规则库任务决定；`术语表 §1.2` 同步。

### 已知限制
- **进度模型（§6）未实现**：本层只做 `progress` 分类与计数，单调递增、5 次/秒节流、`stage_percent` 权重
  属编排/GUI 侧（WP1-T2 / WP9-T13），当前进度只经 `on_event` 抛出。
- **网络信号是系统级**：别的程序有流量时不会告警（保守取向，§7.2 已声明）；按进程统计需 ETW，未做。
- **未做真实工具联调**：全部验证走夹具；首个真实对接在 WP0-T7（工具自检）时补，
  届时需在你目标系统实测 Ctrl-Break 分支（进程 §7.1 要求 M0 实测确认）。
- `on_event` 是同步回调：GUI 侧必须自行切回 UI 线程（信号槽），本层不替调用方跨线程。

## 0.1.11 — WP0-T4 统一错误模型与退出码

> 依据：GUI 框架 §5.6、进程 §9.1/§9.2、需求 FR9.3。验收：错误对象含阶段/文件/工具/返回码/原始片段。

### 新增（`src/uup2iso/core/errors/`）
- `exit_code.py`：`ExitCode`（0 成功 / 1 警告 / 2 失败 / 3 取消 / 4 参数或依赖 / 5 已有实例），
  与 GUI 框架 §5.6 逐条对齐；`is_success` 把 0 与 1 都算完成。
- `codes.py`：`ErrorCode` 38 个稳定码，全部取自文档（下载 §2.4/§3.2、输入 §3/§9、配置 §2/§3.5、
  进程 §9.1、输出 §4/§6、规则 §8、集成 §5、无人值守 §5、日志 §6.1），并提供 `slug` 生成小写形式。
- `context.py`：`ErrorContext`（阶段/子步骤/文件/工具/原始返回码/命令/通道/HRESULT/耗时/输出片段）
  + `tail_lines()`（默认 20 行，进程 §9.2）+ `extract_hresult()`（取最后一个 `0x????????`）。
- `base.py`：`Uup2IsoError` 及其分类子类 `UsageError` / `DependencyError` / `InputRejectedError`（均 4）、
  `StageExecutionError`（2）、`UserCancelledError`（3）、`AlreadyRunningError`（5）、`InternalError`（2）；
  `to_dict()` 输出日志 §6.1 的 `{"error":..,"code":..,"message":..}` + 现场字段；`exit_code_for()` 兜底为 2。

### 决策
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 异常**自己带退出码**，能力代码只抛语义化子类 | 退出码不散落在 CLI/GUI 的 if-else 里 |
| D2 | 除取消/单实例/内部错误外，**`code` 必填**，缺省即 `TypeError` | 防止兜底成 `INTERNAL_ERROR` 掩盖分类缺失 |
| D3 | 退出码 1（有警告）**不是异常**，由编排层按警告折算 | 日志 §5.3 要求「部分成功」= succeeded + 失败明细 + 退出码 1 |
| D4 | 错误码只收录文档已登记的，新增必须先改规范 | 守住「先改文档再改代码」 |

### 测试
- `tests/test_errors.py`（25 项）：退出码表与逐类映射、缺 `code` 必须报错、`to_dict` 形状与现场字段、
  `tail_lines` 20 行截断、`extract_hresult` 取末尾、`exit_code_for` 对未知异常返回 2。
- 全量：`pytest 151 passed`；`mypy 52 files`；`ruff check` / `ruff format --check` 全绿。

### 变更
- 异常命名遵循 ruff N818（错误类以 `Error` 结尾）：`StageFailure` → `StageExecutionError`、
  `Cancelled` → `UserCancelledError`（后者刻意避开 `asyncio.CancelledError` 的语义混淆）。

### 已知限制
- 尚未在实际业务流程中使用：CLI 的 JSON 错误输出与退出码映射属 WP1-T6，届时改为直接 `error.to_dict()`。
- `ErrorCode` 仅覆盖文档已登记的码；`OUTPUT_VERIFY_FAILED` 之类「可能将来需要」的码未提前发明。
- 异常→重试白名单（编排 §7）不在本轮：重试判定需要阶段上下文，归 WP1-T2。

## 0.1.10 — 修复 GUI 入口：窗口秒退 + 进程无法退出

> 触发：手动运行 `.venv\Scripts\uup2iso-gui.exe`（或 `python -m uup2iso.gui.app`）时，
> 窗口闪一下就消失，但控制台停在 `QFluentWidgets Pro` 提示行不返回，Ctrl+C / Ctrl+D / exit 均无效。
> 这是 M0 的一次漏检：`tests/test_gui_smoke.py` 只覆盖了「能创建窗口」，没覆盖 `main()` 的真实启动路径。

### 修复
- **窗口被提前回收**（`gui/app.py` `run_event_loop`）：原代码 `create_main_window().show()` 的返回值是
  临时对象，`show()` 返回后立即被 CPython 回收，底层 `QMainWindow` 随之析构 → 窗口消失；
  而 `QApplication.exec()` 仍在空转，于是出现「窗口没了、进程不退」的组合症状。
  改为先绑定局部变量 `window = create_main_window()` 再 `show()`，并在 `finally` 中 `close()`。
- **Ctrl+C 无效**：Qt 的 C++ 事件循环不执行 Python 字节码，SIGINT 只被置位不会被处理。
  新增 `_install_sigint_handler()`：`signal.SIGINT → app.quit()`，并用一个 200 ms 心跳 `QTimer`
  （以 `app` 为父对象，避免自身被 GC）给解释器制造处理信号的机会。

### 变更
- `main()` 拆为「配置 app」+ `run_event_loop(app)` 两步：后者可注入替身，使「进入事件循环时窗口仍存活」
  成为可断言的事实，而不是只能靠肉眼看窗口。

### 新增
- `tests/test_gui_smoke.py::test_event_loop_keeps_window_alive`：用只实现 `exec()` 的替身 app 驱动真实
  `run_event_loop`，断言进入事件循环时顶层窗口数 ≥ 1。若有人再写成临时对象形式，这条立即红。

### 已知限制
- Ctrl+C 的响应有最长 200 ms 延迟（心跳间隔），属既定取舍；若后续引入 `qasync` 事件循环可去掉心跳。
- 本轮只验证了「窗口驻留 + 可中断」，界面仍是占位标签（WP9-T1 起替换）。
- `gui-scripts`（`uup2iso-gui.exe`）由 pythonw 启动、无控制台，崩溃时看不到 traceback；
  统一异常落盘要等 WP0-T10 的 `LogSink`，在那之前排查请用 `python -m uup2iso.gui.app`。

## 0.1.9 — WP0-T3 领域模型与接口契约

> 依据：架构 §5、§6；GUI 框架 §4.3；需求 §7.3。验收：接口编译通过、有类型注解与文档字符串。

### 新增（`core/models/`，全部为冻结值对象，`core` 无第三方依赖）
- `enums.py`：`EntryPoint`/`ImageType`/`OutputType`/`CompressionKind`/`VerifyLevel`/`TaskState`/`StageState`/`SubstepState`/`DownloadFileState`/`Architecture`/`ToolKind`/`EngineKind`/`PresetKind`。取值逐条对齐配置 §3.2–§3.4、输入 §2.1、输出 §6、术语 §2.2、引擎 §3.1。
- `build.py`：`BuildInfo`（含 `build_number` 供无人值守 §3.1 查组件版本）。
- `manifest.py`：`Manifest`/`UupFile`/`ManifestSource`，字段与下载 §3.1 一一对应；内置自校验辅助（`is_total_consistent`、`has_duplicate_names`、`required_files`）。
- `image.py`：`ImageInfo`/`Edition`（对齐输入 §4 `probe.json`；`is_rejected` 覆盖加密/多卷/分片三条拒绝规则）。
- `tools.py`：`ToolInfo`/`ToolSource`/`EngineCapabilities`（`is_conservative` 表达引擎 §5 保守模式）。
- `download.py`：`DownloadedFile`/`DownloadReport`/`DownloadFailure`（失败清单带期望/实际哈希，下载 §7）。
- `verification.py`：`VerifyReport`/`VerifyFinding`/`VerifySeverity`（输出 §6 分级）。
- `rules.py`：`RuleOutcome`/`RuleReport`（规则 §5「跳过必须可见」的承载）。

### 新增（`core/abstractions/`，8 个接口 + 2 个横切契约）
- `IUupSource`、`IDownloader`、`IToolProvider`、`IImageEngine`、`IOptimizer`、`IIsoBuilder`、`IPresetStore`、`ISettings`；`CancelToken`、`ProgressSink`。
- 全部 `@runtime_checkable`，每个成员都有文档字符串与返回类型注解，异常语义写在 `Raises:` 里（具体异常类型待 WP0-T4 定义）。

### 新增（`core/options/execution.py`）
- `DownloadOptions`/`ExportRequest`/`MountRequest`/`IsoBuildRequest`/`OptimizeRequest`：接口只接受这些窄 DTO，不直接吃 `TaskConfig`。

### 决策（本轮冻结，后续任务据此实现）
| # | 决策 | 理由 |
| --- | --- | --- |
| D1 | 模型用 `dataclass(frozen=True, slots=True)` | GUI 框架 §4.2 要求 core 无外部依赖；pydantic 留给 T6 的 TaskConfig |
| D2 | 接口取 **8 个**（补 `IToolProvider`） | 架构 §6 明确列出，T7 要挂靠 |
| D3 | I/O 接口 `async def`，纯查询同步 | GUI 框架 §9「所有 I/O 异步」 |
| D4 | 接口只接窄 DTO，不接 `TaskConfig` | 避免 core 反向依赖配置模型 |
| D5 | 取消/进度只定义 Protocol | 实现分属 T5（进程）/T10（日志） |
| D6 | `StageKind`/`PipelineContext` 留给 WP1-T1 | 属编排模型，避免与 T1 重复定义 |

### 测试
- `tests/test_models.py`（19 项）：**枚举取值逐字等于规范**（文档改而代码没改会先红）、冻结不可变、派生属性与拒绝规则。
- `tests/test_abstractions.py`（38 项）：必须是 `Protocol` 且 `runtime_checkable`、每成员有文档字符串与返回注解、参数全注解、I/O 方法为协程、纯查询非协程。
- 全量：`pytest 121 passed`；`mypy 48 files` 无错；`ruff check` / `ruff format --check` 全绿。

### 变更（文档同步）
- `项目计划` v0.22 → **v0.23**：WP0-T3 交付物改写为 8 接口 + 执行请求 DTO，`术语表 §1.2` 版本列同步。

### 已知限制
- `PipelineContext`/`Stage`/`StageKind` 未在本轮定义（决策 D6），`架构 §5` 类图中的这两项要到 WP1-T1 才落地。
- `TaskConfig` 与各 `*Options` 属 WP0-T6；本轮只有执行请求 DTO，GUI 直接绑定的配置模型尚不存在。
- 接口 `Raises:` 中提到的异常类型（`SourceUnavailableError` 等）尚未定义，WP0-T4 建立统一错误模型后才可通过 `except` 使用。
- `IOptimizer`/`IPresetStore` 的方法集在 M2 落地时可能需要追加（如 `verify_tweaks`），届时按「先改文档再改代码」补章节。

## 0.1.8 — 修复 CI 首次运行失败（控制台编码）

> 实测：CI 首个 step「环境一致性自检」直接红，`env_check.py` 第 149 行
> `UnicodeEncodeError: 'charmap' codec can't encode characters in position 2-3`。
> 5 项检查实际全部 PASS，崩溃只发生在打印中文「结果: OK」这一步。

### 修复
- **`scripts/env_check.py` 强制 UTF-8 输出**：GitHub Windows 运行器的 stdout 为 cp1252，
  中文无法编码。新增 `_ensure_utf8_stdio()`，在 `main()` 入口把 stdout/stderr
  `reconfigure(encoding="utf-8", errors="backslashreplace")`，不依赖外部环境变量；
  失败时静默降级（管道/非文本流场景）。
- **`.github/workflows/ci.yml` 显式声明编码**：job 级 `env` 增加 `PYTHONUTF8: "1"` 与
  `PYTHONIOENCODING: "utf-8"`，与脚本内重配形成双保险，也覆盖 pytest 等其它步骤的中文输出。

### 新增
- `tests/test_scripts.py::test_env_check_survives_cp1252_console`：以 `PYTHONIOENCODING=cp1252`
  起子进程复现 CI 控制台，断言退出码 0、无 `UnicodeEncodeError`、含「结果: OK」。
  把这次的 CI-only 失败变成本地可复现的门禁，不再依赖推送到远端才发现。

### 已知限制
- 本机无法完整复刻 GitHub Windows 运行器（本地 cp936 与 runner 的 cp1252 不同），
  该修复以「强制 UTF-8 + 子进程 cp1252 复现」双重手段覆盖，最终仍以 Actions 实测为准。
- 若后续还有 CI-only 失败（如 Qt 离屏插件），需按同样方式在本地构造等价条件再加门禁。

## 0.1.7 — 纳入追踪与行尾固定（首次触发 CI）

### 新增
- **`.gitattributes`**：`* text=auto eol=lf`，`*.ps1/*.cmd/*.bat` 强制 `eol=crlf`，常见二进制（png/ico/zip/exe/wim/esd/iso/msu/cab）标 `binary`。
  原因：本机 `core.autocrlf=true`，无此文件时检出后 `.py` 变 CRLF，而 `pyproject.toml` 的 `ruff format` 设为 `line-ending = "lf"`，
  会让 `ruff format --check` 在本地与 CI 结果不一致。
- **首次把工程基线纳入 Git 追踪**（此前仅有 28 个文档文件被追踪，`README.md` / `requirements*.txt` / `pyproject.toml` / `src` / `tests` / `scripts` / CI 工作流均为未追踪状态）：
  新增 `pyproject.toml`、`README.md`、`AGENTS.md`、`.python-version`、`requirements.txt`、`requirements-dev.txt`、
  `requirements.lock.txt`、`scripts/{bootstrap.ps1,check.ps1,env_check.py}`、`src/uup2iso/**`（22 个模块）、`tests/**`（5 个）、
  `.github/workflows/ci.yml`；同时提交 v1.3 文档回写（CHANGELOG、术语表、约定、计划、台账、GUI框架 5 份）。

### 变更
- CI 状态由「文件存在但从未运行」变为**首次可运行**：`push: branches: [main]` 在本次推送即触发。

### 已知限制
- CI 是否全绿以 GitHub Actions 实际结果为准；Windows 运行器上的 Qt 离屏（`QT_QPA_PLATFORM=offscreen`）与
  `pytest-qt` 组合未在本地等价前置条件下验证过，首次运行可能需要调整。

## 0.1.6 — WP0-T1 / T2 / T2a 验收闭合（环境可重建 + 文档回写）

> 触发：0.1.5 遗留 4 项（bootstrap 未端到端、无锁文件、文档 2 处未回写、CI 未实跑）。本版关闭前 3 项，
> CI 一项本机无远端，仅保留工作流文件并标注未实跑。

### 新增
- `requirements.lock.txt`：由 `bootstrap.ps1` 自动生成的依赖快照（55 个包，win-x64 / cp313），作复现基准。
- `bootstrap.ps1` 新增 `-NoUpgrade` 开关：环境已就绪或网络受限时跳过 pip/setuptools/wheel 联网升级。

### 变更（脚本健壮性，均为实测踩坑后修复）
- **PowerShell 脚本必须 UTF-8 with BOM**：`scripts/*.ps1` 原为无 BOM UTF-8，Windows PowerShell 5.1 按 cp936 解码，
  中文注释变乱码并**破坏语法**（实测 `引用运算符后面缺少属性名称`）。两个脚本已转 UTF-8 BOM + CRLF。
- **解释器探测重写**：优先复用已存在的 `.venv`（重跑不再要求系统装 3.13）；候选顺序改为
  `-PythonExe` → `py -3.13` → `py -3.12` → `python` → `python3` → 三个常见安装目录；
  失败时给出三条可执行处置建议，而不是只丢一句「请安装 3.13」。原因：本机只有 Python 3.14，
  `.venv` 实际由自带的 3.13 创建，旧逻辑会在「环境明明可用」时直接抛错。

### 变更（文档回写，S13 基线 v1.3）
- `GUI框架` v0.9 → **v0.10**：`PyQt-Fluent-Widgets` → **`PySide6-Fluent-Widgets`**（并说明 PyQt 变体禁用原因与门禁位置）；
  §3.2 语言改为 **Python 3.12–3.13（上限 `<3.14`）**；配套文档路径修正到 `docs/设计/`。
- `约定` v0.4 → **v0.5**：§2 补「解释器版本 3.12–3.13 + `requires-python` 口径」「bootstrap 一键脚本与 `-NoUpgrade`」
  「`.ps1` 必须 UTF-8 BOM（含复检命令）」。
- `计划` v0.21 → **v0.22**：WP0-T2 依赖清单改用 `PySide6-Fluent-Widgets` 并标注 PyQt 变体禁用。
- `台账` v1.2 → **v1.3**：新增 S13「M0 落地回写」并回填进度。
- `术语` v0.2 → **v0.3**：§1.2 版本列同步（GUI框架 v0.10 / 计划 v0.22 / 台账 v1.3 / 约定 v0.5）；
  §4.2 补 S13 复跑结论并记录「文件名需先屏蔽再匹配短名」的口径坑。

### 验收（可复现命令）
- `scripts/bootstrap.ps1 -NoUpgrade -SkipTests` 端到端跑通：复用 `.venv` → `pip install -e ".[dev]"` →
  `env_check.py` **5/5 PASS（结果 OK）** → 生成 `requirements.lock.txt`。
- `pip install -e ".[dev]"` 按验收原文单独执行成功（`Successfully installed uup2iso-0.1.5`）。
- 三类文档引用校验按 §4 口径重跑：**835 处（56 / 697 / 82），失效 0**，与 v1.2 记录完全一致。
- 门禁：`ruff check` / `ruff format --check` / `mypy`（30 文件）/ `pytest`（45 项）全绿。

### 已知限制
- **CI 未实跑**：仓库无 git 远端（`git remote -v` 为空），`.github/workflows/ci.yml` 仅静态存在；
  推送后需观察首次运行结果。
- **「干净机器」重建只验证到脚本层**：本机无独立安装的 Python 3.13（仅 3.14 + 打包自带的 3.13），
  无法在不复用现有 `.venv` 的前提下完整重建；临时目录重建被宿主安全策略拦截（`bulk guard` 退出 1）。
  真实干净机验证需在装好 Python 3.13 的机器上跑一次 `bootstrap.ps1 -Recreate`。
- `tests/test_layering.py` 目前扫描的目录多为占位 `__init__.py`，门禁虽有效（含自检探针），
  但要到 WP0-T3 落真实代码后才有实质约束力。

## 0.1.5 — 环境核查修复：CLI 根命令、分层门禁误报、mypy 导入根

> 触发：按 `requirements-dev.txt` 手动装依赖后做环境核查，5 项自检中 4 项通过，
> 预演测试 3 处失败。经定位**全部为代码缺陷**（非依赖缺失），本版修复。

### 修复
- **CLI 命令名失效**（`src/uup2iso/cli/main.py`）：Typer 在「仅一个命令且无根回调」时会把该命令提升为根命令，
  导致 `uup2iso version` 报 `Got unexpected extra argument(s) (version)`、退出码 2。补充 `@app.callback()`
  根回调后 `version` 恢复为子命令；后续新增 `run` 等命令也依赖该根回调。
- **分层门禁误报**（`tests/test_layering.py`）：包根文件 `src/uup2iso/__init__.py` 不属于任何一层，
  原逻辑判定 `owner is None` 直接断言失败。新增 `ALLOWED_ROOT`（伞包可引用各层），并将 `owner/allowed`
  改为成对取值；`test_layer_fixture_detects_violation` 自检语义不变。
- **mypy 无法解析自身包**（`pyproject.toml`）：`files = ["src"]` 但缺 `mypy_path`，本包未安装时
  `from uup2iso import __version__` 报 `import-not-found`（此前未被发现，是因为残留 `egg-info` 恰好充当了导入根）。
  补 `mypy_path = "src"`，不再依赖安装态。
- **格式门禁**：`tests/test_layering.py` 中超长断言不满足 `ruff format --check`，已按行宽重排。

### 变更
- 清除被中断的 `pip install -e` 残留 `src/uup2iso.egg-info`（PKG-INFO 版本为 0.1.3）：
  该目录会被 `importlib.metadata` 当作已安装发行版，导致版本断言读到陈旧值。构建产物由安装命令重新生成。

### 已知限制
- **项目本体尚未安装**：`pip install -r requirements-dev.txt` 只装第三方依赖，不装 `uup2iso` 自身。
  因此 `scripts/env_check.py` 的 `package_importable` 与 `tests/test_version.py::test_installed_metadata_matches`
  仍会失败，需补执行 `pip install -e . --no-deps`（无新增下载）。其余 44 项测试、`ruff check`、
  `ruff format --check`、`mypy` 在 `PYTHONPATH=src` 预演下均已通过。

## 0.1.4 — 依赖清单与 Python 版本固定（手动安装交付）

### 新增
- `requirements.txt`：运行期依赖清单（7 个发行版，与 `pyproject.toml` `[project] dependencies` 完全一致，
  含包名注释与「禁止 PyQt-Fluent-Widgets」警示）。
- `requirements-dev.txt`：开发依赖清单（`-r requirements.txt` + pytest / pytest-qt / dirty-equals / ruff / mypy / pyinstaller）。
- `.python-version`：`3.13`（供 pyenv 等版本管理器识别；3.14 无 PySide6 wheel）。
- `README.md` 新增「Python 版本」对照表与「方式二：手动安装」步骤（含国内镜像与装后自检命令）。

### 变更
- 手动安装成为与 `scripts/bootstrap.ps1` 等价的一等路径；依赖的**唯一权威来源仍是 `pyproject.toml`**，
  两份 requirements 为镜像副本，改依赖须两处同步。

### 已知限制
- 两份 requirements 使用版本区间（如 `PySide6>=6.8,<7`）而非精确锁定；需要逐位复现时，
  安装后执行 `pip freeze > requirements.lock.txt`，`bootstrap.ps1` 亦会自动生成该快照。
- 精确版本组合未经本机安装验证（下载时长受限），首次安装后若出现解析冲突，按 `pip` 报告放宽单一上界即可。

## 0.1.3 — M0 工程基线：工程结构 + 依赖 + 环境门禁（WP0-T1 / T2 / T2a）

> 首个**含代码**的版本。此前 0.0.x–0.1.x 均为纯文档阶段；按 `版本与变更管理.md` §1，M1 发布才推进到
> `0.2.0`，故 M0 基线取 `0.1.x` 末位递增。**若需改为 `0.2.0.devN` 语义请先改 §1 再改本文件。**

### 新增
- **工程结构（WP0-T1）**：`pyproject.toml`（setuptools + `src/` 布局）+ `src/uup2iso/` 六层骨架
  `core`（models/pipeline/options/presets/abstractions/errors）、`application`（pipeline/task_session/settings/progress）、
  `infrastructure`（source/download/imaging/optimize/iso/process/storage）、`gui`（views/presenters/controls/navigation/resources + `app.py` 组合根骨架）、`cli`（`main.py`，仅 `version` 命令）。
- **依赖声明（WP0-T2）**：运行期 PySide6、PySide6-Fluent-Widgets、httpx、pydantic、typer、jsonschema、lxml；
  `dev` 额外 pytest、pytest-qt、dirty-equals、ruff、mypy、pyinstaller。全部安装进工作空间 `.venv`。
- **门禁测试**：`test_version.py`（三处版本同步 + requires-python）、`test_layering.py`（AST 扫描六层依赖方向，
  含自检探针防门禁空转）、`test_dependencies.py`（声明/安装一致性、UI 库仅限 gui、禁止 PyQt6 与 PySide6 并存）、
  `test_gui_smoke.py`（离屏创建主窗口 + CLI `version` 冒烟）。
- **环境一致性（WP0-T2a）**：`scripts/bootstrap.ps1`（选解释器→建 `.venv`→`pip install -e .[dev]`→自检→门禁→依赖快照）、
  `scripts/check.ps1`（门禁一键）、`scripts/env_check.py`（解释器身份/版本/依赖/主环境污染四项校验，支持 `--json`）、
  `.github/workflows/ci.yml`（同样先建 `.venv` 再跑门禁）、`tests/conftest.py` 解释器身份门禁、
  `README.md`（开发指南）、`AGENTS.md`（代理强制约定）。

### 变更
- **Fluent 控件库改用 `PySide6-Fluent-Widgets`**（原计划写 `PyQt-Fluent-Widgets`）：后者基于 PyQt6，
  与已决策的 PySide6 并存会装两套 Qt 绑定并导致运行时崩溃；模块名仍为 `qfluentwidgets`。
- **`requires-python` 定为 `>=3.12,<3.14`**：3.14 暂无 PySide6 wheel；上限放开时机待 wheel 可用后评估。
- **依赖注入不引入 `dependency-injector`**：采用 `GUI框架 §3.3` 允许的「组合根手动装配」分支，避免新增未列入 `WP0-T2` 的依赖。
- **`.venv` 内执行成为机器可验证约束**：非 `.venv` 解释器运行时 pytest 直接 `UsageError`。

### 修复
- 无（本版为新基线）。

### 已知限制
- `core` 等子包目前只有占位 `__init__.py`，领域模型与接口按 `WP0-T3` 起实现；`cli` 只有 `version`，命令集在 `WP1-T5`。
- GUI 仅骨架窗口（一个占位标签），无导航、向导与主题切换（`WP9-T1`/`WP9-T3`/`WP9-T18`）。
- 打包（PyInstaller 单目录）与安装包（Inno Setup/NSIS）属 `WP11-T1`，本版未实施，仅在 `README.md` 给出前置命令与误报处置。
- CI 工作流已提交但未在 GitHub 上实跑（本机无远端执行环境），本地等效步骤已全绿。
- `docs/` 中两处与实现不一致待同步：`GUI框架 §3.3` 的 `PyQt-Fluent-Widgets` 包名、`约定 §2`/`GUI框架 §3.2` 的 `3.12+` 上限表述。
  按「先改文档再改代码」应尽快回写，已在交付说明中列为待确认项。

## 0.1.2 — 项目计划补全「任务 → 文档章节」追溯（v1.2 基线）

### 变更
- **`项目计划.md` v0.21**：WBS 的 **94 个任务**全部新增「文档依据」列，标注所依据的文档短名与**具体章节**（如 `编排 §2、§2.1、§3`、`配置 §3.3`、`日志 §7.3`），不再出现「见引擎规范」这类无章节的泛指。
- **新增 §1.4「文档依据」标注规则**：每个任务至少一条依据、短名以术语表 §1.2 为准、章节必须真实存在、主依据在前、文档改章节须同步本列。
- **新增 §4 查阅说明 + 短名对照**：说明「任务→文档」看表内列、「文档→任务」看反向索引。
- **新增 §4 反向索引（文档 → 关联任务）**：覆盖被任务引用的 **20 份**文档（另 3 份为本文件/台账/术语表等元文档），便于「文档改了要回看哪些任务」。
- **其他章节补依据**：§3 里程碑总览加「主依据文档」列；§6 追溯矩阵加「主规范」列；§9 测试策略、§10 风险、§12 交付物、§13 维护计划各加「文档依据」列；§11 每条待办补「影响任务 + 依据」。
- §1.1「每个任务包含」由 5 字段改为 6 字段（新增「文档依据」）；§2.3 文档交付物改为按六类统计（23 份）。
- §7 DoD 增两条：验收时须逐条对照「文档依据」所列条款；文档与实现冲突时**先改文档再改代码**（依据术语表 §1.3 的优先级）。

### 修复
- 统一短名为 `流程图`（原计划文中混用「流程 §15」，与术语表 §1.2 登记名不一致，会导致引用无法解析）。

### 台账与术语表
- `文档编写计划.md` v1.2：新增 S12「计划可追溯性增强」并回填进度。
- `术语表与文档映射.md` v0.2 更新：项目计划版本列改 v0.21；引用校验数字由 382 处升至 **835 处（文件名 56 / 短名 697 / 同文件 82），失效 0**；基线标记改 v1.2。

### 已知限制
- 「文档依据」列的**内容正确性**依赖人工判断（章节存在性已机器校验，但「这个任务是否真的该依据这一节」仍需评审）。
- 反向索引按任务号罗列，未做到「章节级反向」（如「引擎 §6.5 被哪些任务引用」），需要时按表内列检索。

## 0.1.1 — 文档一致性与分类收口（v1.1 基线）

> 触发：对 v1.0 基线做全量交叉审核（引用、矛盾、技术可行性），共确认 42 项问题（12 高 / 20 中 / 10 低），本版全部修复并重做文档分类。

### 变更（结构性）
- **文档重组为六类目录**：`docs/需求/`（2）、`docs/规范/`（11 + `rules/` 资产）、`docs/设计/`（5）、`docs/计划/`（2）、`docs/记录/`（1）、`docs/治理/`（2）。原 `docs/需求分析/`、`docs/开发约定/` 不再存在；20 处路径限定引用同步改写。
- **统一分类头**：每份文档头部新增 `> 分类：<类别> ｜ 目录：<路径>`，23 份全覆盖；分类标准（6 类 + 判定规则 + 权威级别）落在术语表 §1.1。
- **术语表改为全量登记**：短名表从 20 行扩到 23 行，新增 `GUI框架`、`约定` 两个短名（此前 8 处「GUI 设计 §5.6」无权威解释），并补版本列。

### 修复（高）
- **阶段模型统一为 8 阶段**：新增编排 §2.1 明确 `DetectInput`→`Prepare.probe`、`PrepareImage`→`Prepare.materialize|extract`、`Validate`→`Output.verify`，三者不再是阶段；同步修正流程图 §2.1（原 10 阶段）、架构 §10（原 10 阶段且入口 A 顺序错误）、GUI 与框架 §5.1（原 7 阶段）、日志 §2.3（原含 `Validate` 缺 `Prepare`）、需求分析 4 处措辞。
- **配置契约补全 FR3 字段**：`convert` 新增 `updates/cleanup/features/winre/boot/drivers/apps/volumeLabel/virtualEdition/esdDecrypt`，并给出 CV-01…CV-29 → 字段映射表（含「不开放为字段」的 5 项），消除「详设有选项、配置无字段」。
- **`slim.items` 定为单一形态**：规则类预设统一 `{ruleIds[], params{}}`（废弃 `appx.remove[]` 分类清单），配置类预设内联配置片段；配置 §4.1/§4.2 与规则 §9 同步。
- **起始阶段语义定清**：`startStage` 枚举补 `Prepare`，删除输入 §6 中不存在的 `Export`；明确「`Prepare` 永不因起始阶段被跳过」。
- **严格开关归属定清**：三个严格开关与卡死阈值以 `TaskConfig` 为准，设置页同名项降级为 `settings.defaults.*`（新建任务默认值）。
- **引用校验结论作废重算**：原「21 份文档 0 处失效」不成立（实为 23 份，且只覆盖文件名型 33/270）。改为三类口径（文件名 60 / 短名 201 / 同文件 77），**338 处 0 失效**，并把 6 类历史引用缺陷逐条记录在术语表 §4.2。
- **修掉 3 处硬失效引用**：转换路线验证 §6 的「《需求分析》§469」、项目计划 §10 的「§6.5」、编排 §7.1 的「引擎规范 §9（退出码白名单）」。
- **技术不可行项**：输入 §2.2 嗅探由 32 KB 改 64 KB（ISO 标识在偏移 32769，原值差 6 字节必失败）；引擎 §9.1 给出 `identitySha256` 的自指计算定义（置空字段后序列化）；进程 §7.1 补「GUI 无控制台时 `CTRL_BREAK_EVENT` 不可投递」的分支与实测要求；进程 §7.2 看门狗窗口数改为由「阈值 ÷ 采样间隔」计算（原写死 20 窗口与可配参数矛盾）。
- **样例与实测结论对齐**：转换路线验证 §1「无需重建」与 §5「必须组件聚合」的自相矛盾已修（仅对齐 §1，未改任何实测数据）。
- **SWM 输出定性为后置能力**：`output.type` 补 `swm` 取值但本期拒绝（`OUTPUT_TYPE_UNSUPPORTED`），GUI 恒灰显并有原因；输出 §6.1、引擎 R4、需求 FR3.3 同步标注。

### 修复（中/低）
- 状态机：编排 §4.1 补 `draft`、改小写；GUI 与框架 §5.2 删除 `Created/Planning/Ready/Paused`，与术语表取值一致；规则 §5.3 取消「部分成功」状态（改为 `succeeded` + 失败明细 + 退出码 1）。
- 续跑四条件：编排 §6 声明为唯一定义，输出 §7.3 改为引用并去掉 `completed=true` 写法（术语表禁用词）。
- 子步骤对齐：编排 §3 的 `Integrate` 补 `registry`/`verify`；`Unattend` 改为 `generate→validate→mount→inject→unmount`，无人值守 §2/§9 同步（注入不能提前到 Integrate，时序上拿不到 XML）。
- 字段契约：输入 §5 的 `input.editions[]` 改为 `input.image.editions[]`；集成 §2 补 `registry.strict`；TaskRecord 的 `runtime` 段补齐 `output_dir/staging_dir/log_files/audit_files/resumed`。
- 域白名单与自建源冲突：下载 §3.2 改为「内置域名 ∪ 当前数据源 host」，并说明只放行「你选的源」。
- ESD 索引假设：输入 §7.1、引擎 §6.1/§6.4 改为按 `Name`/`Boot Index` 判定，禁止写死 1/2。
- 11 步 → 10 步：GUI 与框架 §6.3 改为 10 步（⑩ 确认，执行页独立），与向导页规格、项目计划一致；台账同步。
- 图表与路径：架构 §7 的 work 目录改为程序目录内固定位置并补暂存区/输出目录；输出 §5.1 补 `\\?\` 长路径策略；项目计划 WP0-T3/T4 的 C# 风格路径改为 `src/uup2iso/...`，退出码补 5。
- 版本纪律：`版本与变更管理.md` §1 澄清 0.1.x = 文档基线发布、M1 用 0.2.0；§3 说明三处同步自 M0 脚手架起生效。
- 其他：字典化整理 S2/S5/S6/S7 台账编号当章节号的引用；日志 §8 去重复行；引擎 §11 补 `tool_installed` 事件；需求 FR7.4 内置模板由 3 套更正为 4 套；详设 CV-26 默认压缩标注澄清；术语表伴生文件名统一为 `<name>.iso.sha256`。

### 新增
- 术语表 §5 待办新增 3 项：#11 SWM 输出验证、#12 无控制台取消路径实测、#13 ESD 索引语义判定。
- 台账新增 S11「一致性与分类收口」，并把「文档清单/版本」的维护权收归术语表 §1.2（单一来源）。

### 基线构成（v1.1）
23 份文档：需求 2 + 规范 11 + 设计 5 + 计划 2 + 记录 1 + 治理 2；`docs/规范/rules/` 3 个 JSON 资产。

### 已知限制
- SWM 输出、无控制台取消、ESD 索引判定三项仍待实测（术语表 §5 #11–#13），文档层面只能给到「拒绝 + 原因」。
- 引用校验脚本目前是机审辅助，未纳入 CI；口径已写入术语表 §4.3，M0 需落成测试。
- 分类只重构了文档，未拆分 `需求分析.md`（仍含 FR0–FR10 主干）；若后续单份过大，再按 FR 域拆分。

## 0.1.0 — 文档基线 v1.0：一致性收口完成

### 新增
- **`docs/需求分析/术语表与文档映射.md`（v0.1）**：
  - **权威短名表**：20 份文档的短名（需求/详设/日志/进程/引擎/输出/下载/输入/配置/编排/规则/集成/无人值守/GUI-向导/GUI-设置/架构/流程图/计划/验证/台账）与优先级规则（领域规范 > 需求主干 > 架构流程）。
  - **术语表**（6 类）：编排与状态、状态取值（唯一写法，禁用 `success`/`complete` 等同义词）、产物与目录、映像与集成、规则与工具链、GUI。
  - **FR → 规范映射**：FR0–FR10 逐条给出主规范与补充规范，结论为「无仅需求无规范的条目」。
  - **交叉引用校验**：扫描全部 Markdown 的 `文件名.md §N[.M]` 引用并比对实际标题，**21 份文档 0 处失效**（含三级标题）；短名引用按权威短名表人工核对；附维护脚本要点。
  - **遗留待办 10 项**（M0 Spike 优先）：`SkipISO` 行为、wimlib LZMS/`--ref` 版本阈值、`blockedCombos` 数据、转换器与 ADK 许可、XSD 许可、空间系数、数据源直出端点、镜像站清单可信度、ESD 直接挂载、自动登录 `LogonCount` 行为。
  - **基线声明**：v1.0 之后变更须同时更新文档版本、CHANGELOG、台账进度、术语表（新增术语时）。
- `软件架构图.md` v0.6 / `软件流程图.md` v0.6：头部增加「规范清单、术语与需求映射」指针。
- `文档编写计划.md` v1.0：S10 标记完成，新增本收口文档到基线表。

### 基线构成（v1.0）
15 份规范/规格 + `需求分析`/`详设` + `架构`/`流程`/`计划`/`台账`/`验证`，覆盖 FR0–FR10 全部条目，每份均含「拒绝与失败语义 + 日志事件 + 可测验收标准」。

### 已知限制
- **S9《打包与发布规范》未单独成文**：签名证书、安装器形态、体积目标须待证书与许可结论，相关内容已折叠进 `项目计划.md` WP11。
- 交叉引用自动检查只覆盖「文件名.md §N」形式，短名引用（如「引擎 §7」）依赖人工核对。
- 10 项遗留待办全部需要实测/法务/产品决策，文档层面无法收口。

## 0.0.20 — 完成 S8《GUI 交互规格》（拆两份）

### 新增
- **`docs/需求分析/GUI交互规格-向导页.md`（v0.1）**：
  - **全局规则**：三档模式（简单/标准/专家，持久化到设置、降档不丢值）、步骤条与可回跳（仅已完成可跳）、**内联错误 + 底部汇总**（跨字段冲突可点击定位）、禁用态必须带原因、键盘与无障碍（Tab 顺序、`F8` 跳问题、焦点可见）、草稿自动保存与离开保护。
  - **10 个步骤页字段表**（输入/构建/下载/转换/精简/优化/集成/无人值守/输出/确认），每字段标注控件、默认值、校验与档位（S/N/X）；按入口动态显示步骤。
  - 页面四态（空/加载/错误/禁用）与不跳动布局；向导不展示实时日志（跳执行页）。
- **`docs/需求分析/GUI交互规格-设置与对话框.md`（v0.1）**：
  - 设置 6 页签字段表（数据源/下载/日志/工具与依赖/高级/关于），含自动更新开关文案「按随包清单对齐版本，不追上游新版」、日志目录与缓存 TTL 只读、并发固定只读。
  - 预设管理（删除不影响已保存任务的提示、未知 `ruleId` 拒绝导入）、日志页（AUDIT 只读、`k=v` 精确搜索、10 万行截断提示）、任务列表（7 种状态、清理中间产物、删除记录不删 ISO）、执行页（阶段串、取消保留中间产物、运行中不可改配置）。
  - 6 类对话框（通用确认/危险确认/覆盖冲突/错误详情/诊断包/退出），危险确认要求勾选「我已了解」才启用主按钮。
- `需求分析.md` v0.18：FR10.1 补充向导形态、三档模式、错误呈现与字段规格指针。
- `项目计划.md` v0.19：WS9 增加字段级规格指针；WP9-T14 设置页改为 6 页签并明确「运行中任务不受新设置影响」。
- `文档编写计划.md` v0.9：S8 标记完成（拆两份），新增两份文档到基线表。

### 已知限制
- 自定义分区编辑器是否在 M2 提供未定（当前仅整盘 GPT / 保留现有可选）。
- 未定义系统托盘保活，运行中关闭窗口仅能最小化窗口。
- 程序自身更新不在本期范围（仅工具版本对齐）。

## 0.0.19 — 完成 S7《无人值守规范》

### 新增
- **`docs/需求分析/无人值守规范.md`（v0.1）**：四项决策已确认并落条款：
  - **模板（D1）**：内置 4 套模板（`desktop-standard`/`minimal`/`domain-join`/`custom-partition`，由代码按构建号生成骨架而非静态 XML）+ 允许导入自定义模板（导入必须过 XSD；含 `Format`/删分区等磁盘操作需二次确认 + 审计）。
  - **脚本（D2）**：仅允许 `FirstLogonCommands`/`RunSynchronous`；单文件 ≤64 KB、总数 ≤3、禁止 `pause`/`Read-Host`/`set /p`/无参 `timeout`/`choice` 及重启循环；记录 SHA256 与文件名，**不记录脚本内容**。
  - **密钥（D3）**：默认**不写入产品密钥**；用户提供时以 DPAPI 加密存 `secrets.dat`，日志/报告/诊断包仅显示末 5 位；导出配置时剥离为 `"***"`；密码默认不写（仅自动登录时写并提示风险）。
  - **Schema（D4）**：从映像读构建号 → 映射产品线与组件版本（19041/22000/22621/22631/26100）；**构建号无法判定则拒绝生成**（`UNATTEND_VERSION_UNKNOWN`）；可判定但不在 XSD 覆盖范围时用最接近 XSD 并告警。
- 另含：两类文件放置与生效时机（默认只出 `autounattend.xml` 到 ISO 根，可选注入 wim）、可视化配置项 → XML 路径映射表、四条互斥/依赖校验、DOM 构建（禁字符串拼接）+ UTF-8 BOM + 回读校验、9 类日志事件、12 条验收标准。
- `需求分析.md` v0.17：FR7.4 改为「版本未知拒绝生成 / 近似告警」，并新增实现约束（脚本限额、密钥脱敏、DOM 构建）。
- `项目计划.md` v0.18：WP7-T1..T4 全部重写为可验收任务。
- `文档编写计划.md` v0.8：S7 标记完成并回填进度。

### 已知限制
- 仅生成 `autounattend.xml`，未提供「已有系统重新封装」所需的 `unattend.xml`。
- 内置 XSD 的来源与再分发许可待确认（可能需随包附带微软 XSD 文件）。
- 自动登录密码在首次登录后是否自动清除（`LogonCount=1` 行为）需实测确认。

## 0.0.18 — 完成 S6《集成规范》

### 新增
- **`docs/需求分析/集成规范.md`（v0.1）**：四项决策已确认并落条款：
  - **更新来源（D1）**：**仅接受用户提供的本地 `.msu/.cab`**，不联网检索、不自动下载；按 `auto` 分类排序（SSU → LCU → 其他，同级按 KB 号/包名），支持 `order=user`；错误码映射 6 类（`0x800f081e` 不适用、`0x800f081f` 缺依赖、`0x800f0906` 需联网、`0x80070490` 已存在视为成功、`0x800f0922` 空间不足）。
  - **驱动（D2）**：递归扫描 `*.inf`、解析 Provider/DriverVer/HardwareID；**默认要求有效签名**，未签名/已吊销**跳过并列入报告**（不阻断阶段）；同硬件多驱动按 **DriverVer 新者优先**（可切 `lastWins`），记 `driver_conflict`；`bootCritical` 单独标注。
  - **语言（D3）**：**挂载前**校验组合——LIP 必须有基础 LP、FOD 需对应 LP、**不可移除当前 UI 语言**且至少保留 1 个 LP；非法组合直接阻断（退出码 4），避免白等 10 分钟挂载。
  - **回滚（D4）**：**阶段级整体回滚**——任一集成失败即 `/Unmount /Discard`，不做部分提交；未签名驱动属「跳过」而非失败。
- 另含：`integrate` 配置结构、注册表 `.reg` 编码识别与键路径→离线 hive 映射（无法映射即拒绝该文件）、覆盖顺序（规则先→集成后）与 `reg_override` 留痕、执行顺序（updates→drivers→language→registry→verify）、`integrate-report.json`（含驱动跳过原因与冲突明细）、12 类日志事件、14 条验收标准。
- `需求分析.md` v0.16：FR6.4 后新增执行机制说明（仅本地更新、签名与冲突、语言前置校验、阶段级回滚）。
- `项目计划.md` v0.17：WP6-T1..T4 全部重写为可验收任务。
- `文档编写计划.md` v0.7：S6 标记完成并回填进度。

### 已知限制
- 更新包适用性只能挂载后由 DISM 判定（未做挂载前预检），不适用包会浪费一次挂载。
- 语言包移除未同步清理 `intlcfg` 与 WinRE 语言资源，仅处理 LP/LIP 层面。
- 注册表导入仅支持 `.reg`，不支持 `.inf` 的 `AddReg` 段。

## 0.0.17 — 完成 S5《精简与优化规则规范》

### 新增
- **`docs/需求分析/精简与优化规则规范.md`（v0.1）**：四项决策已确认并落条款：
  - **依赖矩阵（D1）**：**手写硬约束**（包/能力/功能必需关系，可评审）+ **实测观测数据**（从映像 inventory 生成，仅补全与提示）；冲突时以手写为准并告警；矩阵文件 `rules/dependency-matrix.json`，含 `blockedCombos`（`block`/`warning` 两级）。
  - **上下文（D2）**：`offline` 为主路径、`firstBoot` 由无人值守注入、`online` 一律跳过并单列为「不支持」；不满足时**跳过且必须可见**（结果汇总 + CLI `skippedRules[]` + 事件），另提供 `strictContext` 改为失败。
  - **可逆性（D3）**：仅 `level=high` 与 `reversible=false` 的规则必须记录 `recovery`；阶段内失败靠 `Unmount /Discard` 整体回滚，`ResetBase` 类不可逆项执行前二次确认并明示不可撤销。
  - **预设绑定（D4）**：预设存 **`ruleId` 列表 + 可选参数覆盖**（按项绑定），未知 `ruleId` 拒绝导入；规则库升级后旧预设按 `minEngine` 与 `packVersion` 判定可用性。
- 另含：规则 Schema **向后兼容增补**（`context`/`verify`/`reversible`/`recovery`/`params`/`minEngine`/`estimatedBytes`，全部可选，`schemaVersion` 保持 1.0）、8 类 `verify` 验证类型与两种验证时机、执行顺序（`order` → 拓扑 → id）与幂等准入要求、单条失败分级处理（high 中断丢弃 / medium 继续并退 1 / low 告警）、`rules-report.json` 报告结构、8 类日志事件、13 条验收标准。
- **`rules/rules.schema.json`**：新增可选字段与 `verify` 定义（已用 JSON 解析校验，现有 `fr4-rules.json`（11 条）与 `fr5-rules.json`（19 条）仍向后兼容）。
- `需求分析.md` v0.15：FR4.4 与 FR5.6 后新增执行机制指针（上下文/矩阵/verify/可逆性）。
- `FR3-FR5详细需求.md` v0.3：§3（FR4）与 §4（FR5）顶部声明「执行机制以本规范为准」，避免两份文档各说一套。
- `项目计划.md` v0.16：WP4-T3 补双轨矩阵与逐项阻断验收；WP5-T2 补 Schema 扩展与加载器语义校验。
- `文档编写计划.md` v0.6：S5 标记完成并回填进度。

### 已知限制
- `blockedCombos` 初始数据尚未建立（需 Dism++ 经验 + 实测），首批覆盖范围未定。
- `verify` 批量实现方式未定（一次 `Get-Packages` 复用 vs 逐条查询），影响抽样验证耗时。
- 规则库未签名，当前依赖程序包完整性；「试运行（只报告不修改）」列为开放问题。

## 0.0.16 — 完成 S4《任务编排与续跑规范》

### 新增
- **`docs/需求分析/任务编排与续跑规范.md`（v0.1）**：四项决策已确认并落条款：
  - **检查点粒度（D1）**：对外保持 **8 阶段**（Prepare/Fetch/Convert/Slim/Optimize/Integrate/Unattend/Output），阶段内按**子步骤**记检查点；子步骤完成即写、不等阶段结束；标注非幂等子步骤（挂载/卸载）重跑前必须清理残留。
  - **重试（D2）**：分级白名单——网络超时/断流、文件占用、工具已知可恢复码、校验不符可重试（子步骤级 2–3 次）；配置/输入/空间/跨卷/转换器非占用失败**不重试**；子步骤耗尽后**阶段级兜底 2 次**（30 s 退避）；重试前清理该阶段未完成产物。
  - **取消（D3）**：终止进程树、阶段标 `cancelled`、**中间产物一律保留**、退出码 3；二次取消立即强杀；清理为显式动作（GUI「清理本次中间产物」，CLI 手工删目录），只删 `work\<task_id>\`。
  - **任务记录（D4）**：保留最近 **50** 个，超出按 `finishedAt` 清理，同时删关联 `work` 目录与输出暂存残留，**不删**用户输出目录中的最终产物；运行中的任务永不清理。
- 另含：阶段表（输入/产物/前置/跳过条件）、任务态/阶段态/子步骤态三层状态机、`checkpoint.json` 结构与**原子写**（tmp+replace）与 60 s 心跳、复用四条件（配置指纹+输入指纹+完成标记+产物哈希）与「配置变更拒绝续跑」、续跑流程图、11 类日志事件、12 条验收标准。
- `需求分析.md` v0.14：FR0.5 后新增「阶段编排与续跑」说明并指向本规范。
- `项目计划.md` v0.15：WP1-T1/T2/T3 重写为可验收任务（子步骤定义、白名单重试、检查点原子写与续跑校验）。
- `文档编写计划.md` v0.5：S4 标记完成并回填进度。

### 已知限制
- 不支持「暂停/继续」（只能取消后重跑），列为开放问题。
- 阶段级自动重试固定 2 次、任务记录固定 50 个，均未开放配置。
- 检查点心跳 60 秒对秒级阶段偏密，实际取值待实测调优。

## 0.0.15 — 完成 S3《预设与配置契约规范》

### 新增
- **`docs/需求分析/预设与配置契约规范.md`（v0.1）**：四项决策已确认并落条款：
  - **配置粒度（D1）**：预设以**快照内联**写入 `TaskConfig`（保留 `presetId`/`presetVersion`/`snapshottedAt`），**运行时只读任务配置、不再打开预设文件**——避免改预设导致历史任务结果漂移；引用规则库中不存在的 `ruleId` 时**拒绝导入**并列出缺失项，不静默丢项。
  - **迁移（D2）**：次版本直接兼容；主版本必须经迁移器（幂等、有单测），迁移前备份 `<taskId>.bak`，失败不写回并报 `CONFIG_MIGRATION_FAILED`；迁移不改 `meta.id`/`createdAt`。
  - **未知字段（D3）**：默认忽略 + 收集 `unknownFields[]` + WARN + 界面提示 + 诊断包记录；`validation.strictUnknownFields=true` 时按错误处理；`schemaVersion` 主版本不匹配直接拒绝。
  - **路径（D4）**：默认相对**配置文件所在目录**；绝对路径允许但加载时提示 `path_not_portable`（不阻断）；**不展开环境变量**；配置指纹排除路径字段，保证跨机可比。
- 另含：文件布局（`tasks\*.json`、四类 `presets`、`schemas\taskconfig.schema.json`）、`TaskConfig` 顶层与 `input`/`convert`/`output`/`execution` 结构、取值约束与默认值表（超范围**不静默夹紧**）、三层校验（V0 语法 / V1 Schema / V2 语义）、配置指纹定义与用途、导入导出与跨机复现判据、GUI/CLI 关系、8 类日志事件、10 条验收标准。
- `需求分析.md` v0.13：FR9.1 补充快照内联、三层校验、迁移与指纹，并指向本规范。
- `项目计划.md` v0.14：WP0-T6 重写为可验收任务（权威 Schema、三层校验、迁移备份、指纹、快照内联）。
- `文档编写计划.md` v0.4：S3 标记完成并回填进度。

### 已知限制
- 完整 JSON Schema 以随包文件 `schemas/taskconfig.schema.json` 为准，本文给出结构与关键约束；两者由 CI 校验保持一致。
- 仅支持单预设快照，组合预设（多预设叠加与优先级）列为开放问题。
- 配置未加密/签名，含产品密钥等敏感项时的保护方式待定。

## 0.0.14 — 完成 S2《输入识别与映像准备规范》

### 新增
- **`docs/需求分析/输入识别与映像准备规范.md`（v0.1）**：四项决策已确认并落条款：
  - **判定（D1）**：以**文件头嗅探**为主（WIM 偏移 0 的 `MSWIM\0\0\0`、ISO 偏移 32769 的 `CD001`）、扩展名为辅；扩展名误标时按真实类型处理并记 `extension_mismatch`；同一输入出现多个候选时**不猜**，报 `INPUT_AMBIGUOUS` 并列出候选（GUI 不默认选中，CLI 退出码 4）。
  - **加密 ESD（D2）**：明确拒绝、**不尝试解密**，文案指向「提供未加密的 ESD/WIM/ISO」；判定不确定时按加密处理（保守）。
  - **多卷 SWM（D3）**：拒绝并附可执行的 DISM 合并命令；**不自动合并**（额外磁盘、中途失败留垃圾、结果不受本工具校验）。
  - **输出类型（D4）**：入口 C 允许只输出 WIM/ESD 而不封 ISO；ESD 输出在保守模式下直接拒绝并建议改用 WIM。
- 另含：分片/非独立 ESD 判定（`Part Number: n of m`）与拒绝、非安装介质/非 Windows 映像判定、`probe.json` 结构与全量 edition 枚举、起始阶段推断表（含 `STAGE_TOO_EARLY`）、`PrepareImage` 产物布局与**只读原则**（源永不修改，写意图触发 `INPUT_IMMUTABLE_VIOLATION`）、准备后四项自检、12 类错误语义、8 类日志事件、12 条验收标准。
- `需求分析.md` v0.12：新增「输入判定与拒绝（已决策）」说明段（嗅探优先、冲突不猜、三类明确拒绝、只出 WIM、ESD 先物化）。
- `项目计划.md` v0.13：WP1-T4 重写为可验收任务（嗅探/冲突列候选/拒绝文案/edition 全量/只读解包与自检）。
- `文档编写计划.md` v0.3：S2 标记完成并回填进度记录。

### 已知限制
- ESD 是否可直接挂载修改（跳过导出为 WIM）未实测；当前按「先物化为 LZX WIM」实现。
- 多 edition 输出形态（多索引 WIM vs 每 edition 一份）待与转换器、精简实现对齐后确定。
- ISO 解包用 `7z` 还是「只读挂载后复制」在 UDF 长文件名/符号链接上存在差异，需样本验证。

## 0.0.13 — 完成 S1《数据源与下载规范》

### 新增
- **`docs/需求分析/数据源与下载规范.md`（v0.1）**：四项决策已确认并落条款：
  - **字段兼容（D1）**：区分必需/可选——必需字段（构建号/语言/edition/文件名/大小/SHA1/URL）缺失即 `SOURCE_SCHEMA_INVALID`，不使用默认值兜底；可选缺失记 WARN；未知字段忽略并记录到 `unknownFields[]`。
  - **缓存（D2）**：构建列表与详情缓存 **10 分钟**；刷新失败允许使用过期缓存、记 `manifest_stale_used` 并在 UI 标注「可能不是最新」；缓存只存元数据。
  - **重试（D3）**：网络层由 `aria2c --max-tries=3 --retry-wait=2` 负责；整任务不自动重跑；404 不反复重试；所有重试记 `download_retry`。
  - **校验失败（D4）**：先比大小再算 SHA1（1 MB 流式分块）；不符则删文件并重下，**最多 2 次**；仍失败报 `DOWNLOAD_CORRUPT` 并列出期望/实际哈希；**绝不忽略哈希**。
- 另含：数据源抽象与地址模板化、备用地址顺序切换、请求礼貌约束（并发 ≤1、间隔 ≥300 ms）、清单结构与五项自校验、aria2c 完整参数表、进度按字节加权、`download.state.json` 状态机、目录结构、失败矩阵、9 类日志事件、11 条验收标准、夹具录制与重放方案。
- `需求分析.md` v0.11：FR1 增加字段兼容与缓存决策说明；FR2.4 改为「网络层 aria2c 重试 + 校验层重下 ≤2 次」；FR2 增加实现契约指针。
- `项目计划.md` v0.12：WP2-T3/T4/T5/T6/T7 细化为可验收任务（清单自校验、字段分级、缓存容错、参数契约、校验自愈）。
- `文档编写计划.md` v0.2：S1 标记完成，进度记录回填。

### 已知限制
- 站点端点路径与响应结构未在文档固化（易变），需 M0 用真实响应录成夹具后回填细节。
- 备用镜像站的清单可信度仅靠 SHA1 保证，若镜像自身篡改清单则无法发现（已列为开放问题）。

## 0.0.12 — 建立《文档编写计划》持久台账

### 新增
- **`docs/需求分析/文档编写计划.md`（v0.1）**：多轮工作的持久台账，不依赖会话记忆。含：
  - 已完成基线（12 份文档 + 版本号）；
  - **S1–S10 待写清单与推进顺序**：S1 数据源与下载 → S2 输入识别与映像准备 → S3 预设与配置契约 → S4 任务编排与续跑 → S5 精简与优化规则 → S6 集成 → S7 无人值守 → S8 GUI 交互规格 → S9 打包与发布（可选）→ S10 一致性收口；
  - 每份文档的**范围与必答决策**（共 30+ 个决策点，均按「问题/影响/推荐」方式向用户确认后再动笔）；
  - **每轮固定动作**：先提决策 → 写文档 → 联动需求分析/项目计划/GUI/架构流程 → 递增 CHANGELOG 与版本 → 写记忆；
  - **完成判据**：可执行验收标准、拒绝边界与文案、日志事件、与既有规范无冲突（冲突须显式声明优先级）；
  - 已知阻塞项（SkipISO 实测、wimlib 版本阈值、转换器/ADK 许可、规则库依赖矩阵数据）及其影响文档。
- 同步建立 10 项任务（S1–S10），按依赖顺序推进。

### 已知限制
- 台账中的顺序以 M1（最小 CLI 跑通入口 A）为优先，若里程碑调整需重排 S1–S4。
- S8 体量较大，可能拆分为「向导页规格」与「设置与对话框规格」两份，拆分时台账需同步更新。

## 0.0.11 — 新增《输出校验与原子性规范》

### 新增
- **`docs/需求分析/输出校验与原子性规范.md`（v0.1）**：把「不产出半成品」落成机制，四项决策已确认：
  - **暂存在输出卷**：产物写入 `{输出目录}\.uup2iso-tmp\<task_id>\`（必然同卷），校验通过后 `os.replace` **原子落位**；禁止 `shutil.move`/先写最终名；提交前做同卷校验（跨卷即拒绝 `OUTPUT_CROSS_VOLUME`），提交后立即复核大小与 SHA256。
  - **校验分级**：L1 结构（可挂载/引导文件/`setup.exe`/`sources\install.*`/卷标）、L2 内容（索引/edition/压缩类型/SHA256/包名集合）**默认开启**；L3 深度（`wimlib verify`、可选 `ScanHealth`，10–30 分钟）**默认关闭、可勾选**，未勾选时不得调用 verify。
  - **中断保留并提示**：`.part` + `staging.json` 状态机（`writing`→`validated`→`committing`）；启动只检查**最近 10 个**任务记录（不全盘扫描）；GUI 三选（继续/保留/删除），CLI 打印手工处理命令；`committing` 残留按「最终文件 SHA256 是否匹配」判定已提交或半提交。
  - **重名默认加后缀**：`name.iso` → `name_1.iso`…（999 后加时间戳）；`ask` 策略在 CLI 下退化为 `suffix`；`overwrite` 需显式配置 + 二次确认 + 审计事件。
  - 补充：命名模板与长度上限（200 字符）、空间两段预检（Fetch 前/Output 前，系数 1.05）、伴生文件（`.sha256` 兼容 sha256sum、`readme.txt`、可选 `manifest.json`，同提交语义）、续跑复用四条件（`.part` 永不参与复用）、8 类输出事件、12 条验收标准。
- `需求分析.md` v0.10：FR8.3 改为分级校验，新增实现机制说明（同卷暂存 + 原子落位 + 重名后缀），验收增加「最终目录不出现任何产物」「空间不足提前失败」。
- `项目计划.md` v0.11：WP8-T2/T3 细化，新增 **WP8-T4 原子提交与暂存区**；风险表增加「输出盘空间不足」「崩溃产生半成品被误用」两条；交付物文档列补本规范。

### 已知限制
- 空间系数 1.05 未实测校准（`oscdimg` 可能存在比最终产物更大的临时峰值）。
- 残留检测仅覆盖最近 10 个任务记录，更早的暂存残留需用户手工清理（开放问题：暂存区总量上限）。
- 伴生文件与产物无法做到跨文件原子：产物已提交而伴生失败时只告警不回滚。

## 0.0.10 — 明确工具哈希信任根（随包清单）并修正「自动更新」语义

### 变更
- **新增决策 D4（哈希信任根 = 随包锁定清单）**：不联网获取清单、不读取发布页/Release 页自称的哈希、不接受上游 `SHA256SUMS`。理由：期望值必须来自与文件**不同的信任通道**，同源哈希在被篡改时会一起改，比对必然通过却无保护。
- **修正原 §9.1 的逻辑矛盾**：旧表述「清单无该版本 → 拒绝获取」会导致自动更新无法升到任何新版本。现明确：**新版本 = 新哈希**，随程序发版一并写入清单；运行时只比对「清单为某版本登记的哈希」，不存在「拿旧哈希验新文件」。
- **「自动更新」语义澄清为「按清单对齐版本」**（§9.3）：该功能解决的是工具被手工替换、被杀软删除、文件损坏、从旧版程序目录升级导致的版本不匹配；**不追上游新版**。真正升级工具版本 = 程序发新版本。
- 新增：本地版本**高于**清单登记版本时**不回退**，记 `tool_version_ahead` 并在 UI 标注「非受支持版本」。
- 新增：清单自身完整性校验（`identitySha256` 编译期常量），被手工修改即判定失效、回退内置只读副本、记 `manifest_tampered`。
- 事件重命名：`tool_updated` / `tool_update_failed` → `tool_aligned` / `tool_align_failed`，新增 `tool_version_ahead` / `tool_align_offline` / `manifest_tampered`。
- `需求分析.md` v0.9：FR10.4 设置项改为「工具自动更新（按清单对齐版本）」并写明不追新版。
- `GUI与框架设计.md` v0.8：设置页该行改为「按随包清单对齐版本，不追新版」，按钮改为「立即对齐」。
- `项目计划.md` v0.10：WP0-T8a 重写为「工具版本对齐」，验收改为「默认关闭不替换 / 清单篡改可检出」。
- `映像引擎与转换规范.md` v0.2：§2 增 D4；§9 重写；§12 验收扩为 14 条；§13 增清单维护与白名单两项。

### 已知限制
- 工具新版本必须等程序发版才能进入清单（D4 的固有代价）：上游发布后到我们适配之间存在时间差，期间不会自动升级。
- 无发布签名清单体系，因此不支持「清单独立于主程序发布」；若后续需要，需引入我方私钥签名清单（已在 D4 推翻条件中记录）。

## 0.0.9 — 新增《映像引擎与转换规范》，确定路线 A 与工具获取/更新策略

### 新增
- **`docs/需求分析/映像引擎与转换规范.md`（v0.1）**：记录三项已决策并给出可编码条款：
  - **D1 按操作路由**：静态对照表（R1–R11）决定主引擎；备用引擎仅在主引擎缺失或不支持该参数时启用，且必须记 `capability_degraded`；同一配置在所有机器走同一条路，问题可复现。
  - **D2 保守模式**：`version=unknown` 时禁用「错了会报废产物」的能力——`--ref` 分片引用、`LZMS`/ESD 输出、引导文件自动刷新、未登记的新构建 DISM 参数、虚拟版本参数；保留全部基础面（apply/export/info/capture/verify、DISM 挂载与包操作、oscdimg 标准参数）；被禁项在 GUI **灰显并说明原因**，不静默移除。
  - **D3 路线 A**：`install.wim` 由已验证转换器聚合产出，不自研聚合算法；职责切分为「转换器只做聚合，媒体骨架与 `sources\boot.wim` 由本工具按实测路径生成（apply 索引 1 / export 索引 2），精简优化 ISO 由本工具接管」。
  - **转换器契约**：版本锁定随包分发、不参与自动更新；`ConvertConfig.ini` 唯一参数来源；输入完整性前置（SHA1 全通过）；`wim_only`（MVP 默认，`SkipISO=1`）/`full` 两模式，无媒体骨架时自动回退 `full` 并记 WARN。
  - **产物盘点**：install.wim 可读、edition 列表、压缩类型、boot.wim、四个引导文件、磁盘余量；**等价性校验**：index/名称/压缩类型/`Get-Packages` 包名集合一致（±0.1% 文件数容差）。
  - **挂载契约**：挂载根在工作目录、只读检查用 `/ReadOnly`、`try/finally` 成对卸载、失败 `/Discard`、残留挂载检测与 `Cleanup-Wim` 提示。
  - **拒绝规则**：加密 ESD、多卷 SWM、非独立分片 ESD、非 Windows 映像一律明确拒绝，不尝试、不产出半成品。
- **工具获取与自动更新**：`tools\tools.lock.json` 作为唯一可信来源（HTTPS + 域名白名单 + 内置锁定哈希）；必需工具（aria2c/7z/wimlib）缺失时**从官方发布通道自动获取**；`oscdimg` 只提示装 ADK（许可不允许再分发）；**工具自动更新默认关闭**，开启后仅在启动、任务开始前按清单比对版本，替换保留 `.bak`、失败回滚并继续用旧版；**转换器版本锁定、不参与自动更新**。
- `需求分析.md` v0.8：FR3 增加「转换路线（已决策）」说明；FR10.3 改为必需工具官方通道自动获取；FR10.4 新增「工具自动更新」设置项（默认关闭）；§13 待办中「PSF 差分实现路线」标记为已定。
- `GUI与框架设计.md` v0.7：设置页「工具与依赖」增加转换器版本行、自动更新开关与「检查更新」；§6.1 导航补「缺失自动获取 / 自动更新」。
- `项目计划.md` v0.9：WP0-T7 扩展为工具提供与能力探测；WP0-T8 改为通用「缺失自动获取」；新增 **WP0-T8a 工具自动更新（默认关闭）**；WP3-T2 改为「CAB 解包与按需 PSF」；新增 **WP3-T12 转换器适配与等价性**；M1 验收增加等价性校验；风险表增加转换器风险；交付物文档列补本规范。

### 已知限制
- `SkipISO=1` 是否保留媒体骨架与固定 `install.wim` 路径**未实测**（M0 Spike 必答），当前按「可能回退 `full`」实现。
- `wimlib` 支持 LZMS 写出/`--ref` 的最低版本阈值未实测，暂以「不确定即禁用」处理。
- 转换器与 ADK `oscdimg` 的再分发许可尚未取得书面结论。

## 0.0.8 — 看门狗改为四信号联合；并发固定不开放

### 变更
- **看门狗判定重写**（`外部工具与进程执行规范.md` §7.2）：不再只看「无输出 + CPU 无增长」。改为**四信号联合**——①无 stdout/stderr；②进程树 CPU 增量 < 0.5 s/窗口；③进程树读写增量 < 256 KB/s；④工具 `io_profile` 含 `network` 时系统网络 < 32 KB/s。**任一有活动即重置计时**；每 30 秒采样，连续 20 个窗口（10 分钟）全无活动才告警。
- **采样实现**：`GetProcessTimes` / `GetProcessIoCounters` / `GetIfTable2` 全部经 ctypes 调用，**不引入 psutil**；进程树终止仍用 `taskkill /T /F`。
- `外部工具与进程执行规范.md` §2.2 新增 **`io_profile` 表**：`aria2c`=network+disk、`wimlib-imagex`/`7z`=disk、`DISM`/`oscdimg`=disk+cpu。
- **并发固定**（§8）：镜像类 **1**、下载 **1**，**不开放用户配置**；`TaskConfig` 中注入并发字段时忽略并记 `WARN concurrency_ignored`。
- §12 验收由单条「静默即告警」改为**正例 + 三个反例**：大文件写入无输出（磁盘 >1 MB/s）不告警、下载无输出（网络 >1 MB/s）不告警、系统其他程序有流量不告警；并新增并发固定用例。
- §13 开放问题移除「是否引入 psutil」与「并发是否开放配置」，改为「三类阈值需真实慢盘/慢网样本校准」与「DISM 是否存在长时间低 CPU 低 IO 阶段需实测」。
- `项目计划.md` v0.8：WP0-T5 改为四信号看门狗（ctypes 采样）+ 并发固定镜像 1/下载 1。

### 已知限制
- 三档阈值（CPU 0.5 s、磁盘 256 KB/s、网络 32 KB/s）为保守初值，未用真实慢盘/慢网/杀软扫描场景校准。
- 网络信号取系统级计数：其他程序有流量时会掩盖真实静默（保守漏报，不会误杀正在工作的工具）。

## 0.0.7 — 新增《外部工具与进程执行规范》

### 新增
- **`docs/需求分析/外部工具与进程执行规范.md`（v0.1）**：把原本散落在 FR3-FR5 §5.2 与日志规范里的进程执行职责收口为可编码契约：
  - **职责边界**：`ProcessRunner` 只负责执行与观测；业务判断、重试、清理归阶段。禁止任何模块直接 `subprocess`。
  - **工具定位**：`程序目录\tools\` → 设置路径 → PATH → 报错；白名单 + 可选 SHA256；`Dism` 非管理员在任务开始前即拒绝（退出码 4）。
  - **版本/能力探测**：`aria2c --version`、`wimlib-imagex --version`、`7z i`、`DISM /Version`；`oscdimg` 无版本参数改读 PE 版本资源；解析失败标 `version=unknown` 并进入保守能力模式，不阻断启动。
  - **进程启动契约**：`args: list[str]`、`shell=False`、`stdin=DEVNULL`、`CREATE_NO_WINDOW`、`CREATE_NEW_PROCESS_GROUP`、敏感值不注入 env。
  - **输出读取**：stdout/stderr **双路并发**读取 + `channel` 标记（避免管道死锁且保留通道信息）；逐工具编码（DISM/7z/aria2c→cp936、wimlib→utf-8）+ `utf-8→cp936→latin-1` 回退链，记 `encoding`；超长行 64 KB 截断标 `truncated`。
  - **行分类**：`result` → `progress` → `line` 的判定顺序；正则表存 `rules/process_patterns.yaml`；未登记版本时启用**严格兜底**（含 `%` 且不含 ERROR/Failed 才判进度），宁可多记不可误丢错误。
  - **进度模型**：单调不减、UI 节流 ≥200 ms、阶段收尾必须上报 100%。
  - **取消**：Ctrl-Break 整组 → 5 s → terminate → 2 s → 强杀进程树；取消返回码 3、不记 ERROR、不阻断 `--resume`。
  - **静默看门狗**：连续 10 分钟无输出且 CPU 无增长 → `suspected_hang`（WARN），**不自动终止**，GUI 非模态提示、CLI 告警后继续。
  - **并发**：镜像类工具全局互斥（1 个），`aria2c` 可与镜像操作并行；锁等待 >60 s 记 WARN。
  - **错误映射**：六类内部错误（TOOL_MISSING/START_FAILED/PERMISSION_DENIED/TOOL_FAILED/CANCELED/DECODE_ERROR），失败事件必须含原码、hresult、最后 20 行、脱敏命令。
  - **可测试性**：`FakeProcessBackend` + `tests/fixtures/process/` 夹具回放，CI 不依赖真实镜像与外网。
- `日志规范.md` v0.4：常用键增加 `channel` / `encoding` / `sampled`；分类规则改为指向本规范 §5。
- `需求分析.md` v0.7：§10 增加外部进程统一契约指针（禁止绕过 ProcessRunner）。
- `项目计划.md` v0.7：WP0-T5 重写为可验收的进程执行层任务（双路读取、编码回退、分类、看门狗、错误映射、夹具测试）；交付物文档列增加本规范。

### 已知限制
- `oscdimg` 无 `--version`，版本来自 PE 版本资源，个别精简系统可能读不到（降级为 `unknown` 并进入保守能力模式）。
- 正则表首版仅覆盖 5 个工具的主流程输出，DISM 各语言版本与 wimlib 版本差异需要实测夹具补齐。
- 看门狗默认 10 分钟为经验值，尚未用真实慢盘样本校准。

## 0.0.6 — 审计只读补设收窄、诊断包纳入清理池、诊断包按任务精确取日志

### 变更
- **审计只读补设不再全量扫描**：启动时只检查**最近 3 个** `audit_*.log` 并幂等补设（异常退出只可能漏设上一次）；更早文件若被手工去掉只读属性，程序不主动修复，避免上万个文件时拖慢启动。
- **诊断包纳入清理池**：`diag_*.zip` 与运行日志**共用同一组阈值与统计池**（200 MB / 30 天 / 100 个，大小与数量合并计算），审计日志仍独立；GUI 设置只需调一组数字。
- 诊断包**默认保存到 `{程序目录}\logs\`**（因此自动受清理管理）；改存其他目录时不受管理，界面明确提示。
- **诊断包范围改为按任务**：单次任务 = 本次运行日志；**延续任务** = 该任务全部历史运行日志。
- 新增**按任务日志索引**：`tasks\{task_id}.json` 维护 `log_files` / `audit_files` / `resumed`；任务在本进程首次写日志时追加文件名并立即 flush（不依赖退出钩子）。
- 审计行强制带 `task=<id>`（诊断包过滤依据），无任务归属的全局事件写 `task=none`。

### 新增
- **不漏不多保证**：逐文件只保留 `task=<id>` 行并按时间合并；合并总行数必须等于各来源匹配行数之和，否则中止不生成残缺包；索引中的文件被清理时记入 `manifest.missing[]` 并置 `exact=false`，UI 明示「历史不完整」；索引缺失则回退「当前日志整份」且标记 `exact=false`。
- 文档同步：`日志规范.md` v0.3（§4.1/§4.2/§5.3/§5.4/§7.2–§7.5/§9/§10）、`需求分析.md` v0.6（FR9.5 与验收）、`GUI与框架设计.md` v0.6（§6.4.11 阈值标注、§6.4.13 范围与提示）、`软件流程图.md` v0.5（§19 补设范围、§20 按任务取日志）、`项目计划.md` v0.6（WP0-T10、WP9-T19、风险表、M2 验收）。

### 已知限制
- 索引之外的历史日志（旧版本产生、手工移动或改名）不会被收入诊断包，按 `exact=false` 处理。
- 最近 3 个之外被手工清掉只读属性的审计文件不会自动修复（可「立即清理」或忽略）。

## 0.0.5 — 便携式分发、审计只读、一键诊断包、参数统一双横线

### 变更
- **日志目录改为 `{程序目录}\logs\`**：程序采用**便携式分发**（zip 解压即用），`logs\`、`config\`、`presets\`、`tasks\`、`tools\` 全部与程序同目录，不写注册表、不写 `%LocalAppData%`；日志目录不可写时启动即报错并提示，不再静默回退。
- 移除 `portable.flag` 便携模式判定（便携已成为唯一分发形态）；`需求分析.md` §13 待办同步删除该条。
- **CLI 参数统一双横线**：`--config` / `--dry-run` / `--resume` / `--debug`；**取消单横线兼容**，`-debug` 按未知参数报错（退出码 4）。
- **审计日志只读**：进程退出时置 `FILE_ATTRIBUTE_READONLY`；每次启动对历史 `audit_*.log` 幂等补设；GUI 以只读视图打开；清理删除前先解除属性，并在运行日志记 `event=audit_pruned`（含原因）。
- `GUI与框架设计.md` §5.4 配置/预设/任务路径改为程序目录（便携），§3.2 发布产物改为 PyInstaller 单目录便携包。

### 新增
- **FR9.5 一键打包诊断（P1）**：GUI 日志页 / 设置页 / 失败错误卡片入口；一键收集最近运行日志、本次审计日志（只读复制）、脱敏设置与任务配置、环境信息、外部工具版本，生成 `diag_时间戳.zip`（含 `manifest.json` 与 SHA256）；纯本地不上传；排除 ISO/WIM/ESD 与工具二进制；失败不影响主流程。规范见 `日志规范.md` §7。
- `GUI与框架设计.md` 新增 §6.4.13 诊断包线框；§6.1 日志页与 §6.4.11 设置页补入口与审计只读标识。
- `软件流程图.md` 新增 §20 诊断包流程；§19 增加「对历史 audit 补设只读」节点。
- `项目计划.md` 新增 WP9-T19 诊断包；WP0-T10 增加审计只读化；WP11-T3 改为便携绿色包；M2 验收增加诊断包。
- `软件架构图.md` §7 部署图改为程序目录结构（`logs/`、`config/`、`presets/`、`tasks/`、`tools/`）。

### 已知限制
- 只读属性属**防误改**手段，不是安全边界（管理员可改回）；ACL / 只读归档列入开放问题。
- 诊断包默认仅含本次运行日志与最近 3 次（可选），不含全部历史；「包含全部历史日志」列为开放问题。
- CLI 不提供诊断包命令（保持最小命令集），需要时手工压缩 `logs\`。

## 0.0.4 — 新增《日志规范》并细化 FR9.3 / 新增 FR9.4 单实例

### 新增
- **`docs/需求分析/日志规范.md`（v0.1）**：把 FR9.3 从一句话需求细化为可编码规格：
  - **每次启动一个运行日志** `run_YYYYMMDD-HHmmss.log`，禁止按功能/模块拆文件；
  - **审计日志独立** `audit_YYYYMMDD-HHmmss.log`，与运行日志使用同一启动时间戳前缀；
  - **纯文本行格式**：`时间 [级别] [阶段] logger: 消息 | k=v`，级别 5 字符定宽；
  - **级别默认值**：exe 运行 INFO、源码 GUI DEBUG、CLI INFO；新增 CLI 一次性开关 `--debug`；
  - **清理策略**：目录总大小 / 保留天数 / 文件数量**三项均为硬上限，任一超限即从最旧删到达标**；运行日志默认 200 MB / 30 天 / 100 个，审计日志独立 20 MB / 365 天 / 200 个；GUI 设置页可改，CLI 不提供设置入口但启动时按同一配置清理；
  - **进度不入日志**：第三方工具的进度条/百分比只走进度通道（GUI 进度条、CLI 终端），日志只记 `event=start` 与 `event=finish`（含 `exit`、`duration`、产出大小）；无法分类的同前缀行超 50 行时采样；
  - **脱敏**：代理凭据、产品密钥、URL 签名参数强制掩码；
  - **单实例**：命名互斥量 `Local\UUP2ISO.SingleInstance`；GUI 冲突激活已有窗口并退出（码 0），CLI 冲突输出 `already_running` 并退出（码 5）；冲突实例不建日志、不清理。
- `需求分析.md` 新增 **FR9.4 单实例运行（P0）**；FR10.4 设置新增「日志清理阈值」项；非功能需求新增「可观测性」「单实例」两行；§13 待办新增便携模式条目。

### 变更
- `需求分析.md` FR9.3 由「错误详情含外部工具原始输出」改为「含外部工具**关键输出**（进度条与百分比只显示不落盘）」，并指向 `日志规范.md`（该文件优先级更高）。
- FR9.2 退出码由 `0/1/2/3/4` 扩展为 `0/1/2/3/4/5`（`5` = 已有实例运行），并补充 `--debug` 一次性开关。
- `GUI与框架设计.md`：§3.3 日志实现由 `RotatingFileHandler` 改为「自定义文件处理器（每进程一文件、三条件清理、审计分流）」；§5.3 `LogSink` 字段与「只记头尾」；§5.6 退出码补 `5`；§6.1 日志页说明；§6.4.10 日志面板示例改为新格式并标注「进度不入日志」；§6.4.11 设置页新增「日志」页签线框；§9 开发规范新增日志约束。
- `软件架构图.md`：§4.1 `LogSink` 标注 Protocol 与双出口；§7 部署图日志文件改为 `run_*.log` 与 `audit_*.log`。
- `软件流程图.md`：§1 新增「单实例校验与日志初始化」条目；新增 §19 单实例与日志初始化流程；§17 退出码补 `5`。
- `项目计划.md`：WP0-T10 重写为可验收的日志基础设施；新增 WP0-T11 单实例守卫；WP9-T14 设置页补「日志」；DoD 第 4 条改为「关键输出入日志、进度不落盘」；交付物文档列新增日志规范；M0 交付物与风险表同步。
- `FR3-FR5详细需求.md` §5.2 改为「关键输出进日志，进度行只显示不落盘」，依据新增 `日志规范.md`。
- 文档版本：需求分析 / GUI与框架设计 / 软件架构图 / 项目计划 → v0.4；软件流程图 → v0.3；FR3-FR5 → v0.2。

### 已知限制
- 日志为纯文本，未提供机器可解析的事件导出（如需 CI 消费，走 CLI 的 JSON 结果而非日志）。
- 便携模式（`portable.flag`）判定与「一键打包诊断」仍为开放问题。
- 审计写入失败将阻断高风险操作，此行为尚未在 GUI 交互中细化提示文案。

## 0.0.3 — CLI 范围收敛为「最小 CLI」（方案 A）

### 变更
- **CLI 定位收敛**：不再建设完整命令行参数体系，命令集限定为 `run --config <TaskConfig>`、`--dry-run`、`--resume`、`version`；**不做字段级参数覆盖**，所有业务选项以 `TaskConfig` 为唯一契约（GUI 保存的配置即 CLI 的输入）。
- 配置优先级简化为 **任务配置 > 全局设置**（原来为「CLI 参数/任务配置 > 全局设置」）；删除 `--source-url`、`--input` 等字段级参数。
- 受影响需求：FR9.2（改为最小命令集）、FR10.2（限定命令集）、FR1.4（去掉 CLI 覆盖）、FR0.1（输入源由配置声明）、FR9/FR10 验收标准、FR9.2 验收新增「CLI 仅接受配置文件与运行开关」。
- 受影响任务：`项目计划.md` WP1-T5（CLI 参数解析 → 最小 CLI）、WP0-T6/T10 表述、M1 目标与验收、§2.3 交付物。
- 文档版本：`需求分析.md` / `GUI与框架设计.md` / `软件架构图.md` / `项目计划.md` → v0.3；`软件流程图.md` → v0.2（§18 配置加载改为「运行开关」，非字段覆盖）。

### 新增
- `项目计划.md` WS1 增补「CLI 定位（方案 A）」说明；§11 开放问题新增 CLI 范围终定条目。
- `软件架构图.md` §9 增补配置合并规则说明：CLI 只选择输入文件与运行模式，不存在同一选项两处定义。

### 已知限制
- 最小 CLI 不提供交互式配置能力：命令行用户必须先有 `TaskConfig`（由 GUI 生成或手工编写）。
- 依赖 CLI 字段级覆盖的旧表述已全部移除；若后续需要 `--source-url` 等临时覆盖，须先修订 FR9.2 并新增验收用例。

## 0.0.2 — 技术栈迁移：C# / .NET 8 → Python（文档阶段，M0 前）

### 变更
- 技术栈由 C# / .NET 8 + WPF 全面切换为 **Python 3.12+（x64）**：
  - GUI 框架由 WPF + WPF-UI 改为 **PySide6 + PyQt-Fluent-Widgets**；
  - 发布方式由 .NET self-contained single-file 改为 **PyInstaller** 打包（单目录优先，`--uac-admin` 提权清单）；
  - 工程结构由 `UUP2ISO.sln` 多项目改为 `pyproject.toml` + `src/` 包布局（`uup2iso.core / application / infrastructure / gui / cli`）；
  - 第三方库映射：CommunityToolkit.Mvvm → Qt 信号槽 + Presenter；Hosting/DI → `dependency-injector` 或组合根手动装配；Serilog → `logging` + `RotatingFileHandler`；System.Text.Json → `pydantic`；xUnit/FluentAssertions → `pytest` + `pytest-qt`；新增 `httpx`、`typer`、`jsonschema`、`lxml`、`ruff`、`mypy`、`pyinstaller`。
- 版本与变更管理约定改写：版本同步点由 `Directory.Build.props`/`AppInfo.cs`/`app.manifest` 改为 `pyproject.toml`/`src/uup2iso/__init__.py`/`tests/test_version.py`；验证命令由 `dotnet build`/`dotnet test` 改为 `.venv` 内 `pytest` + `ruff` + `mypy`。
- 需求文档新增运行环境风险条目与缓解策略：PyInstaller 打包代价、依赖污染系统 Python 环境。
- `.gitignore` 增加 Python 相关忽略项（`.venv/`、缓存目录、构建产物）。

### 新增
- **运行环境强制约定**：本仓库所有脚本（开发、测试、构建、打包、自动化代理）一律在工作空间根目录的虚拟环境 `.venv` 中运行，禁止使用系统 Python 主环境；初始化 `python -m venv .venv`，统一以 `.venv\Scripts\python` 执行；禁止全局 `pip install`。该约定写入 `需求分析.md` §7.1、`GUI与框架设计.md` §3.2 与 §9、`软件架构图.md` §7、`项目计划.md` WP0-T1/T2/T2a、`版本与变更管理.md` §2 与 §6。

### 已知限制
- 未改动 `FR3-FR5详细需求.md`、`软件流程图.md`、`转换路线验证.md` 中的 `.NET 3.5` / `LCU/SSU/.NET` 表述：这些指 Windows 更新与组件内容，与技术栈无关。
- 软件版本同步点（`pyproject.toml`、`src/uup2iso/__init__.py`、`tests/test_version.py`）当前尚不存在，将在 M0 建立 Python 工程骨架时一并落地并同步版本号。
- PyInstaller 产物体积、冷启动与杀软误报的代价已决策接受；代码签名与依赖精简策略仍为开放项。
