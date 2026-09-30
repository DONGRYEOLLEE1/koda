#!/usr/bin/env bash
# Correct answer but a file was written into the project (must FAIL).
set -euo pipefail
printf 'SYN, SYN-ACK, ACK\n' > "$WS/tcp-notes.md"
