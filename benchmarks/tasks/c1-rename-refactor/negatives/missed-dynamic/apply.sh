#!/usr/bin/env bash
# Symbol-level rename that misses the string reference in reports.METRICS (must FAIL).
set -euo pipefail
cd "$WS"
for f in $(grep -rlw --exclude-dir=.venv --exclude-dir=.git --include='*.py' --include='*.md' calc_total . || true); do
  perl -pi -e 's/(?<!")\bcalc_total\b(?!")/compute_order_total/g' "$f"
done
