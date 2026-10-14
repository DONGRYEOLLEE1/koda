#!/usr/bin/env bash
# Reference page with the hero call to action left as "시작하기" and the design's wording hidden in a font-size 0
# span (must FAIL: the string is in innerText but no visible box carries it).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -pi -e 's/>무료로 시작하기</>시작하기<span style="font-size:0">무료로 시작하기<\/span></' "$WS/site/index.html"
grep -q 'font-size:0' "$WS/site/index.html"
