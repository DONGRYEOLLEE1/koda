#!/usr/bin/env bash
# The data array and template function are added to app.js, but the four static cards stay in index.html and
# app.js never renders (must FAIL: cards present without JS, four class="card" literals).
set -euo pipefail
cp "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/site/app.js" "$WS/site/app.js"
perl -0pi -e 's/\ndocument\.getElementById\("cards"\)\.innerHTML = [^\n]*\n/\n\/\/ render step not wired up yet\n/' "$WS/site/app.js"
