#!/bin/sh
# Scenario Forge Bridge installer (Ubuntu / Linux): uv, the Bridge CLI, then the Simulator Runner for the CARLA
# version picked in the menu. Run again to update.
#
#   curl -LsSf https://<site>/bridge/install.sh | sh                     # menu
#   curl -LsSf https://<site>/bridge/install.sh | SF_CARLA=0.9.16 sh     # no menu (0.9.16 | 0.9.15 | auto)
#
# SF_CARLA_ROOT=<CARLA folder> when CARLA is not in a usual place (~/CARLA_*, ~/Downloads/CARLA_*, /opt/carla*).
set -eu

SOURCE="${SF_BRIDGE_SOURCE:-git+https://github.com/vminhquan/demo_fullstack_build-phase.git#subdirectory=bridge}"

say() { printf '%s\n' "$*"; }

if ! command -v git >/dev/null 2>&1; then
  say "Cần Git để tải Scenario Forge Bridge: sudo apt install -y git"
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  say "Cài uv (trình quản lý Python, không cần quyền quản trị)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

say "Cài Scenario Forge Bridge…"
uv tool install --force --quiet "$SOURCE"
BIN="$(uv tool dir --bin 2>/dev/null || printf '%s' "$HOME/.local/bin")/scenario-forge-bridge"
"$BIN" --version

set --
[ -n "${SF_CARLA:-}" ] && set -- "$@" --carla "$SF_CARLA"
[ -n "${SF_CARLA_ROOT:-}" ] && set -- "$@" --carla-root "$SF_CARLA_ROOT"

# `curl | sh` feeds this script on stdin: the version menu reads the keyboard from the terminal instead.
if [ -z "${SF_CARLA:-}" ] && (: </dev/tty) 2>/dev/null; then
  "$BIN" setup-runner "$@" </dev/tty || say "Chưa chuẩn bị xong môi trường chạy test: chạy lại  scenario-forge-bridge setup-runner"
else
  "$BIN" setup-runner "$@" || say "Chưa chuẩn bị xong môi trường chạy test: chạy lại  scenario-forge-bridge setup-runner"
fi

uv tool update-shell >/dev/null 2>&1 || true
case ":$PATH:" in
  *":$(dirname "$BIN"):"*) ;;
  *) say "Mở terminal mới để dùng lệnh scenario-forge-bridge." ;;
esac
