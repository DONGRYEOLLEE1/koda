#!/usr/bin/env bash
# Reference page without the pricing section (must FAIL: three required strings missing, FAQ moves up).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -0pi -e 's/  <section id="pricing"[\s\S]*?<\/section>\n//' "$WS/site/index.html"
grep -q 'id="pricing"' "$WS/site/index.html" && { echo "pricing still present" >&2; exit 1; } || true
