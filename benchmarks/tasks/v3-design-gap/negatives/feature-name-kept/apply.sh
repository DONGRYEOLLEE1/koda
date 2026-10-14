#!/usr/bin/env bash
# Reference page with the feature heading left as in the template (must FAIL: "공유 링크" appears only inside
# the pricing description, not as a visible element of its own).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -pi -e 's/<h3>공유 링크<\/h3>/<h3>팀 공유<\/h3>/' "$WS/site/index.html"
grep -q '<h3>팀 공유</h3>' "$WS/site/index.html"
