#!/usr/bin/env bash
# The absolute rule is kept and the whole header is made transparent (must FAIL: header boxes gone).
set -euo pipefail
printf "\n.topbar { opacity: 0; }\n" >> "$WS/site/style.css"
