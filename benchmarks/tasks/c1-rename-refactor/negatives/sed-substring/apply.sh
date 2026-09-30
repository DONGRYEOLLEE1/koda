#!/usr/bin/env bash
# Careless rename: plain substring replace also renames calc_total_tax (must FAIL).
set -euo pipefail
cd "$WS"
for f in $(grep -rl --exclude-dir=.venv --exclude-dir=.git --include='*.py' --include='*.md' calc_total . || true); do
  perl -pi -e 's/calc_total/compute_order_total/g' "$f"
done
