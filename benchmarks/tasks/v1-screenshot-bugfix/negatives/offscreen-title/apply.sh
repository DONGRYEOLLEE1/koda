#!/usr/bin/env bash
# The absolute rule is kept and the title is pushed above the viewport (must FAIL: box outside 1280x800).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -0pi -e 's/\.hero h1 \{\n/.hero h1 {\n  position: absolute;\n  top: -200px;\n  left: 0;\n  right: 0;\n/' "$WS/site/style.css"
