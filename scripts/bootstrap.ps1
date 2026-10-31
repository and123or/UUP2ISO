#Requires -Version 5.1
<#
.SYNOPSIS
    初始化工作空间虚拟环境 .venv 并安装依赖（项目计划 WP0-T1 / WP0-T2 / WP0-T2a）。

.DESCRIPTION
    干净机器克隆后，一条命令得到可复现的开发环境。所有步骤都在工作空间 .venv 内完成，
    绝不向系统 Python 主环境安装任何包。

    步骤：选择解释器 -> 创建 .venv -> 升级 pip -> pip install -e ".[dev]"
          -> 环境自检 -> lint / 类型检查 / 测试 -> 生成依赖快照

.PARAMETER PythonExe
    指定基础解释器（默认自动探测：python -> py -3.13 -> py -3.12）。

.PARAMETER Recreate
    删除并重建 .venv（仅影响工作空间内的 .venv 目录）。

.PARAMETER SkipTests
    跳过 lint / mypy / pytest（只装环境，CI 会单独跑检查）。

.PARAMETER NoUpgrade
    跳过 pip / setuptools / wheel 的联网升级（离线或网络受限时使用；
    已就绪的环境重跑 bootstrap 通常加此开关，避免等待 PyPI）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Recreate

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -NoUpgrade -SkipTests
#>
[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [switch]$Recreate,
    [switch]$SkipTests,
    [switch]$NoUpgrade
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $Root ".venv"
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
$Supported = @("3.12", "3.13")

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令失败 (exit=$LASTEXITCODE): $Exe $($Arguments -join ' ')"
    }
}

function Get-PythonShortVersion {
    param([string]$Exe, [string[]]$PrefixArgs)
    try {
        $out = & $Exe @PrefixArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return ($out | Select-Object -First 1).Trim()
    }
    catch { return $null }
}

# --- 1. 选择基础解释器 -------------------------------------------------------
Write-Step "选择基础解释器"

$ReuseVenv = $false

# 优先复用已存在的 .venv：重跑时不必再找基础解释器
# （覆盖「只装了 3.14、但 .venv 已就绪」的场景，避免误报失败）
if (-not $Recreate -and -not $PythonExe -and (Test-Path $VenvPy)) {
    $existingVer = Get-PythonShortVersion -Exe $VenvPy -PrefixArgs @()
    if ($existingVer -and ($Supported -contains $existingVer)) {
        Write-Host "    复用现有 .venv（Python $existingVer），跳过基础解释器探测" -ForegroundColor DarkGray
        $ReuseVenv = $true
    }
}

$BaseExe = $null
$BaseArgs = @()

if (-not $ReuseVenv) {
    $candidates = @()
    if ($PythonExe) { $candidates += , @($PythonExe, @()) }
    # py 启动器优先（可精确指定次版本）
    $candidates += , @("py", @("-3.13"))
    $candidates += , @("py", @("-3.12"))
    $candidates += , @("python", @())
    $candidates += , @("python3", @())
    # 未加入 PATH 的典型安装位置
    foreach ($minor in @("313", "312")) {
        $candidates += , @("$env:LOCALAPPDATA\Programs\Python\Python$minor\python.exe", @())
        $candidates += , @("C:\Python$minor\python.exe", @())
        $candidates += , @("C:\Program Files\Python$minor\python.exe", @())
    }

    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $extraArgs = $candidate[1]
        if ($exe -like "*\*") {
            if (-not (Test-Path $exe)) { continue }
        }
        elseif (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
            continue
        }
        $ver = Get-PythonShortVersion -Exe $exe -PrefixArgs $extraArgs
        if (-not $ver) { continue }
        if ($Supported -contains $ver) {
            $BaseExe = $exe
            $BaseArgs = $extraArgs
            Write-Host "    使用 $exe $($extraArgs -join ' ') -> Python $ver"
            break
        }
        Write-Host "    跳过 $exe $($extraArgs -join ' ') -> Python $ver（不在 3.12-3.13 范围内）" -ForegroundColor DarkYellow
    }

    if (-not $BaseExe) {
        throw "未找到 Python 3.12 或 3.13（3.14 无 PySide6 wheel，不能替代）。请安装 Python 3.13 x64 后重跑，或用 -PythonExe 指定 3.13 解释器绝对路径；若 .venv 已就绪，加 -NoUpgrade -SkipTests 重跑即可。"
    }
}

# --- 2. 创建 / 重建 .venv ----------------------------------------------------
Write-Step "准备工作空间虚拟环境 .venv"

if ($Recreate -and (Test-Path $VenvDir)) {
    Write-Host "    删除既有 .venv（-Recreate）"
    Remove-Item -LiteralPath $VenvDir -Recurse -Force
}

if (-not (Test-Path $VenvPy)) {
    Write-Host "    python -m venv .venv"
    Invoke-Checked -Exe $BaseExe -Arguments ($BaseArgs + @("-m", "venv", $VenvDir))
}
else {
    Write-Host "    .venv 已存在，复用"
}

# --- 3. 安装依赖 -------------------------------------------------------------
if (-not $NoUpgrade) {
    Write-Step "升级打包工具"
    Invoke-Checked -Exe $VenvPy -Arguments @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
}
else {
    Write-Step "跳过 pip/setuptools/wheel 联网升级（-NoUpgrade）"
}

Write-Step "安装项目与开发依赖（-e `".[dev]`"）"
Push-Location $Root
try {
    Invoke-Checked -Exe $VenvPy -Arguments @("-m", "pip", "install", "-e", ".[dev]")
}
finally {
    Pop-Location
}

# --- 4. 环境自检 -------------------------------------------------------------
Write-Step "环境一致性自检（scripts/env_check.py）"
Invoke-Checked -Exe $VenvPy -Arguments @((Join-Path $Root "scripts\env_check.py"))

# --- 5. 质量门禁 -------------------------------------------------------------
if (-not $SkipTests) {
    Write-Step "ruff check"
    Invoke-Checked -Exe $VenvPy -Arguments @("-m", "ruff", "check", ".")

    Write-Step "ruff format --check"
    Invoke-Checked -Exe $VenvPy -Arguments @("-m", "ruff", "format", "--check", ".")

    Write-Step "mypy"
    Invoke-Checked -Exe $VenvPy -Arguments @("-m", "mypy")

    Write-Step "pytest"
    Invoke-Checked -Exe $VenvPy -Arguments @("-m", "pytest")
}

# --- 6. 依赖快照（可复现性证据，非强制约束）--------------------------------
Write-Step "生成依赖快照 requirements.lock.txt"
$lockFile = Join-Path $Root "requirements.lock.txt"
$pyTag = (& $VenvPy -c "import sys; print('cp%d%d' % sys.version_info[:2])").Trim()
$header = @(
    "# 由 scripts/bootstrap.ps1 生成的依赖快照（仅供审计/复现参考，不参与解析）",
    "# 平台: win-x64  |  解释器: $pyTag  |  生成时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    ""
)
$frozen = & $VenvPy -m pip freeze
Set-Content -LiteralPath $lockFile -Value ($header + $frozen) -Encoding utf8
Write-Host "    已写入 $lockFile（$(@($frozen).Count) 个包）"

Write-Host ""
Write-Host "环境就绪。后续命令统一使用：" -ForegroundColor Green
Write-Host "  .venv\Scripts\python -m pytest"
Write-Host "  .venv\Scripts\python -m ruff check ."
Write-Host "  .venv\Scripts\python -m uup2iso version   # 等价: .venv\Scripts\uup2iso.exe version"
Write-Host "  .venv\Scripts\python scripts\env_check.py"
