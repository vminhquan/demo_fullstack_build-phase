# Scenario Forge Bridge installer (Windows PowerShell): uv, the Bridge CLI, then the Simulator Runner for the CARLA
# version picked in the menu. Run again to update.
#
#   irm https://<site>/bridge/install.ps1 | iex                               # menu
#   $env:SF_CARLA = "0.9.16"; irm https://<site>/bridge/install.ps1 | iex     # no menu (0.9.16 | 0.9.15 | auto)
#
# $env:SF_CARLA_ROOT = "<CARLA folder>" when CARLA is not in a usual place (C:\CARLA_*, ~\Downloads\CARLA_*).
$ErrorActionPreference = "Stop"

$Source = if ($env:SF_BRIDGE_SOURCE) { $env:SF_BRIDGE_SOURCE } else { "git+https://github.com/vminhquan/demo_fullstack_build-phase.git#subdirectory=bridge" }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Cần Git để tải Scenario Forge Bridge: winget install Git.Git (rồi mở PowerShell mới)"
    return
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Cài uv (trình quản lý Python, không cần quyền quản trị)…"
    powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
}
$env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:Path"

Write-Host "Cài Scenario Forge Bridge…"
uv tool install --force --quiet $Source
if ($LASTEXITCODE -ne 0) { Write-Host "Cài Scenario Forge Bridge thất bại."; return }
$Bin = Join-Path (uv tool dir --bin) "scenario-forge-bridge.exe"
& $Bin --version

$SetupArgs = @()
if ($env:SF_CARLA) { $SetupArgs += @("--carla", $env:SF_CARLA) }
if ($env:SF_CARLA_ROOT) { $SetupArgs += @("--carla-root", $env:SF_CARLA_ROOT) }
& $Bin setup-runner @SetupArgs
if ($LASTEXITCODE -ne 0) { Write-Host "Chưa chuẩn bị xong môi trường chạy test: chạy lại  scenario-forge-bridge setup-runner" }

uv tool update-shell *> $null
Write-Host "Mở PowerShell mới nếu lệnh scenario-forge-bridge chưa có."
