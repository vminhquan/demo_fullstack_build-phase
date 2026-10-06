#!/usr/bin/env bash
# Builds a single-file Linux binary: dist/scenario-forge-bridge
# PyInstaller cannot cross-compile: run this on Ubuntu (build on the oldest Ubuntu you support, glibc is forward compatible).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv-build
. .venv-build/bin/activate
pip install --upgrade pip >/dev/null
pip install -e '.[dev]'
pyinstaller --onefile --clean --name scenario-forge-bridge \
  --collect-submodules keyring.backends --collect-submodules questionary \
  --add-data "scenario_forge_bridge/carla_catalog.py:scenario_forge_bridge" \
  pyinstaller_entry.py
echo "Built: $(pwd)/dist/scenario-forge-bridge"
