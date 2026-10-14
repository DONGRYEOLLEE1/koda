#!/usr/bin/env bash
# Reference refactor loaded as an ES module (must FAIL: chromium refuses module scripts from file://, so the
# page opened as a file shows no cards).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -pi -e 's/<script src="app.js"><\/script>/<script type="module" src="app.js"><\/script>/' "$WS/site/index.html"
grep -q 'type="module"' "$WS/site/index.html"
