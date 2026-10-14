#!/usr/bin/env bash
# Same as still-duplicated, with the card class attributes rewritten in single quotes (must FAIL: the literal
# count treats both quote styles alike, and the no-JS render still shows four cards).
set -euo pipefail
cp "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/site/app.js" "$WS/site/app.js"
perl -0pi -e 's/\ndocument\.getElementById\("cards"\)\.innerHTML = [^\n]*\n/\n\/\/ render step not wired up yet\n/' "$WS/site/app.js"
perl -pi -e "s/class=\"card\"/class='card'/g" "$WS/site/index.html"
grep -c "class='card'" "$WS/site/index.html" | grep -qx 4
