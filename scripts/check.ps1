#Requires -Version 5.1
<#
.SYNOPSIS
    质量门禁：环境自检 + ruff + mypy + pytest（项目计划 §7 DoD）。

.DESCRIPTION
    全部命令在 .venv 内执行；不创建 .venv，请先运行 scripts/bootstrap.ps1。

.PARAMETER Fast
    只跑 ruff 与 pytest（跳过 mypy 与环境自检）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/check.ps1
#>
[CmdletBinding()]
param([switch]$Fast)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPy)) {
    throw "未找到 .venv，请先执行: powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1"
}

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "门禁失败 (exit=$LASTEXITCODE): $Exe $($Arguments -join ' ')"
    }
}

Push-Location $Root
try {
    if (-not $Fast) {
        Write-Step "环境自检"
        Invoke-Checked -Exe $VenvPy -Arguments @("scripts\env_check.py")
    }

    Write-Step "ruff check"
    Invoke-Checked -Exe $VenvPy -Arguments @("-m", "ruff", "check", ".")

    Write-Step "ruff format --check"
    Invoke-Checked -Exe $VenvPy -Arguments @("-m", "ruff", "format", "--check", ".")

    if (-not $Fast) {
        Write-Step "mypy"
        Invoke-Checked -Exe $VenvPy -Arguments @("-m", "mypy")
    }

    Write-Step "pytest"
    Invoke-Checked -Exe $VenvPy -Arguments @("-m", "pytest")

    Write-Host ""
    Write-Host "全部门禁通过。" -ForegroundColor Green
}
finally {
    Pop-Location
}
