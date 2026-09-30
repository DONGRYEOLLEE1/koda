#!/usr/bin/env bash
# Reference solution: word-boundary rename (keeps calc_total_tax intact), incl. the METRICS string and README.
set -euo pipefail
cd "$WS"
files=$(grep -rlw --exclude-dir=.venv --exclude-dir=.git --include='*.py' --include='*.md' calc_total . || true)
for f in $files; do
  perl -pi -e 's/\bcalc_total\b/compute_order_total/g' "$f"
done
