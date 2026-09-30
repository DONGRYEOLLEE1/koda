#!/usr/bin/env bash
# Install the comparison harnesses (opencode, hermes) under $BENCH_TOOLS_DIR and configure them for DeepSeek.
# Idempotent. Every step runs in a subshell whose HOME/XDG_* point at $BENCH_HOME/<tool>, so the real home
# directory (~/.config, ~/.hermes, shell rc files, ...) is never touched.
#
#   BENCH_STATE_DIR   default $HOME/.cache/koda-bench  (BENCH_TOOLS_DIR=$BENCH_STATE_DIR/tools, BENCH_HOME=$BENCH_STATE_DIR/home)
#   OPENCODE_VERSION  default 1.18.33  (version used for the published numbers)
#   HERMES_COMMIT     default f42f579cf8bac4918ac9599bece71618afadd846  (hermes-agent main, release 2026.9.24)
#
# Requirements: node/npm (opencode), curl + bash (hermes installer), uv.
set -euo pipefail
BT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH_STATE_DIR="${BENCH_STATE_DIR:-$HOME/.cache/koda-bench}"
export BENCH_TOOLS_DIR="${BENCH_TOOLS_DIR:-$BENCH_STATE_DIR/tools}"
export BENCH_HOME="${BENCH_HOME:-$BENCH_STATE_DIR/home}"
OPENCODE_VERSION="${OPENCODE_VERSION:-1.18.33}"
HERMES_COMMIT="${HERMES_COMMIT:-f42f579cf8bac4918ac9599bece71618afadd846}"
mkdir -p "$BENCH_TOOLS_DIR" "$BENCH_HOME"

echo "== opencode (npm opencode-ai@$OPENCODE_VERSION -> $BENCH_TOOLS_DIR/opencode)"
(
  . "$BT/isolate.sh" opencode
  # npm postinstall runs `opencode --version`, which creates $HOME/.config|.local/share|.cache|.local/state/opencode
  # -> the isolated HOME is required here.
  if [ "$("$BENCH_TOOLS_DIR/opencode/node_modules/.bin/opencode" --version 2>/dev/null || true)" != "$OPENCODE_VERSION" ]; then
    npm install --prefix "$BENCH_TOOLS_DIR/opencode" --no-fund --no-audit "opencode-ai@$OPENCODE_VERSION"
  fi
  mkdir -p "$XDG_CONFIG_HOME/opencode"
  # Global (isolated) defaults. run_opencode.sh overlays baseURL per run via OPENCODE_CONFIG. Key by env reference only.
  cat > "$XDG_CONFIG_HOME/opencode/opencode.json" <<'EOF'
{
  "$schema": "https://opencode.ai/config.json",
  "model": "deepseek/deepseek-flash",
  "small_model": "deepseek/deepseek-flash",
  "autoupdate": false,
  "share": "disabled",
  "provider": {
    "deepseek": {
      "options": {
        "apiKey": "{env:DEEPSEEK_API_KEY}"
      }
    }
  }
}
EOF
  "$BENCH_TOOLS_DIR/opencode/node_modules/.bin/opencode" --version
)

echo "== hermes (official install.sh --commit $HERMES_COMMIT -> $BENCH_TOOLS_DIR/hermes/hermes-agent)"
(
  . "$BT/isolate.sh" hermes
  mkdir -p "$BENCH_TOOLS_DIR/src" "$BENCH_TOOLS_DIR/hermes"
  if [ ! -x "$HOME/.local/bin/hermes" ]; then
    # Download first (read it before running it), then run with documented flags.
    curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o "$BENCH_TOOLS_DIR/src/hermes-install.sh"
    # --non-interactive: skip setup/gateway wizards. --skip-browser/--skip-computer-use: large components a
    # coding benchmark does not need.
    bash "$BENCH_TOOLS_DIR/src/hermes-install.sh" --non-interactive --skip-browser --skip-computer-use \
      --dir "$BENCH_TOOLS_DIR/hermes/hermes-agent" --commit "$HERMES_COMMIT"
  fi
  H="$HOME/.local/bin/hermes"
  # Documented "Custom & Self-Hosted LLM Providers" config. run_hermes.sh defaults to the built-in `deepseek`
  # provider (--provider deepseek + DEEPSEEK_BASE_URL); the custom entry below is kept for HERMES_BENCH_PROVIDER=custom.
  "$H" config set model.provider custom
  "$H" config set model.default deepseek-flash
  "$H" config set model.base_url '${HERMES_BENCH_BASE_URL}'
  "$H" config set model.key_env DEEPSEEK_API_KEY
  "$H" config set agent.reasoning_effort high --force
  "$H" --version | head -2
)
echo "done. runners: $BT/run_koda.sh | run_opencode.sh | run_hermes.sh <workspace_dir> <prompt_file> <out_dir>"
