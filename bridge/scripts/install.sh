#!/usr/bin/env bash
# Installs the built binary for the current user on Ubuntu: ~/.local/bin/scenario-forge-bridge
set -euo pipefail
SRC="${1:-$(dirname "$0")/../dist/scenario-forge-bridge}"
mkdir -p "$HOME/.local/bin"
install -m 0755 "$SRC" "$HOME/.local/bin/scenario-forge-bridge"
echo "Installed to $HOME/.local/bin/scenario-forge-bridge"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Add ~/.local/bin to PATH (e.g. in ~/.bashrc)";; esac
