#!/usr/bin/env bash
# Reference refactor with one price mistyped in the data array (must FAIL: card text differs).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -pi -e 's/13,200원/13,500원/' "$WS/site/app.js"
grep -q '13,500원' "$WS/site/app.js"
