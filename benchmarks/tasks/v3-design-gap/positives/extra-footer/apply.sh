#!/usr/bin/env bash
# Reference page plus a footer the design does not show (must PASS: extra content below the sections is fine).
set -euo pipefail
cp -R "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/overlay/." "$WS/"
perl -0pi -e 's/<\/main>\n/<\/main>\n<footer>\n  <p>물결 노트 · 서울 성동구 왕십리로 83 · help\@example.com<\/p>\n<\/footer>\n/' "$WS/site/index.html"
grep -q '<footer>' "$WS/site/index.html"
