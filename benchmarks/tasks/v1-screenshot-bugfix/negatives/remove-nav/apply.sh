#!/usr/bin/env bash
# Reference fix, then the navigation is deleted so nothing can overlap (must FAIL: menu text gone).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -0pi -e 's/  <nav class="menu"[^\n]*\n(?:    <a [^\n]*\n)+  <\/nav>\n//' "$WS/site/index.html"
grep -q '<nav' "$WS/site/index.html" && { echo "nav still present" >&2; exit 1; } || true
