#!/usr/bin/env bash
# The title stays absolute and is only moved down, so it now sits on the lead paragraph (must FAIL: overlap).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -0pi -e 's/\.hero h1 \{\n/.hero h1 {\n  position: absolute;\n  top: 140px;\n  left: 0;\n  right: 0;\n/' "$WS/site/style.css"
