#!/usr/bin/env bash
# Reference fix, then the title is shrunk to 6px so it cannot touch anything (must FAIL: h1 height changed).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
printf "\n.hero h1 { font-size: 6px; }\n" >> "$WS/site/style.css"
