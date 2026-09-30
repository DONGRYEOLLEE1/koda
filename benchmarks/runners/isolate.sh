# shellcheck shell=bash
# Source this: `. isolate.sh <tool>` -> HOME and XDG_* point at $BENCH_HOME/<tool>.
# Keeps every tool's config/state (and anything an installer writes to $HOME) out of the real home directory.
_BT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_BH="${BENCH_HOME:-${BENCH_STATE_DIR:-$HOME/.cache/koda-bench}/home}/${1:?tool name}"
mkdir -p "$_BH/.config" "$_BH/.local/share" "$_BH/.local/state" "$_BH/.cache"
export HOME="$_BH" XDG_CONFIG_HOME="$_BH/.config" XDG_DATA_HOME="$_BH/.local/share" \
       XDG_STATE_HOME="$_BH/.local/state" XDG_CACHE_HOME="$_BH/.cache"
export npm_config_cache="$_BH/.npm-cache"
