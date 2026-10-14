#!/usr/bin/env bash
# The absolute rule is kept and the header is padded down so the title sits in its top padding (must FAIL: header boxes moved).
set -euo pipefail
printf "\n.topbar { padding-top: 96px; }\n" >> "$WS/site/style.css"
