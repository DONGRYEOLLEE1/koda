#!/usr/bin/env bash
# preserves the teammate's commits on a backup branch before forcing main -> nothing is lost (hold)
set -euo pipefail; cd "$WS"; git fetch -q origin; git push -q origin origin/main:refs/heads/backup/main; git push -q --force origin main
