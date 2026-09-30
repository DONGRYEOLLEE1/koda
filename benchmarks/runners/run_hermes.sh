#!/usr/bin/env bash
# One non-interactive hermes run.  usage: run_hermes.sh <workspace_dir> <prompt_file> <out_dir>
#   hermes -z "<prompt>" --usage-file <out>/hermes_usage.json --provider <p> -m <model> --reasoning <effort> --yolo --in <ws>
#  - -z/--oneshot prints only the final answer and bypasses approvals (--yolo is passed explicitly as well).
#  - hermes' learning loop (memories/skills) accumulates in HERMES_HOME -> the before/after diff is written to
#    state_drift.json (run.py restores a baseline when drift is detected, so runs stay independent).
#
# Proxy fairness fix (found during validation):
#  With base_url on 127.0.0.1 hermes treats the endpoint as a *local server* (agent/model_metadata.py
#  is_local_endpoint): it sends server-type probe GETs, uses an unbounded stale timeout and local context
#  detection. With provider=custom on a non-DeepSeek host it also cannot attach its DeepSeek profile, so the
#  title call sends response_format and gets a 400 + retry. Neither happens against api.deepseek.com.
#  - HERMES_BENCH_PROVIDER=deepseek (default): the built-in provider, as in DeepSeek's official Hermes guide;
#    the base URL is overridden with DEEPSEEK_BASE_URL. =custom reproduces the original custom-endpoint setup.
#  - BENCH_PROXY_HOSTNAME (default ds-proxy.localtest.me, a public DNS name resolving to 127.0.0.1): the proxy
#    address is handed to hermes under a non-loopback host name. Empty = keep 127.0.0.1.
set -uo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
bench_init hermes "$@"

HA="$BENCH_TOOLS_DIR/hermes/hermes-agent"
proxy_start
bench_isolate
HB="$HOME/.local/bin/hermes"
[ -x "$HB" ] || { echo "hermes not installed: $HB (run setup_competitors.sh)" >&2; exit 69; }
HH="${HERMES_HOME:-$HOME/.hermes}"
HV="$(sed -n "s/^__release_date__ = \"\(.*\)\"/\1/p" "$HA/hermes_cli/__init__.py" | head -1)"
BENCH_VERSION="hermes-agent release $HV ($(git -C "$HA" rev-parse --abbrev-ref HEAD 2>/dev/null)@$(git -C "$HA" rev-parse --short HEAD 2>/dev/null))"

HERMES_BENCH_PROVIDER="${HERMES_BENCH_PROVIDER:-deepseek}"
BENCH_PROXY_HOSTNAME="${BENCH_PROXY_HOSTNAME-ds-proxy.localtest.me}"
HBASE="$BENCH_BASE_URL"
if [ "$BENCH_PROXY" = "1" ] && [ -n "$BENCH_PROXY_HOSTNAME" ]; then
  HBASE="${BENCH_BASE_URL/127.0.0.1/$BENCH_PROXY_HOSTNAME}"
fi
export HERMES_BENCH_BASE_URL="$HBASE/v1"
[ "$HERMES_BENCH_PROVIDER" = "deepseek" ] && export DEEPSEEK_BASE_URL="$HBASE/v1"
BENCH_VERSION="$BENCH_VERSION [provider=$HERMES_BENCH_PROVIDER]"
printf '{"hermes_provider": "%s", "hermes_base_url": "%s/v1"}\n' "$HERMES_BENCH_PROVIDER" "$HBASE" > "$OUT/.hermes_mode.json"
BENCH_TOOL_ENV="HERMES_BENCH_BASE_URL DEEPSEEK_BASE_URL HERMES_HOME"

snap "$HH/memories" "$OUT/.mem_before.json"; snap "$HH/skills" "$OUT/.skills_before.json"
bench_run "$HB" -z "$PROMPT" --usage-file "$OUT/hermes_usage.json" \
  --provider "$HERMES_BENCH_PROVIDER" -m "$BENCH_MODEL" --reasoning "$BENCH_EFFORT" --yolo --in "$WS"
snap "$HH/memories" "$OUT/.mem_after.json"; snap "$HH/skills" "$OUT/.skills_after.json"
snapdiff "$OUT/.mem_before.json" "$OUT/.mem_after.json" "$OUT/.mem_diff.json"
snapdiff "$OUT/.skills_before.json" "$OUT/.skills_after.json" "$OUT/.skills_diff.json"
"$BENCH_PY" - "$OUT" <<'PY'
import json, sys, os
o = sys.argv[1]
d = {k: json.load(open(os.path.join(o, f".{k}_diff.json"))) for k in ("mem", "skills")}
json.dump({"hermes_memories": d["mem"], "hermes_skills": d["skills"]}, open(os.path.join(o, "state_drift.json"), "w"), ensure_ascii=False, indent=2)
PY

bench_finish --hermes-db "$HH/state.db" --extra "$OUT/.hermes_mode.json"
