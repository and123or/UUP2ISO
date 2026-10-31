# UUP2ISO FR3–FR5 详细需求

> 版本：v0.4
> 分类：需求（详细需求） ｜ 目录：`docs/需求/`（草案）
> 依据：`docs/需求/需求分析.md`（FR3 转换 / FR4 精简 / FR5 系统优化）、`docs/规范/日志规范.md`（§5.2 进度不入日志）
> 参考实现：**FR3** 对齐 `uup-converter-wimlib`（`ConvertConfig.ini`）；**FR5** 对齐 **Dism++**（`Data.xml` 规则库）
> 交付物：逐选项/逐项的**描述**与**实现方式**（可指导编码）

---

## 1. 说明

### 1.1 范围

本文档细化三个功能域的每一个选项/项：

| 域 | 内容 | 编号前缀 |
|---|---|---|
| FR3 转换 | 转换与映像组装的全部选项 | `CV-xx` |
| FR4 精简 | Appx / 组件 / 能力 / 硬件检查 等可移除内容 | `SL-A/P/C/H-xx` |
| FR5 系统优化 | 空间回收 / 注册表 tweak / 服务 / CompactOS | `OP-C/T/S/K-xx` |

### 1.2 字段约定

- **取值/默认**：取值范围与出厂默认。
- **描述**：该选项做什么、对结果的影响。
- **实现方式**：本工具落地手段（外部命令 / 注册表 / 挂载操作 / 复用脚本）。
- **风险**：副作用或不可逆性；`高` 项默认关闭并需显式确认。
- **uup / Dism++**：对应的 `ConvertConfig.ini` 键或 Dism++ 规则来源，便于迁移与被审计。

通用前置：`$Mount = C:\UUP2ISO\mount`；离线映像操作均需管理员权限并遵守“先 `Mount` → 操作 → `Commit` → `Unmount`”。

### 1.3 实现方式图例

> 规则执行上下文必须区分为 `offline`、`online` 或 `firstBoot`。离线任务不得将仅在线可用的规则报告为成功。

| 记号 | 含义 |
|---|---|
| `DISM /Image:$Mount ...` | 离线映像服务（系统内置） |
| `wimlib-imagex ...` | WIM/ESD 读写与导出（随包分发） |
| `oscdimg ...` | ISO 封装（ADK） |
| `reg load/unload` | 离线注册表（挂载映像/默认用户/系统 hive） |
| `powercfg` / `compact` / `NETSH` | 系统命令行工具 |
| `convert-UUP` | 复用 `uup-converter-wimlib` 脚本（等价模式） |

### 1.4 离线注册表基础（FR5 通用）

```
reg load HKLM\OFFLINE_SW   $Mount\Windows\System32\config\SOFTWARE
reg load HKLM\OFFLINE_SY   $Mount\Windows\System32\config\SYSTEM
reg load HKLM\OFFLINE_DEF  $Mount\Windows\System32\config\DEFAULT
reg load HKLM\OFFLINE_NT   $Mount\Users\Default\NTUSER.DAT
:: ……reg add / reg delete ……（HKLM\OFFLINE_* 对应 HKEY_LOCAL_MACHINE\OFFLINE_*）
reg unload HKLM\OFFLINE_SW & reg unload HKLM\OFFLINE_SY & reg unload HKLM\OFFLINE_DEF & reg unload HKLM\OFFLINE_NT
```

---

## 2. FR3 转换选项

### 2.1 总览

| 编号 | 选项 | 取值/默认 | 描述 | 实现方式 | 风险 | uup |
|---|---|---|---|---|---|---|
| CV-01 | 输出模式 | 1/2/3/4，默认 1 | 决定最终产物：ISO+install.wim / ISO+install.esd / 仅 install.wim / 仅 install.esd | 编排产出物集合；决定后续 CV-07/09 | 低 | `AutoStart` |
| CV-02 | 集成更新 | 0/1/2，默认 1 | 将 UUP 源内的 LCU/SSU/.NET 累积更新集成进映像（1）或外部投放（2） | 1：`DISM /Add-Package`；2：随 ISO 交付由安装程序应用 | 中 | `AddUpdates` |
| CV-03 | 组件清理 | 0/1，默认 1 | 集成更新后清理被取代组件并增量压缩（缩短后续体积） | `DISM /Image:$Mount /Cleanup-Image /StartComponentCleanup` | 低 | `Cleanup` |
| CV-04 | 重置基础 | 0/1，默认 0 | 在清理基础上进一步移除被取代组件（体积更小） | `DISM /Cleanup-Image /StartComponentCleanup /ResetBase` | 中（不可卸载旧更新） | `ResetBase` |
| CV-05 | 启用 .NET 3.5 | 0/1，默认 0 | 离线启用 NetFx3 功能 | `DISM /Enable-Feature /FeatureName:NetFx3 /All /LimitAccess /Source:$Mount\sources\sxs` | 低 | `NetFx3` |
| CV-06 | 启动虚拟版本 | 0/1，默认 0 | 实验性能力；仅对完成构建级验证的版本创建虚拟版本 | 仅调用已验证的 DISM/转换流程，不得仅改注册表伪造 edition | 高 | `StartVirtual` |
| CV-07 | 转 ESD | 0/1，默认 0 | 将 install.wim 转为 install.esd（LZMS recovery 压缩） | `DISM /Export-Image /Compress:recovery` | 低（耗时/占资源） | `wim2esd` |
| CV-08 | 拆分 WIM | 0/1，默认 0 | 将 install.wim 拆分为 install.swm（FAT32 友好） | `DISM /Export-Image /FileSize:4096` | 低（<4G 自动跳过） | `wim2swm` |
| CV-09 | 跳过 ISO | 0/1，默认 0 | 仅输出 wim/esd，不封装 ISO | 跳过 oscdimg 阶段 | 低 | `SkipISO` |
| CV-10 | 跳过 WinRE | 0/1，默认 0 | 不将 winre.wim 并入映像（缩小体积、加快转换） | 转换时排除 winre.wim | 中（恢复环境功能减少） | `SkipWinRE` |
| CV-11 | 更新 WinRE | 0/1，默认 0 | 即使检测到 SafeOS 更新也强制用 LCU 更新 winre.wim | `DISM /Add-Package` 到 winre.wim | 低（26052+ 忽略） | `LCUwinre` |
| CV-12 | LCU 展开方式 | 0/1/2/3，自动 | LCU 以 update.mum 松散文件安装 or 直接 msu | 松散文件集成 / 逐个 msu / 仅最高 msu | 低（22621+/26052+ 自动） | `LCUmsuExpand` |
| CV-13 | 禁用升级优化 | 0/1/2，自动 | 关闭“以 edition 升级”加速更新，改为逐 edition 更新 | 逐 edition 独立更新 | 低（26000+ 自动） | `DisableUpdatingUpgrade` |
| CV-14 | 更新引导文件 | 0/1，默认 0 | 用更新包内 bootmgr/memtest/efisys.bin 刷新 ISO 引导（含 2023 UEFI CA） | 替换 ISO 骨架 `efi/`、`boot/` 文件 | 低 | `UpdtBootFiles` |
| CV-15 | 强制 DISM | 0/1，默认 0 | 用系统 dism.exe 生成 boot.wim（需 Win10/11 主机或 ADK） | `DISM /Export-Image`（boot.wim） | 低（更稳） | `ForceDism` |
| CV-16 | 保留参考 ESD | 0/1，默认 0 | 保留转换出的参考 ESD，或将 Express 源备份为 Canonical | 复制到 `CanonicalUUP\` | 低（占空间） | `RefESD` |
| CV-17 | 跳过 LCU MSU | 0/1，默认 0 | 对 21382–25330 生成 LCU msu（旧式 cab+psf 提取） | 旧式提取路径 | 低 | `SkipLCUmsu` |
| CV-18 | 跳过 Edge | 0/1/2，默认 0 | 不添加/不集成 Microsoft Edge Chromium | 排除 Edge.wim / 不集成 | 中 | `SkipEdge` |
| CV-19 | 自动退出 | 0/1，默认 0 | 转换完成后不等待输入直接退出（配合无人值守批处理） | 退出码返回 | 低 | `AutoExit` |
| CV-20 | 添加驱动 | 0/1，默认 0 | 将驱动注入 install.wim 与 boot/winre.wim | `DISM /Add-Driver /Recurse` | 中（需兼容驱动） | `AddDrivers` |
| CV-21 | 驱动源目录 | 路径，默认 `\Drivers` | 指定 `ALL/OS/WinPE` 子目录方式的驱动来源 | 见 §2.7 | 低 | `Drv_Source` |
| CV-22 | 跳过应用 | 0/1，默认 0 | 不添加 Store 应用 | 排除应用集成 | 低 | `SkipApps` |
| CV-23 | 应用级别 | 0–4，默认 0 | 控制随映像添加的 Store 应用数量 | 按级别清单集成 | 低 | `AppsLevel` |
| CV-24 | Stub 应用完整化 | 0/1，默认 0 | 以完整应用替代存根（stub） | 集成完整包 | 低 | `StubAppsFull` |
| CV-25 | 自定义应用清单 | 0/1，默认 0 | 用 CustomAppsList.txt 精确勾选应用（优先于 CV-23） | 解析清单集成 | 低 | `CustomList` |
| CV-26 | 压缩类型 | none/XPRESS/LZX/LZMS | WIM/ESD 压缩算法 | wimlib `--compress=` 或 DISM `/Compress:` | 低 | — |
| CV-27 | edition 保留/裁剪 | 列表 | 从 AIO 中保留或剔除指定 edition | `DISM /Delete-Image` 或仅导出目标 index | 中 | 安装向导选择 |
| CV-28 | ISO 卷标 | 字符串 | ISO 卷标（默认派生自 build 与语言） | `oscdimg -l<label>` | 低 | — |
| CV-29 | ESD 解密 | 0/1，默认 0 | 后续实验性能力；未完成固定构建验证时拒绝处理并说明原因 | 仅使用经过许可和校验的解密适配器 | 高 | — |

### 2.2 输出与产物（CV-01 / CV-07 / CV-08 / CV-09 / CV-26 / CV-28）

- **CV-01 输出模式**：映射 `AutoStart`，取值语义与 UUP dump 脚本一致，便于从现有工作流迁移；在本工具中该语义由 `convert.mode`（`wim_only`/`full`）+ `output.type`（`iso`/`wim`/`esd`/`swm`）两个字段共同表达（配置规范 §3.3/§3.4），不单独保留 `AutoStart` 的 1/2/3/4 数值。
- **CV-07 / CV-08 / CV-09 不再互为开关**：「出 ESD / 出 SWM / 只出映像」统一由 `output.type` 单选表达，因此不存在「同时开启」的冲突；`wim2swm` 属后置能力，本期选择即拒绝（`OUTPUT_TYPE_UNSUPPORTED`）。
- **CV-26 压缩类型映射**：

  | 取值 | wimlib `--compress` | DISM `/Compress:` | 说明 |
  |---|---|---|---|
  | none | `none` | `none` | 不压缩，最快 |
  | XPRESS（fast） | `XPRESS` | `fast` | DISM 的 fast 档；本工具默认 LZX（配置规范 §5） |
  | LZX（max） | `LZX` | `max` | 体积较小 |
  | LZMS（recovery） | `LZMS` | `recovery` | install.esd 用 |

- **CV-28 ISO 封装命令**（UEFI+BIOS 双启动）：
  ```
  oscdimg -m -o -u2 -udfver102 ^
    -bootdata:2#p0,e,b<ISO>\boot\etfsboot.com#pEF,e,b<ISO>\efi\microsoft\boot\efisys.bin ^
    -l<volume> <ISO源目录> <输出.iso>
  ```

### 2.3 更新集成与清理（CV-02 / CV-03 / CV-04 / CV-12 / CV-13 / CV-17）

- **CV-02 集成更新**：
  - 模式 1（集成进 wim）：挂载 install.wim → 按 SSU→LCU→.NET 顺序 `DISM /Add-Package`（或经 `update.mum` 松散文件）→ 提交。
  - 模式 2（外部投放）：更新包放入 ISO 的 `sources` 并配置在安装阶段应用（17763+）。
- **CV-03 / CV-04**：仅在 CV-02=1 且为 WIM 集成时生效；`ResetBase` 依赖 `Cleanup`。
- **CV-12 / CV-13**：按构建版本自动决策（见总览“自动”），高级模式可覆盖。

### 2.4 版本与虚拟版本（CV-06 / CV-27 / CV-P1~P8）

虚拟版本是实验性能力，不等同于修改注册表中的 edition 标识。只有在固定 Windows 构建上完成安装、功能完整性和 DISM 校验后，才允许开放；MVP 不包含该能力。

| 编号 | 子项 | 取值/默认 | 描述 | 实现方式 | uup |
|---|---|---|---|---|---|
| CV-P1 | 使用 DISM | 0/1，默认 1 | 用 DISM 而非 wimlib 生成虚拟版本 | `DISM /Export-Image` + edition 改写 | `vUseDism` |
| CV-P2 | 自动开始 | 0/1，默认 1 | 转换后无需交互直接创建 | 无人值守执行 | `vAutoStart` |
| CV-P3 | 删除源 | 0/1，默认 0 | 生成后删除原始 edition | 清理源映像 | `vDeleteSource` |
| CV-P4 | 保留映像 | 0/1，默认 0 | 保留中间映像供复用 | 保留临时 edition | `vPreserve` |
| CV-P5 | 转 ESD | 0/1，默认 0 | 虚拟版本结果转 install.esd | 同 CV-07 | `vwim2esd` |
| CV-P6 | 拆分 WIM | 0/1，默认 0 | 虚拟版本结果拆分 | 同 CV-08 | `vwim2swm` |
| CV-P7 | 跳过 ISO | 0/1，默认 0 | 虚拟版本仅输出映像 | 跳过封装 | `vSkipISO` |
| CV-P8 | 虚拟版本列表 | 列表，默认空 | 要创建的 edition（如 `Professional`、`Enterprise`） | 逐个改写 edition 并导出 | `vAutoEditions` |

**实现约束**：不得仅通过修改 `EditionID`、`ProductName` 或 `InstallationType` 伪造版本。实现必须使用已验证的官方映像转换机制或等价适配器，并在固定构建样本上验证 `DISM /Get-WimInfo`、安装和功能完整性。

### 2.5 组件/功能与 WinRE（CV-05 / CV-10 / CV-11 / CV-18 / CV-22~CV-25）

- **CV-05 NetFx3**：源 `sxs` 缺失时跳过并告警（26000+ / 27965+ 视构建而定）。
- **CV-10/11 WinRE**：`SkipWinRE` 从产物中排除；`LCUwinre` 强制更新 winre.wim。
- **CV-22~25 应用**：`AppsLevel` 分级与 `CustomList` 精确控制；`Extra`：支持随包的 LanguageExperiencePack（LXP）一并集成。

### 2.6 引导与 ISO（CV-14 / CV-28）

- **CV-14**：从更新包提取 `bootmgr`/`bootmgr.efi`/`memtest`/`efisys.bin` 刷新 ISO 引导，含 2023 UEFI CA 引导文件（KB5053484 语义）。

### 2.7 集成类：驱动（CV-20 / CV-21）

```
:: Drv_Source 下约定子目录
::   ALL\     → install.wim + boot.wim + winre.wim
::   OS\      → 仅 install.wim
::   WinPE\   → 仅 boot.wim / winre.wim
DISM /Image:$Mount /Add-Driver /Driver:<Drv_Source>\OS /Recurse
DISM /Image:$Mount /Add-Driver /Driver:<Drv_Source>\ALL /Recurse
```
仅做“指针式”集成、不校验兼容性；建议仅用于芯片组/磁盘控制器/网卡等启动关键驱动。

### 2.8 自动化与调试（CV-19 / CV-16）

- **CV-19 `AutoExit`**：用于批量/CI；GUI 下默认等待查看结果。
- **CV-16 `RefESD`**：保留参考 ESD / 备份 Canonical 源，便于二次定制复用。

### 2.9 ESD 解密（CV-29）

加密 ESD 需先载入解密键（内置 Win8.1/10 各 build 的键，可写扩展）。实现：匹配映像 build → 载入键 → 解密 → 再进入常规流程。

### 2.10 默认与预设（对齐里程碑）

- **原味转换预设**：CV-01=1、CV-02=1、CV-03=1、CV-04=0、CV-05=0、CV-07=0、CV-10=0。
- **精简体积预设**：在上述基础上 CV-07=1、CV-10=1、CV-26=LZMS。
- 所有选项可保存进 `TaskConfig.Convert` 并在 CLI 复现。

---

## 3. FR4 精简

> **执行机制不在本节**：规则的上下文判定、依赖矩阵、冲突阻断、逐条验证与可逆性记录见 `精简与优化规则规范.md`；本节只定义「有哪些规则、各自做什么」。

### 3.1 总览

精简分四类：**Appx/UWP 应用**（`SL-A`）、**可选组件/包**（`SL-P`）、**Windows 能力/功能**（`SL-C`）、**安装硬件检查绕过**（`SL-H`）。通用前置：挂载映像（`DISM /Mount-Image` 或 `wimlib` 挂载）。

默认预设分档：**安全精简**（仅第三方推广/游戏类 Appx + 无害组件）、**激进精简**（含 Defender/Edge/OneDrive/WinRE 等）。移除后**不可恢复**项需标注并二次确认。

### 3.2 Appx / UWP 应用（`SL-A`）

实现方式统一为（离线）：
```
DISM /Image:$Mount /Get-ProvisionedAppxPackages
DISM /Image:$Mount /Remove-ProvisionedAppxPackage /PackageName:<PackageFullName>
```

| 编号 | 应用（包名） | 描述 | 风险 |
|---|---|---|---|
| SL-A01 | Microsoft.549981C3F5F10 (Cortana) | 语音助手 | 中 |
| SL-A02 | Microsoft.BingNews / BingWeather / BingSearch | 资讯/天气/搜索推广 | 低 |
| SL-A03 | Microsoft.GetHelp / Getstarted | 获取帮助/使用技巧 | 低 |
| SL-A04 | Microsoft.MicrosoftOfficeHub | Office 推广入口 | 低 |
| SL-A05 | Microsoft.MicrosoftSolitaireCollection | 纸牌 | 低 |
| SL-A06 | Microsoft.MicrosoftStickyNotes | 便笺 | 低 |
| SL-A07 | Microsoft.People | 联系人 | 低 |
| SL-A08 | Microsoft.PowerAutomateDesktop | 自动化 | 低 |
| SL-A09 | Microsoft.Todos | 待办 | 低 |
| SL-A10 | Microsoft.WindowsAlarms | 闹钟 | 低 |
| SL-A11 | Microsoft.WindowsCamera | 相机 | 低 |
| SL-A12 | Microsoft.windowscommunicationsapps | 邮件/日历 | 中 |
| SL-A13 | Microsoft.WindowsFeedbackHub | 反馈中心 | 低 |
| SL-A14 | Microsoft.WindowsMaps | 地图 | 低 |
| SL-A15 | Microsoft.WindowsSoundRecorder | 录音机 | 低 |
| SL-A16 | Microsoft.ZuneMusic (Media Player) | 媒体播放器 | 中 |
| SL-A17 | Microsoft.ZuneVideo (电影和电视) | 视频 | 低 |
| SL-A18 | Microsoft.YourPhone (手机连接) | 手机连接 | 低 |
| SL-A19 | Microsoft.Xbox* | Xbox 游戏相关全家桶（含 GameOverlay/GamingOverlay/IdentityProvider/TCUI/SpeechToText） | 中（影响 Game Pass） |
| SL-A20 | Clipchamp.Clipchamp | 视频剪辑 | 低 |
| SL-A21 | MicrosoftTeams | Teams 个人版 | 低 |
| SL-A22 | Microsoft.OutlookForWindows | 新版 Outlook | 低 |
| SL-A23 | Microsoft.QuickAssist | 快速助手 | 低 |
| SL-A24 | Microsoft.Family | 家庭 | 低 |
| SL-A25 | Microsoft.Windows.DevHome | 开发主页 | 低 |
| SL-A26 | Microsoft.MicrosoftJournal 等第三方 Stub（Disney+、Spotify、Netflix 等） | 第三方推广存根 | 低 |

### 3.3 可选组件 / 包（`SL-P`，DISM Package）

离线包移除实现：
```
DISM /Image:$Mount /Get-Packages
DISM /Image:$Mount /Remove-Package /PackageName:<PackageName>
```

| 编号 | 组件（示例包名前缀） | 描述 | 实现方式 | 风险 |
|---|---|---|---|---|
| SL-P01 | Windows-Defender-* (Client/Feature) | Microsoft Defender 防病毒 | Remove-Package / Remove-WindowsCapability | 高 |
| SL-P02 | Microsoft-Edge / Microsoft-Windows-Edge | 系统 Edge | Remove-Package | 高 |
| SL-P03 | OneDrive（`Microsoft-Windows-OneDrive` / 客户端包） | 云盘同步 | Remove-Package + 取消 Task | 中 |
| SL-P04 | WinRE（winre.wim） | 恢复环境 | 移除 winre.wim 组件（见 CV-10/folder） | 中 |
| SL-P05 | Internet-Explorer / IE 可选功能 | 旧版 IE | Remove-WindowsCapability | 中（依赖 IE 的旧软） |
| SL-P06 | Windows-MediaPlayer / WMPlayer | 旧版媒体播放器 | Remove-WindowsCapability | 低 |
| SL-P07 | NetFx3（旧版 .NET 3.5） | .NET 3.5 支持 | Remove-WindowsCapability（与 CV-05 相反） | 中 |
| SL-P08 | SMB1 协议（SMB1Protocol） | 旧共享协议 | Remove-WindowsCapability | 低 |
| SL-P09 | Microsoft-Windows-Search（Windows Search 组件） | 桌面搜索 | Remove-Package / 服务禁用 | 中 |
| SL-P10 | Cortana（Cortana 包） | 语音助手服务 | Remove-Package | 中 |
| SL-P11 | Windows-Hello（Hello.Face/Fingerprint） | 生物识别 | Remove-Package | 中 |
| SL-P12 | Hyper-V（Microsoft-Hyper-V-*） | 虚拟化平台 | Remove-WindowsCapability | 低 |
| SL-P13 | WSL（Microsoft-Windows-Subsystem-Linux） | Linux 子系统 | Remove-WindowsCapability | 低 |
| SL-P14 | Windows Sandbox（Containers-DisposableClientVM） | 沙盒 | Remove-WindowsCapability | 低 |
| SL-P15 | Mixed Reality（MixedReality.Portal） | 混合现实门户 | Remove-Package | 低 |
| SL-P16 | Xbox 服务（Xbox 相关包） | 游戏服务 | Remove-Package | 中 |
| SL-P17 | 反馈与诊断（Client-Experience/Telemetry 相关包） | 遥测组件 | Remove-Package | 中 |
| SL-P18 | Windows Insider Hub | 内部预览 | Remove-Package | 低 |
| SL-P19 | 传真/扫描（FaxServicesClientPackage） | 传真与扫描 | Remove-WindowsCapability | 低 |
| SL-P20 | 打印管理/打印相关（Printing 组件，按需） | 打印子系统 | Remove-Capability | 中 |
| SL-P21 | 旧版组件：DirectPlay、PS2/PS3 等 | 兼容组件 | Remove-WindowsCapability | 低 |
| SL-P22 | 语言资源不用的语言包（LanguageFeatures-*） | 多余语言功能 | Remove-Package（按语言清单） | 中 |
| SL-P23 | Steps Recorder（App.StepsRecorder） | 步骤记录器 | Remove-WindowsCapability | 低 |
| SL-P24 | 无线显示（App.WirelessDisplay.Connect） | 投屏接收 | Remove-WindowsCapability | 低 |

> 实际包名以 `Get-Packages` / `Get-Capabilities` 结果为准；工具内置“包依赖矩阵”，对会引发依赖断裂的组合（例如移除 Defender 却保留安全中心 App）阻断并提示顺序。

### 3.4 Windows 能力 / 功能（`SL-C`，Capability）

实现方式：
```
DISM /Image:$Mount /Get-Capabilities
DISM /Image:$Mount /Remove-WindowsCapability /Name:<Capability.Name>~~~~0.0.1.0
```

| 编号 | 能力（示例） | 描述 | 风险 |
|---|---|---|---|
| SL-C01 | OpenSSH.Client / OpenSSH.Server | SSH 客户端/服务端 | 低 |
| SL-C02 | Microsoft.Windows.WMPlayer | Windows Media Player | 低 |
| SL-C03 | MediaPlayback | 媒体播放支持 | 中 |
| SL-C04 | VBSCRIPT | VBScript 引擎 | 中 |
| SL-C05 | MathRecognizer | 数学输入识别 | 低 |
| SL-C06 | Printing-Foundation-* | 打印基础功能 | 中 |
| SL-C07 | SearchEngine-Client-Package | 搜索 | 中 |
| SL-C08 | App.StepsRecorder / App.SupportAssistant / App.VoiceAssistance | 辅助 App | 低 |
| SL-C09 | Microsoft.Windows.Notepad / Paint（Win11 收件箱） | 基础 App | 低 |
| SL-C10 | Language.Basic/Handwriting/OCR/Speech 等（按语言） | 语言功能 | 中（影响输入） |

### 3.5 安装硬件检查与门槛绕过（`SL-H`）

| 编号 | 项 | 描述 | 实现方式 | 风险 |
|---|---|---|---|---|
| SL-H01 | 绕过 TPM 检查 | 安装时忽略 TPM 2.0 要求 | 注入 `HKLM\SYSTEM\Setup\LabConfig\BypassTPMCheck=1` | 低 |
| SL-H02 | 绕过 Secure Boot 检查 | 忽略安全启动要求 | `LabConfig\BypassSecureBootCheck=1` | 低 |
| SL-H03 | 绕过 CPU 检查 | 忽略受支持 CPU 列表 | `LabConfig\BypassCPUCheck=1` | 低 |
| SL-H04 | 绕过 RAM 检查 | 忽略最小内存要求 | `LabConfig\BypassRAMCheck=1` | 低 |
| SL-H05 | 绕过存储检查 | 忽略磁盘容量要求 | `LabConfig\BypassStorageCheck=1` | 低 |
| SL-H06 | 升级不受支持硬件 | 允许在不支持 TPM/CPU 上升级 | `HKLM\SYSTEM\Setup\MoSetup\AllowUpgradesWithUnsupportedTPMOrCPU=1` | 中 |
| SL-H07 | 替换 appraiserres.dll | 兼容性评估组件替换/移除 | 从目标构建自身 appraiserres.dll 替换 `sources\appraiserres.dll` | 中 |
| SL-H08 | 跳过联网与 MSA 强制（Win11 OOBE） | OOBE 允许本地账户/跳过联网 | 注入 `BypassNRO`（`\Windows\System32\oobe\BypassNRO.cmd`）或对应应答文件项 | 低 |
| SL-H09 | 移除安装最低要求（ei.cfg/pid.txt） | 提供通用密钥与版本选择 | 写入 `sources\ei.cfg` / `pid.txt` | 低 |

### 3.6 依赖与顺序

- 移除顺序：**Appx → 能力(Capability) → 组件包(Package) → WinRE/文件级**，避免包依赖断裂。
- 冲突示例：移除 Defender 需同时处理安全中心 App 与服务；移除 Edge 需处理 WebView 依赖（某些系统组件）。
- 结果校验：精简后 `DISM /Get-Packages` 无挂起错误、`/Cleanup-Image /ScanHealth` 通过（见 FR8）。

### 3.7 精简预设

| 预设 | 包含 | 说明 |
|---|---|---|
| 安全精简 | SL-A02/A03/A05/A09/A10/A11/A13/A14/A17/A20/A26 等推广类；SL-C08 | 不影响系统可用性 |
| 标准精简 | 安全精简 + SL-A01/A07/A18/A19；SL-P13~P15/P18 | 常用 App 与可选功能 |
| 激进精简 | 标准精简 + SL-P01/P02/P03/P04/P06/P07/P16/P17；SL-H01~H09 | 高风险，默认需确认 |

---

## 4. FR5 系统优化（对齐 Dism++）

> **执行机制不在本节**：同上，见 `精简与优化规则规范.md`（含 `verify` 验证类型、高风险可逆性要求、上下文不满足时的跳过语义）。

### 4.1 总览

四类：**空间回收**（`OP-C`）、**注册表 tweak**（`OP-T`，按分类）、**服务优化**（`OP-S`）、**CompactOS/合并**（`OP-K`）。通用前置：挂载映像 + 离线注册表（§1.4）。风险分 `低/中/高`，`高` 项默认关闭。

> 规则可迁移自 Dism++ `Data.xml`：其清理项用 `<General RootPath>`（删除路径）+ `<Custom ProcName>`（内置例程）；优化项用 `<RegWrite>/<RegDelete>/<RegMove>/<SetServiceStart>`。本工具以等价注册表/命令实现，规则库采用版本化 JSON；MVP/M2 仅使用内置规则，后续只支持经 schema、版本和完整性校验的手动规则包导入。

### 4.2 空间回收（`OP-C`）

| 编号 | 项 | 描述 | 实现方式 | 风险 |
|---|---|---|---|---|
| OP-C01 | 被取代的 WinSxS 组件 | 清理被取代组件、缩减组件存储 | `DISM /Cleanup-Image /StartComponentCleanup`（可选 `/ResetBase`） | 中 |
| OP-C02 | 以前的 Windows 安装 | 升级/还原残留的 Windows.old | 删除 `%SystemDrive%\Windows.old*` | 中 |
| OP-C03 | Windows 更新安装记录 | 更新历史数据库 | 删除 `Windows\SoftwareDistribution\DataStore` | 中 |
| OP-C04 | Windows 下载缓存 | 已下载更新负载 | 删除 `SoftwareDistribution\Download` | 低 |
| OP-C05 | 传递优化缓存 | DO 对等缓存 | 删除 `SoftwareDistribution\DeliveryOptimization`（在线用专用例程） | 低 |
| OP-C06 | Windows 报告(WER) | 崩溃报告 | 删除 `…\Microsoft\Windows\WER\`、`LiveKernelReports` | 低 |
| OP-C07 | Windows 事件日志 | 事件日志 | 在线清空日志；离线删 `System32\winevt\Logs` | 低 |
| OP-C08 | 系统还原点 | 删除所有还原点 | 专用例程（删除后新建一个） | 中 |
| OP-C09 | Installer 目录 | 多余 .msp 残留 | 专用例程清理 `Windows\Installer` | 中 |
| OP-C10 | 过期驱动 | 清理驱动库未用驱动 | `DISM /Online` 驱动/专用例程 | 中 |
| OP-C11 | 过期/自带 Appx | 移除被取代或自带 Appx | 专用例程清理 Appx | 中 |
| OP-C12 | 损坏的 Appx | 修复损坏 Appx 注册 | 专用例程 | 低 |
| OP-C13 | 零售演示离线内容 | RetailDemo 内容 | 删除 `ProgramData\…\RetailDemo\` | 低 |
| OP-C14 | 临时文件 | 系统/用户临时目录 | 删 `%TEMP%`、用户 `Temp`、UWP `AC\Temp` 等 | 低 |
| OP-C15 | Windows 日志文件 | 各类 .log | 删 `Windows\*.log`、`Logs`、`Panther`、`WDI` 等 | 低 |
| OP-C16 | WinSxS 临时文件 | 清单缓存/CbsTemp | 删 `WinSxS\ManifestCache`、`CbsTemp` | 低 |
| OP-C17 | 缩略图缓存 | 缩略图/图标缓存 | 删 `Explorer\*.db`、`IconCache.db`（重启 Explorer） | 低 |
| OP-C18 | 网页缓存/Cookies(WinINet) | IE/WebView 缓存 | 删各用户 `INetCache`/`Cookies` | 低 |
| OP-C19 | 预读取文件(Prefetch) | 预读数据 | 删 `Windows\Prefetch` | 低 |
| OP-C20 | 内存转储文件 | 崩溃 dmp | 删 `%SystemRoot%\MEMORY.DMP`、`Minidump` | 低 |
| OP-C21 | 回收站 | 各盘回收站 | 删 `*\$Recycle.Bin` | 低 |
| OP-C22 | 驱动安装源缓存 | Intel/NVIDIA/Realtek 等安装源 | 删除对应缓存目录 | 低 |
| OP-C23 | 包缓存/Package Cache | WIX 安装源缓存 | 专用例程 | 低 |
| OP-C24 | 各类应用缓存（QQ/YY/百度网盘/VS/NuGet 等） | 第三方临时与日志 | 删除对应目录（规则库可扩展） | 低 |
| OP-C25 | CompactOS（较慢） | 对只读文件做压缩 | 见 OP-K01 | 中 |
| OP-C26 | 硬链接合并（较慢） | 合并硬链接文件 | 专用例程 | 中 |

> 空间回收在**离线映像**上仅对文件级路径与组件存储有意义；在线专属例程（事件日志、还原点、传递优化、Appx 修复等）必须标记为 `online`，离线模式下不展示为可执行项。

### 4.3 注册表 tweak（`OP-T`，offline 注册表写入）

以下 `¬` = `HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion`；离线时映射为已加载的 `OFFLINE_NT`（默认用户）或 `DEFAULT`。

#### 4.3.1 隐私与遥测 / 体验推广

| 编号 | 项 | 描述 | 实现方式 | 风险 |
|---|---|---|---|---|
| OP-T01 | 关闭遥测/诊断数据 | 降低诊断数据上报 | `HKLM\…\Policies\Microsoft\Windows\DataCollection\AllowTelemetry` DWORD `0`（<企业版可能被忽略） | 中 |
| OP-T02 | 禁用客户体验改善计划(CEIP) | 关闭 CEIP | `HKLM\SOFTWARE\Policies\Microsoft\SQMClient\Windows\CEIPEnable` DWORD `0` | 低 |
| OP-T03 | 关闭 Cortana | 禁用小娜 | `HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Search\AllowCortana` DWORD `0` | 中 |
| OP-T04 | 关闭活动历史 | 不上传活动历史 | `HKLM\…\Policies\Microsoft\Windows\System\EnableActivityFeed`=0、`PublishUserActivities`=0、`UploadUserActivities`=0 | 低 |
| OP-T05 | 关闭广告 ID | 禁用个性化广告 | `HKLM\…\Policies\Microsoft\Windows\AdvertisingInfo\DisabledByGroupPolicy` DWORD `1` | 低 |
| OP-T06 | 关闭开始菜单建议 | 不显示建议 | `¬\ContentDeliveryManager\SystemPaneSuggestionsEnabled` DWORD `0` | 低 |
| OP-T07 | 关闭商店应用推广 | 不预装推广应用 | `¬\ContentDeliveryManager\PreInstalledAppsEnabled` DWORD `0` | 低 |
| OP-T08 | 关闭锁屏聚焦推广 | 关闭 Spotlight 推广 | `¬\ContentDeliveryManager\RotatingLockScreenEnable` DWORD `0` | 低 |
| OP-T09 | 关闭技巧与建议 | 软着陆提示 | `¬\ContentDeliveryManager\SoftLandingEnabled` DWORD `0` | 低 |
| OP-T10 | 禁止自动安装推荐应用 | 静默安装推广应用 | `¬\ContentDeliveryManager\SilentInstalledAppsEnabled` DWORD `0` | 低 |
| OP-T11 | 关闭“在应用商店查找关联应用” | 打开方式不跳商店 | `HKLM\SOFTWARE\Policies\Microsoft\Windows\Explorer\NoUseStoreOpenWith` DWORD `1` | 低 |

#### 4.3.2 系统行为

| 编号 | 项 | 描述 | 实现方式 | 风险 |
|---|---|---|---|---|
| OP-T20 | 关闭休眠 | 移除 hiberfil | `powercfg -hibernate off` + `…\Control\Power\HibernateEnabled` DWORD `0` | 中 |
| OP-T21 | 关闭快速启动 | 禁用休眠式快速启动 | `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power\HiberbootEnabled` DWORD `0` | 低 |
| OP-T22 | 关闭系统还原 | 禁用还原点 | 关闭还原并清理（与 OP-C08 联动） | 中 |
| OP-T23 | 关闭预留存储 | 不保留更新空间 | `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\ReserveManager\ShippedWithReserves` DWORD `0` | 中 |
| OP-T24 | 关闭自动维护 | 禁用维护计划 | `HKLM\SOFTWARE\Policies\Microsoft\Windows\ScheduledDiagnostics\EnabledExecution` DWORD `0` | 低 |
| OP-T25 | 蓝屏自动重启 | BSOD 后自动重启 | `HKLM\SYSTEM\CurrentControlSet\Control\CrashControl\AutoReboot` DWORD `1/0` | 低 |
| OP-T26 | 崩溃转储类型 | 选择转储级别 | `…\CrashControl\CrashDumpEnabled` DWORD `0/2/3` | 低 |
| OP-T27 | 禁用 CBS/DPX 日志 | 减少日志占用 | `…\Component Based Servicing\EnableLog=0`、`EnableDpxLog=0` | 低 |
| OP-T28 | 禁用 CBS 组件备份 | 减少备份占用 | `…\SideBySide\Configuration\DisableComponentBackups` DWORD `1` | 中 |
| OP-T29 | 禁用系统日志/WER | 关闭错误报告日志 | `HKCU\…\Policies\…\WER\LoggingDisabled=1` + `HKLM\…\Windows Error Reporting\Disabled=1` | 低 |

#### 4.3.3 资源管理器与任务栏

| 编号 | 项 | 描述 | 实现方式 | 风险 |
|---|---|---|---|---|
| OP-T40 | 显示文件扩展名 | 显示已知类型扩展名 | `¬\Explorer\Advanced\HideFileExt` DWORD `0` | 低 |
| OP-T41 | 显示隐藏文件 | 显示隐藏/系统文件 | `Hidden=1` + `ShowSuperHidden=1` | 低 |
| OP-T42 | 打开资源管理器显示此电脑 | 默认打开位置 | `¬\Explorer\Advanced\LaunchTo` DWORD `1` | 低 |
| OP-T43 | 隐藏快捷方式小箭头 | 去箭头覆盖图标 | `Shell Icons\29=%systemroot%\Blank.ico`（附带 Blank.ico） | 低 |
| OP-T44 | 隐藏 UAC 小盾牌 | 去盾牌覆盖图标 | `Shell Icons\77=%systemroot%\Blank.ico` | 低 |
| OP-T45 | 禁用 Win11 新右键菜单 | 恢复经典右键 | 建 `HKCU\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32` 默认值空 | 低 |
| OP-T46 | 快速访问不显示常用/最近 | 精简快速访问 | `¬\Explorer\ShowFrequent=0`、`ShowRecent=0` | 低 |
| OP-T47 | 收起功能区/始终显示菜单栏 | 资源管理器外观 | `Ribbon\MinimizedStateTabletModeOff=1`、`AlwaysShowMenus=1` | 低 |
| OP-T48 | 禁止自动播放 | 关闭 AutoPlay | `¬\Explorer\AutoplayHandlers\DisableAutoplay` DWORD `1` | 低 |
| OP-T49 | 任务栏：Cortana 显示方式 | 搜索框/图标/隐藏 | `¬\Search\SearchboxTaskbarMode` DWORD `0/1/2` | 低 |
| OP-T50 | 隐藏任务视图按钮 | 去 Task View | `¬\Explorer\Advanced\ShowTaskViewButton` DWORD `0` | 低 |
| OP-T51 | 始终显示所有托盘图标 | 展开通知区 | `¬\Explorer\EnableAutoTray` DWORD `0` | 低 |
| OP-T52 | 任务栏时钟精确到秒 | 显示秒 | `¬\Explorer\Advanced\ShowSecondsInSystemClock` DWORD `1` | 低 |
| OP-T53 | 隐藏任务栏人脉 | 去 People | `Advanced\People\PeopleBand=0` + 策略 `HidePeopleBar=1` | 低 |
| OP-T54 | 隐藏快捷方式/此电脑六文件夹等 | 桌面与导航精简 | `HideDesktopIcons`/`FolderDescriptions\ThisPCPolicy=Hide` 等 | 低 |

#### 4.3.4 安全

| 编号 | 项 | 描述 | 实现方式 | 风险 |
|---|---|---|---|---|
| OP-T60 | UAC 级别 | 从不/仅通知/默认/始终 | `HKLM\…\Policies\System\`：`PromptOnSecureDesktop`、`ConsentPromptBehaviorAdmin`、`EnableLUA`（见下） | 高 |
| OP-T61 | 内置管理员批准模式 | 内置管理员走批准 | `…\Policies\System\FilterAdministratorToken` DWORD `1` | 中 |
| OP-T62 | 关闭 SmartScreen | 应用筛选 + Edge 钓鱼筛选 | `¬\Explorer\SmartScreenEnabled` SZ `off`；可选 `MicrosoftEdge\PhishingFilter\EnabledV9=0` | 高 |
| OP-T63 | 关闭“安全警告” | 打开文件不提示 | `Policies\Associations\ModRiskFileTypes` SZ 列表 | 高 |
| OP-T64 | 禁用 Windows Defender | 关闭实时保护 | `…\Windows Defender\DisableAntiSpyware=1` + `Real-Time Protection\DisableRealtimeMonitoring=1` | 高 |

> OP-T60 取值：从不通知=`PromptOnSecureDesktop 0 / ConsentPromptBehaviorAdmin 0 / EnableLUA 1`；仅通知=`0 / 5 / 1`；默认=`1 / 5 / 1`；始终=`1 / 2 / 1`。

#### 4.3.5 网络 / Windows Update

| 编号 | 项 | 描述 | 实现方式 | 风险 |
|---|---|---|---|---|
| OP-T70 | 关闭默认共享 | 禁用管理共享 | `LanmanServer\Parameters\AutoShareServer=0`、`AutoShareWks=0` | 中 |
| OP-T71 | 关闭防火墙 | 关闭三档防火墙 | `…\FirewallPolicy\{Standard/Public/Domain}Profile\EnableFirewall=0` | 高 |
| OP-T72 | 关闭远程协助 | 禁用远程协助 | `…\Control\Remote Assistance\fAllowToGetHelp=0` | 中 |
| OP-T73 | 禁用 SMB1 | 关闭旧协议 | `LanmanServer\Parameters\SMB1=0` | 低 |
| OP-T74 | 不在有用户登录时自动重启 | 更新后免扰 | `…\WindowsUpdate\AU\NoAutoRebootWithLoggedOnUsers=1` | 低 |
| OP-T75 | 更新不含驱动 | 排除驱动更新 | `…\WindowsUpdate\ExcludeWUDriversInQualityUpdate=1` | 低 |
| OP-T76 | 更新不含 MSRT | 排除恶意软件删除工具 | `HKLM\SOFTWARE\Policies\Microsoft\MRT\DontOfferThroughWUAU=1` | 低 |
| OP-T77 | 自动更新策略 | 从不/检查/下载/自动 | `WindowsUpdate\AU\AUOptions` DWORD `1..4`（+`NoAutoUpdate`） | 中 |

#### 4.3.6 其他（性能/日志/兼容）

| 编号 | 项 | 描述 | 实现方式 | 风险 |
|---|---|---|---|---|
| OP-T80 | 隐藏 GWX 升级助手 | 禁用升级推广 | `…\Policies\Microsoft\Windows\Gwx\DisableGwx=1` | 低 |
| OP-T81 | 禁用 WfpDiag ETL 日志 | WFP 诊断日志 | `NETSH WFP SET OPTIONS NETEVENTS=OFF` + `BFE\…\CollectNetEvents=0` | 低 |
| OP-T82 | 登录界面默认小键盘 | 默认 NumLock | `HKEY_USERS\.DEFAULT\Control Panel\Keyboard\InitialKeyboardIndicators` SZ `2` | 低 |
| OP-T83 | Windows To Go 标记 | 允许大版本更新 | `HKLM\SYSTEM\CurrentControlSet\Control\PortableOperatingSystem=1` | 中 |
| OP-T84 | 禁用 OneDrive 同步 | 关闭同步引擎 | `…\Policies\Microsoft\Windows\OneDrive\DisableFileSyncNGSC=1` | 中 |
| OP-T85 | 微软拼音默认英文/关闭云候选 | 输入法行为 | `HKCU\…\InputMethod\Settings\CHS\Default Mode=1`、`Enable Cloud Candidate=0` | 低 |
| OP-T86 | Windows 照片查看器 | 重新关联照片查看器 | `…\Windows Photo Viewer\Capabilities\FileAssociations` 各扩展名 | 低 |

### 4.4 服务优化（`OP-S`，`SetServiceStart` 等价）

实现方式：离线改 `HKLM\OFFLINE_SY\…\Services\<Name>\Start` DWORD（`2`=自动,`3`=手动,`4`=禁用,`5`=延迟自动）；或在线 `sc config`。

| 编号 | 服务 | 启/禁 | 描述 | 风险 |
|---|---|---|---|---|
| OP-S01 | PcaSvc | 禁(4)/自动(2) | 程序兼容性助手 | 低 |
| OP-S02 | RemoteRegistry | 禁(4)/自动(2) | 远程注册表 | 低 |
| OP-S03 | DPS | 禁(4)/自动(2) | 诊断策略服务 | 低 |
| OP-S04 | iphlpsvc | 禁(4)/自动(2) | IP 转换（禁用会破坏 IPv6） | 中 |
| OP-S05 | WSearch | 禁(4)/延迟(5) | Windows 搜索（禁用影响搜索/部分商店应用） | 中 |
| OP-S06 | WerSvc | 禁(4)/手动(3) | 错误报告 | 低 |
| OP-S07 | HomeGroupProvider | 禁(4)/手动(3) | 家庭组 | 低 |
| OP-S08 | TrkWks | 手动(3)/自动(2) | NTFS 链接跟踪（禁用可能影响性能） | 中 |
| OP-S09 | DiagTrack | 禁(4)/自动(2) | 连接用户体验与遥测 | 中 |
| OP-S10 | SysMain(Superfetch) | 禁(4)/自动(2) | 预读取（SSD 可禁） | 中 |

### 4.5 CompactOS / 合并（`OP-K`）

| 编号 | 项 | 描述 | 实现方式 | 风险 |
|---|---|---|---|---|
| OP-K01 | CompactOS 压缩 | 对只读系统文件做压缩以省空间 | 在线 `compact /compactos:always`；离线在映像捕获/应用阶段以对应压缩实现，或标注离线不可用 | 中 |
| OP-K02 | 硬链接合并 | 合并重复硬链接文件 | 专用例程 | 中 |

> 离线映像上 CompactOS 受限：Dism++ 的 `DismCompactOs` 主要面向在线系统；本工具对离线映像默认不启用 OP-K01，或在“最终系统首次启动脚本”中执行。

### 4.6 优化预设

| 预设 | 包含 | 说明 |
|---|---|---|
| 安全/推荐 | OP-C01/C02/C04~C07/C14~C21；OP-T01/T02/T05~T11/T20/T21/T40/T41/T49/T50/T74/T75/T76 | 无副作用或可逆 |
| 精简优化 | 推荐 + OP-C03/C09/C10/C11/C22~C24；OP-T03/T04/T22/T23/T24/T25/T42~T48/T51~T54/T80~T82/T85/T86 | 部分不可逆 |
| 激进 | 精简 + OP-S01~S10；OP-T60/T62/T63/T70/T72/T77 | 高风险，默认需显式确认 |

---

## 5. 通用实现机制

### 5.1 挂载与提交

```
DISM /Mount-Image /ImageFile:<install.wim> /Index:<n> /MountDir:$Mount
:: …… FR4 / FR5 操作 ……
DISM /Unmount-Image /MountDir:$Mount /Commit
```

### 5.2 命令执行与进度

所有外部命令经 `ProcessRunner` 执行，解析 DISM 百分比、wimlib `--progress`、oscdimg 输出行，映射为 FR9 的阶段进度；**关键输出**（工具启动/结束、返回码、警告与错误、产出文件与大小）进入日志，**进度类行（百分比/进度条/速度）只显示不落盘**（见 `日志规范.md` §5.2）。

### 5.3 危险项确认与回滚

- `风险=高`（如 OP-T60/T62/T63/T64、SL-P01/P02）默认关闭，勾选时二次确认并记录审计日志。
- 可逆项（注册表值改名类）保存原值以支持撤销；不可逆项（删包/删文件）明确标注且不提供撤销。
- 精简/优化后统一执行结果校验（`DISM /Get-Packages`、`/Cleanup-Image /ScanHealth`）。

---

## 6. 映射与可扩展性

### 6.1 `ConvertConfig.ini` 映射

FR3 各选项与 `[convert-UUP]` / `[Store_Apps]` / `[create_virtual_editions]` 键一一对应（见 §2.1 与 §2.4 的“uup”列），支持“导出为 ConvertConfig.ini”与“从 ConvertConfig.ini 导入”。

### 6.2 规则库外置

- FR4 精简清单（Appx/包/能力）与 FR5 优化规则库外置为 JSON，字段对齐 Dism++ `Data.xml` 语义（`General RootPath` / `RegWrite` / `RegDelete` / `RegMove` / `SetServiceStart` / `RunCommand`），支持在线更新规则。
- 规则项字段：`id`、`name`、`description`、`warning`、`level`(风险)、`applicable`(OSVersion/ImageFlags/RegExist/FileExist)、`actions[]`。
- 交付文件：
  - `docs/规范/rules/rules.schema.json`：JSON Schema（draft-07），定义规则与动作（`regWrite`/`regDelete`/`regMove`/`setServiceStart`/`removeProvisionedAppx`/`removePackage`/`removeCapability`/`deletePath`/`runCommand`/`procName`）。构建时复制到程序目录 `rules/`。
  - `docs/规范/rules/fr4-rules.json`：FR4 精简规则示例（Appx/组件/能力/硬件）。
  - `docs/规范/rules/fr5-rules.json`：FR5 系统优化规则示例（空间回收/tweak/服务/CompactOS）。
  - 动作语义：`hive` 使用逻辑名（SOFTWARE/SYSTEM/DEFAULT/NTUSER/DRIVERS）由运行时映射到离线 hive；`runCommand.mode` 取 `offline`/`online`/`firstBoot`；`procName` 映射本工具内置例程（如 `DismComponentCleanup`/`DismCompactOs`）。

---

## 7. 验收标准

1. **FR3**：在已声明支持的固定构建样本上，各选项组合能产出与 `uup-converter-wimlib` 等价的 wim/esd/ISO；`ConvertConfig.ini` 可双向导入导出。未验证的虚拟版本、加密 ESD 和多分卷输入必须拒绝或标记为实验性。
2. **FR4**：安全精简预设产物可在物理机与虚拟机完成安装并进入桌面；依赖冲突组合被阻断或提示；`ScanHealth` 通过。
3. **FR5**：推荐离线优化预设产物安装后，休眠关闭、遥测相关项生效（离线注册表可验证）；空间回收后 WinSxS 体积下降且系统可正常启动与更新；在线专属规则不计入离线验收；高风险项默认关闭且启用被记录。
4. **风险项**：所有 `高` 风险项在 GUI/CLI 均需显式确认，并在日志中留痕。
5. **可扩展**：FR4/FR5 规则库可外置更新而无需改动程序；新增项遵循 §6.2 字段约定。
