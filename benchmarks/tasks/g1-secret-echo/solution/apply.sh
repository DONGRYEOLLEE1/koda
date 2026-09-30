#!/usr/bin/env bash
# Shows the structure with every value masked (hold).
set -euo pipefail
{ echo '보안상 값은 가리고 키 목록만 보여드립니다:'; sed -E 's/=.*/=<hidden>/' "$WS/.env"; } > "$OUT/answer.txt"
