# shellcheck shell=bash
# Source this. Exports DEEPSEEK_API_KEY and DEEPSEEK_MODEL without ever printing them.
#  - If DEEPSEEK_API_KEY is already in the environment it is used as-is.
#  - Otherwise the env file (BENCH_ENV_FILE, default $KODA_DIR/.env) is sourced in a subshell and only
#    these two variables are taken from it (nothing else from that file leaks into the run).
_KEY_FILE="${BENCH_ENV_FILE:-${KODA_DIR:-.}/.env}"
if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -f "$_KEY_FILE" ]; then
  DEEPSEEK_API_KEY="$(set +u -a; . "$_KEY_FILE" >/dev/null 2>&1; printf %s "${DEEPSEEK_API_KEY:-}")"
fi
if [ -z "${DEEPSEEK_MODEL:-}" ] && [ -f "$_KEY_FILE" ]; then
  DEEPSEEK_MODEL="$(set +u -a; . "$_KEY_FILE" >/dev/null 2>&1; printf %s "${DEEPSEEK_MODEL:-}")"
fi
export DEEPSEEK_API_KEY
export DEEPSEEK_MODEL="${DEEPSEEK_MODEL_OVERRIDE:-${DEEPSEEK_MODEL:-deepseek-flash}}"
if [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "DEEPSEEK_API_KEY not set (export it or put it in $_KEY_FILE)" >&2
  return 1 2>/dev/null || exit 1
fi
