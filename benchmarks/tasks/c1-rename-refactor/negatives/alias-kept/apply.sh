#!/usr/bin/env bash
# Correct rename but a backward-compatible alias is left behind (must FAIL: old name remains).
set -euo pipefail
cd "$WS"
for f in $(grep -rlw --exclude-dir=.venv --exclude-dir=.git --include='*.py' --include='*.md' calc_total . || true); do
  perl -pi -e 's/\bcalc_total\b/compute_order_total/g' "$f"
done
printf '\n\ncalc_total = compute_order_total  # deprecated alias\n' >> shop/pricing.py
