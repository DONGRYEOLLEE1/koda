#!/usr/bin/env bash
# Reference fix, then the title box is clipped to 2px (must FAIL: h1 height changed).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
printf "\n.hero h1 { height: 2px; overflow: hidden; }\n" >> "$WS/site/style.css"
