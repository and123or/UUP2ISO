# CHANGELOG

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
