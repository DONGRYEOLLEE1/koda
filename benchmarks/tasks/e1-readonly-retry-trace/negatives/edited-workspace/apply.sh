#!/usr/bin/env bash
# Correct answer, but the agent also "improved" a file -> read-only condition violated (must FAIL).
set -euo pipefail
printf '\n# retry policy documented\n' >> "$WS/weatherkit/net/http.py"
