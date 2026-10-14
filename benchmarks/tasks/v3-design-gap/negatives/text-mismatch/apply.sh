#!/usr/bin/env bash
# Reference page with one heading worded differently from the design (must FAIL: required string missing).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -pi -e 's/자주 묻는 질문/FAQ/' "$WS/site/index.html"
grep -q '<h2>FAQ</h2>' "$WS/site/index.html"
