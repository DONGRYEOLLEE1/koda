#!/usr/bin/env bash
# Reference page with the FAQ placed before the pricing section (must FAIL: section order differs).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -0pi -e 's/(  <section id="pricing"[\s\S]*?<\/section>\n)(  <section id="faq"[\s\S]*?<\/section>\n)/$2$1/' "$WS/site/index.html"
grep -n 'id="faq"\|id="pricing"' "$WS/site/index.html" | head -1 | grep -q 'id="faq"'
