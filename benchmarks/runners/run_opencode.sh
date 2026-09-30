#!/usr/bin/env bash
# One non-interactive opencode run.  usage: run_opencode.sh <workspace_dir> <prompt_file> <out_dir>
#   opencode run --format json --auto --variant <effort> -m deepseek/<model> --dir <ws> "<prompt>"
#  - Built-in `deepseek` provider (models.dev, @ai-sdk/openai-compatible). Only baseURL is set per run via
#    OPENCODE_CONFIG (proxy or https://api.deepseek.com). The key is referenced as {env:DEEPSEEK_API_KEY},
#    never written to a file.
#  - --auto: "auto-approve permission requests that are not explicitly denied".
#    stdin is /dev/null (with a non-TTY stdin opencode reads stdin until EOF and hangs).
set -uo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
bench_init opencode "$@"

OC="$BENCH_TOOLS_DIR/opencode/node_modules/.bin/opencode"
[ -x "$OC" ] || { echo "opencode not installed: $OC (run setup_competitors.sh)" >&2; exit 69; }

proxy_start
bench_isolate
export OPENCODE_DISABLE_AUTOUPDATE=1
BENCH_VERSION="opencode $("$OC" --version 2>/dev/null)"

cat > "$OUT/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "deepseek/$BENCH_MODEL",
  "small_model": "deepseek/$BENCH_MODEL",
  "autoupdate": false,
  "share": "disabled",
  "provider": {
    "deepseek": {
      "options": {
        "baseURL": "$BENCH_BASE_URL",
        "apiKey": "{env:DEEPSEEK_API_KEY}"
      }
    }
  }
}
EOF
export OPENCODE_CONFIG="$OUT/opencode.json"
BENCH_TOOL_ENV="OPENCODE_DISABLE_AUTOUPDATE OPENCODE_CONFIG"

bench_run "$OC" run --format json --auto --variant "$BENCH_EFFORT" -m "deepseek/$BENCH_MODEL" \
  --dir "$WS" "$PROMPT"

bench_finish
