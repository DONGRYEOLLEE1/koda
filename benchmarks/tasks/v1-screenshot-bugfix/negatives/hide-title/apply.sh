#!/usr/bin/env bash
# Reference fix, then the title is hidden with display:none (must FAIL: no visible title).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -0pi -e 's/\.hero h1 \{\n/.hero h1 {\n  display: none;\n/' "$WS/site/style.css"
