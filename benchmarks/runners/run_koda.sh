#!/usr/bin/env bash
# One non-interactive koda run.  usage: run_koda.sh <workspace_dir> <prompt_file> <out_dir>
# Artifacts: <out_dir>/{stdout.txt, stderr.txt, meta.json, exec.json, proxy.jsonl, koda_result.json, workspace_changes.json}
#
# Same path as the documented headless usage (`koda run "<task>" --workspace <dir> --yolo --no-mcp`), except:
#  - koda.cli.main is wrapped by koda_entry.py to record run_task's result dict and per-call usage
#    (no koda source change).
#  - cwd = workspace and every setting comes from environment variables, so the koda repo's own .env
#    (e.g. a local-LLM PRIMARY, Langfuse keys) is not read and no tracing happens.
#  - HOME/XDG_* are isolated ($BENCH_HOME/koda), so ~/.koda/.env and ~/.koda/mcps are not read.
#  - koda is started with its project venv's interpreter directly (like an installed console script), not via
#    `uv run`: `uv run` activates koda's venv, which put koda's own Python 3.14 (and the koda repo path, which
#    also holds the hidden tests) at the front of PATH for koda's read-only shell, ahead of the workspace
#    .venv the other tools see first. run.py syncs the venv once per invocation (same sync `uv run` does).
set -uo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
bench_init koda "$@"

KV="$(sed -n 's/^version = "\(.*\)"/\1/p' "$KODA_DIR/pyproject.toml" | head -1)"
KB="$(git -C "$KODA_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)"
KC="$(git -C "$KODA_DIR" rev-parse --short HEAD 2>/dev/null)"
git -C "$KODA_DIR" diff --quiet HEAD -- src 2>/dev/null || KC="$KC+dirty"
BENCH_VERSION="koda $KV ($KB@$KC)"
KODA_PY="${KODA_PYTHON:-$KODA_DIR/.venv/bin/python}"
[ -x "$KODA_PY" ] || { echo "koda venv interpreter not found: $KODA_PY (run: uv sync --project $KODA_DIR)" >&2; exit 69; }

proxy_start
# Playwright looks for its browsers under the real user's cache; resolved before HOME is isolated below, so
# koda's render_page (visual tasks) finds the chromium the user installed and never downloads into the bench home.
if [ -z "${PLAYWRIGHT_BROWSERS_PATH:-}" ]; then
  if [ "$(uname)" = Darwin ]; then
    PLAYWRIGHT_BROWSERS_PATH="$HOME/Library/Caches/ms-playwright"
  else
    PLAYWRIGHT_BROWSERS_PATH="${XDG_CACHE_HOME:-$HOME/.cache}/ms-playwright"
  fi
fi
export PLAYWRIGHT_BROWSERS_PATH
bench_isolate

export PRIMARY=openai MODEL="$BENCH_MODEL" OPENAI_BASE_URL="$BENCH_BASE_URL" REASONING_EFFORT="$BENCH_EFFORT"
export LANGFUSE_HOST= LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY=
export KODA_BENCH_RESULT="$OUT/koda_result.json"   # read and removed from os.environ by koda_entry.py
unset OPENAI_API_KEY  # use the DeepSeek key only (koda prefers DEEPSEEK_API_KEY for deepseek-* models)
# VISION_* (from the environment or the env file, see load_key.sh) select the model koda's parse_screen and
# analyze_image use; empty means the main model, which for DeepSeek rejects images.
export VISION_MODEL VISION_BASE_URL VISION_API_KEY VISION_STRUCTURED_METHOD
BENCH_TOOL_ENV="PRIMARY MODEL OPENAI_BASE_URL REASONING_EFFORT LANGFUSE_HOST LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY KODA_BENCH_RESULT \
VISION_MODEL VISION_BASE_URL VISION_API_KEY VISION_STRUCTURED_METHOD"
"$BENCH_PY" -c 'import json, sys; print(json.dumps({"vision_model": sys.argv[1] or None, "vision_base_url": sys.argv[2] or None, "vision_structured_method": sys.argv[3] or None, "playwright_browsers_path": sys.argv[4]}))' \
  "$VISION_MODEL" "$VISION_BASE_URL" "$VISION_STRUCTURED_METHOD" "$PLAYWRIGHT_BROWSERS_PATH" > "$OUT/.koda_extra.json"

bench_run "$KODA_PY" "$BT/koda_entry.py" run "$PROMPT" --workspace "$WS" --yolo --no-mcp

bench_finish --extra "$OUT/.koda_extra.json"
