# shellcheck shell=bash
# Shared part of run_<tool>.sh. Source it, do not execute it. (bash 3.2 compatible for macOS)
#
# Paths (all optional; nothing machine-specific is hard-coded):
#   KODA_DIR          koda repo to benchmark. Default: two levels above this directory.
#   BENCH_STATE_DIR   Root for competitor installs and isolated homes. Default: $HOME/.cache/koda-bench
#   BENCH_TOOLS_DIR   Where opencode / hermes are installed (setup_competitors.sh). Default: $BENCH_STATE_DIR/tools
#   BENCH_HOME        Parent of the per-tool isolated HOME dirs. Default: $BENCH_STATE_DIR/home
#   BENCH_ENV_FILE    .env to read DEEPSEEK_API_KEY / DEEPSEEK_MODEL from when they are not already exported.
#                     Default: $KODA_DIR/.env
# Run knobs:
#   BENCH_PROXY=1             Route traffic through the metering proxy (default). 0 = direct to api.deepseek.com
#                             (tokens then come from each tool's own report only).
#   BENCH_EFFORT=high         reasoning effort requested from every tool (DeepSeek supports low/high/max).
#   BENCH_MODEL               Default: DEEPSEEK_MODEL from the env file, else deepseek-flash.
#   BENCH_TIMEOUT=300         Seconds. On expiry the whole process group is killed and meta.timed_out=true.
#   BENCH_CAPTURE_TOOL_ARGS=0 >0: the proxy also logs each tool call's name + arguments (truncated to N chars).
#   BENCH_WATCH_REGEX         Tool calls whose arguments match are "watched": the proxy logs the first 1500
#                             chars of their tool result when it is sent back to the model (safety tasks).
#   BENCH_TOOL_PATH_PREPEND   Prepended to PATH for the tool process only (e.g. the workspace .venv/bin).

BT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KODA_DIR="${KODA_DIR:-$(cd "$BT/../.." && pwd)}"
BENCH_STATE_DIR="${BENCH_STATE_DIR:-$HOME/.cache/koda-bench}"
BENCH_TOOLS_DIR="${BENCH_TOOLS_DIR:-$BENCH_STATE_DIR/tools}"
BENCH_HOME="${BENCH_HOME:-$BENCH_STATE_DIR/home}"
export KODA_DIR BENCH_TOOLS_DIR BENCH_HOME
BENCH_TIMEOUT="${BENCH_TIMEOUT:-300}"
BENCH_EFFORT="${BENCH_EFFORT:-high}"
BENCH_PROXY="${BENCH_PROXY:-1}"
BENCH_CAPTURE_TOOL_ARGS="${BENCH_CAPTURE_TOOL_ARGS:-0}"
BENCH_WATCH_REGEX="${BENCH_WATCH_REGEX:-}"
BENCH_TOOL_PATH_PREPEND="${BENCH_TOOL_PATH_PREPEND:-}"
DEEPSEEK_DIRECT_URL="https://api.deepseek.com"
BENCH_PROXY_PID=""

bench_usage() {
  echo "usage: $(basename "$0") <workspace_dir> <prompt_file> <out_dir>" >&2
  exit 64
}

bench_init() {  # bench_init <tool> <workspace_dir> <prompt_file> <out_dir>
  BENCH_TOOL="$1"; shift
  [ $# -eq 3 ] || bench_usage
  [ -d "$1" ] || { echo "workspace not found: $1" >&2; exit 66; }
  [ -f "$2" ] || { echo "prompt_file not found: $2" >&2; exit 66; }
  mkdir -p "$3" || exit 73
  WS="$(cd "$1" && pwd -P)"
  PROMPT_FILE="$(cd "$(dirname "$2")" && pwd -P)/$(basename "$2")"
  OUT="$(cd "$3" && pwd -P)"
  PROMPT="$(cat "$PROMPT_FILE")"
  [ -n "$PROMPT" ] || { echo "empty prompt: $PROMPT_FILE" >&2; exit 65; }
  # Resolve the real uv locations before HOME is isolated, so helpers / proxy / `uv run` reuse the caches.
  BENCH_PY="${BENCH_PY:-$(uv python find 2>/dev/null)}"
  [ -x "$BENCH_PY" ] || { echo "helper python not found (uv python find)" >&2; exit 69; }
  REAL_UV_CACHE_DIR="$(uv cache dir 2>/dev/null)"
  REAL_UV_PYTHON_DIR="$(uv python dir 2>/dev/null)"
  # Move previous artifacts aside (the proxy log is append-only and must not mix runs).
  local f
  for f in proxy.jsonl stdout.txt stderr.txt exec.json meta.json; do
    [ -e "$OUT/$f" ] && mv "$OUT/$f" "$OUT/$f.prev"
  done
  # macOS with the Xcode license not accepted: the /usr/bin/git and /usr/bin/python3 xcrun shims exit 69.
  # A tool whose shell puts /usr/bin first (login shells run path_helper, e.g. hermes) would pay extra tool
  # calls that the others never see. Point the shims at the Command Line Tools so every tool gets a working
  # /usr/bin/git (no-op when the shims already work or on Linux). Recorded as BENCH_DEVELOPER_DIR_FIX.
  BENCH_DEVELOPER_DIR_FIX=0
  if [ "$(uname)" = Darwin ] && [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Library/Developer/CommandLineTools ] \
     && ! /usr/bin/git --version >/dev/null 2>&1; then
    export DEVELOPER_DIR=/Library/Developer/CommandLineTools
    BENCH_DEVELOPER_DIR_FIX=1
  fi
  # shellcheck source=load_key.sh
  . "$BT/load_key.sh" || exit 78
  BENCH_MODEL="${BENCH_MODEL:-$DEEPSEEK_MODEL}"
  trap 'proxy_stop' EXIT
}

bench_isolate() {  # HOME/XDG_* -> $BENCH_HOME/<tool>
  # shellcheck source=isolate.sh
  . "$BT/isolate.sh" "$BENCH_TOOL"
}

proxy_start() {
  if [ "$BENCH_PROXY" = "1" ]; then
    local pf="$OUT/.proxy.port" i
    local -a extra=()
    [ "$BENCH_CAPTURE_TOOL_ARGS" != "0" ] && extra+=(--capture-tool-args "$BENCH_CAPTURE_TOOL_ARGS")
    [ -n "$BENCH_WATCH_REGEX" ] && extra+=(--watch-regex "$BENCH_WATCH_REGEX")
    : > "$pf"
    UV_CACHE_DIR="$REAL_UV_CACHE_DIR" UV_PYTHON_INSTALL_DIR="$REAL_UV_PYTHON_DIR" \
      uv run --quiet --script "$BT/usage_proxy.py" --log "$OUT/proxy.jsonl" --port-file "$pf" \
      --upstream "$DEEPSEEK_DIRECT_URL" ${extra[@]+"${extra[@]}"} > "$OUT/proxy.log" 2>&1 &
    BENCH_PROXY_PID=$!
    for i in $(seq 1 240); do
      [ -s "$pf" ] && break
      kill -0 "$BENCH_PROXY_PID" 2>/dev/null || break
      sleep 0.25
    done
    if [ -s "$pf" ]; then
      BENCH_BASE_URL="http://127.0.0.1:$(cat "$pf")"
    else
      echo "[bench] metering proxy failed to start -> direct connection (see proxy.log)" >&2
      BENCH_PROXY=0
      BENCH_BASE_URL="$DEEPSEEK_DIRECT_URL"
    fi
  else
    BENCH_BASE_URL="$DEEPSEEK_DIRECT_URL"
  fi
}

proxy_stop() {
  if [ -n "$BENCH_PROXY_PID" ]; then
    kill "$BENCH_PROXY_PID" 2>/dev/null
    pkill -f "usage_proxy.py --log $OUT/proxy.jsonl" 2>/dev/null
    wait "$BENCH_PROXY_PID" 2>/dev/null
    BENCH_PROXY_PID=""
  fi
}

snap() {  # snap <dir> <out.json>
  "$BENCH_PY" "$BT/ws_snapshot.py" snap "$1" "$2"
}

snapdiff() {  # snapdiff <before.json> <after.json> <out.json>
  "$BENCH_PY" "$BT/ws_snapshot.py" diff "$1" "$2" "$3"
}

# The tool process sees ONLY these variables (plus the per-tool list in BENCH_TOOL_ENV, set by run_<tool>.sh).
# Everything else from the caller's environment is dropped: the host session (tmux, SSH agent, the calling
# agent's own variables and tokens) and the benchmark's variables (KODA_DIR -> the repo holding hidden tests,
# BENCH_WATCH_REGEX, ...). Same allowlist for every tool; TERM is pinned.
BENCH_ENV_ALLOW_BASE="PATH HOME USER LOGNAME SHELL LANG LC_ALL LC_CTYPE TMPDIR __CF_USER_TEXT_ENCODING \
XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME XDG_CACHE_HOME npm_config_cache DEVELOPER_DIR \
GIT_CEILING_DIRECTORIES GIT_TERMINAL_PROMPT GIT_PAGER PAGER DEEPSEEK_API_KEY"

bench_run() {  # bench_run <cmd...>  - cwd=workspace, stdin=/dev/null, timeout + wall clock
  snap "$WS" "$OUT/.ws_before.json"
  printf '{"developer_dir_fix": %s, "tool_path_prepend": %s, "env_allowlist": true}\n' \
    "${BENCH_DEVELOPER_DIR_FIX:-0}" "$([ -n "$BENCH_TOOL_PATH_PREPEND" ] && echo true || echo false)" \
    > "$OUT/bench_env.json"
  local tool_path="$PATH"
  [ -n "$BENCH_TOOL_PATH_PREPEND" ] && tool_path="$BENCH_TOOL_PATH_PREPEND:$PATH"
  local allow
  allow="$(echo $BENCH_ENV_ALLOW_BASE ${BENCH_TOOL_ENV:-} | tr ' ' ',')"
  # GIT_CEILING_DIRECTORIES: git run inside the workspace can never climb above it into another repo.
  # GIT_TERMINAL_PROMPT=0 / pagers: avoid interactive hangs; applied identically to every tool.
  PATH="$tool_path" GIT_CEILING_DIRECTORIES="$(dirname "$WS")" GIT_TERMINAL_PROMPT=0 GIT_PAGER=cat PAGER=cat \
    "$BENCH_PY" "$BT/bench_exec.py" --timeout "$BENCH_TIMEOUT" --cwd "$WS" \
    --env-allow "$allow" --env-set TERM=xterm-256color \
    --stdout "$OUT/stdout.txt" --stderr "$OUT/stderr.txt" --result "$OUT/exec.json" -- "$@"
  snap "$WS" "$OUT/.ws_after.json"
  snapdiff "$OUT/.ws_before.json" "$OUT/.ws_after.json" "$OUT/workspace_changes.json"
}

bench_finish() {  # bench_finish [extra summarize.py args...]
  proxy_stop
  "$BENCH_PY" "$BT/summarize.py" --tool "$BENCH_TOOL" --out "$OUT" --workspace "$WS" \
    --prompt-file "$PROMPT_FILE" --version "${BENCH_VERSION:-}" --model "$BENCH_MODEL" \
    --effort "$BENCH_EFFORT" --base-url "$BENCH_BASE_URL" --proxy "$BENCH_PROXY" "$@"
  "$BENCH_PY" - "$OUT/meta.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
keys = ("tool", "exit_code", "timed_out", "wall_seconds", "input_tokens", "output_tokens", "tool_calls", "llm_calls")
print(json.dumps({k: m.get(k) for k in keys} | {"meta": sys.argv[1]}, ensure_ascii=False))
PY
  local rc
  rc="$("$BENCH_PY" -c '
import json, sys
m = json.load(open(sys.argv[1]))
rc = m.get("exit_code")
print(124 if m.get("timed_out") else (rc if isinstance(rc, int) and rc >= 0 else (128 - rc if isinstance(rc, int) else 1)))
' "$OUT/meta.json")"
  exit "$rc"
}
