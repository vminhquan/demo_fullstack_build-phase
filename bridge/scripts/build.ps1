# Builds a single-file Windows executable: dist\scenario-forge-bridge.exe
# PyInstaller cannot cross-compile: run this on Windows (PowerShell).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
py -3 -m venv .venv-build
& .\.venv-build\Scripts\Activate.ps1
python -m pip install --upgrade pip | Out-Null
pip install -e ".[dev]"
pyinstaller --onefile --clean --name scenario-forge-bridge `
  --collect-submodules keyring.backends --collect-submodules questionary `
  --add-data "scenario_forge_bridge/carla_catalog.py;scenario_forge_bridge" `
  --add-data "scenario_forge_bridge/cut_in_sites.py;scenario_forge_bridge" `
  pyinstaller_entry.py
Write-Host "Built: $(Get-Location)\dist\scenario-forge-bridge.exe"
