#!/usr/bin/env bash
# Reference refactor plus a padding change on the cards (must FAIL: boxes move by more than 2 px).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -0pi -e 's/\.card \{\n  display: flex;\n  flex-direction: column;\n  padding: 16px;/.card {\n  display: flex;\n  flex-direction: column;\n  padding: 20px;/' "$WS/site/style.css"
grep -q 'padding: 20px;' "$WS/site/style.css"
