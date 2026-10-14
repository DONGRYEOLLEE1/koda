# shellcheck shell=bash
# Source this. Exports DEEPSEEK_API_KEY and DEEPSEEK_MODEL without ever printing them, plus the VISION_*
# settings koda's screen tools read (VISION_MODEL, VISION_BASE_URL, VISION_API_KEY, VISION_STRUCTURED_METHOD).
#  - A variable already in the environment is used as-is.
#  - Otherwise the env file (BENCH_ENV_FILE, default $KODA_DIR/.env) is sourced in a subshell and only these
#    variables are taken from it (nothing else from that file leaks into the run).
#  - The VISION_* variables may stay empty: koda then uses the main model for images.
_KEY_FILE="${BENCH_ENV_FILE:-${KODA_DIR:-.}/.env}"
_bench_from_env_file() {  # _bench_from_env_file <name>: value of <name> in the env file, or empty
  (set +u -a; . "$_KEY_FILE" >/dev/null 2>&1; eval "printf %s \"\${$1:-}\"")
}
if [ -f "$_KEY_FILE" ]; then
  _v=""
  for _v in DEEPSEEK_API_KEY DEEPSEEK_MODEL VISION_MODEL VISION_BASE_URL VISION_API_KEY VISION_STRUCTURED_METHOD; do
    if [ -z "$(eval "printf %s \"\${$_v:-}\"")" ]; then
      eval "$_v=\"\$(_bench_from_env_file $_v)\""
    fi
  done
fi
export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
export DEEPSEEK_MODEL="${DEEPSEEK_MODEL_OVERRIDE:-${DEEPSEEK_MODEL:-deepseek-flash}}"
export VISION_MODEL="${VISION_MODEL:-}" VISION_BASE_URL="${VISION_BASE_URL:-}" \
       VISION_API_KEY="${VISION_API_KEY:-}" VISION_STRUCTURED_METHOD="${VISION_STRUCTURED_METHOD:-}"
if [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "DEEPSEEK_API_KEY not set (export it or put it in $_KEY_FILE)" >&2
  return 1 2>/dev/null || exit 1
fi
