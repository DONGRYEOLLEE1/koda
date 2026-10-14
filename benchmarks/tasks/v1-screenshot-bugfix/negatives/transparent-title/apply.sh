#!/usr/bin/env bash
# Reference fix, then the title text is made transparent (must FAIL: color alpha 0).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -0pi -e 's/\.hero h1 \{\n/.hero h1 {\n  color: transparent;\n/' "$WS/site/style.css"
