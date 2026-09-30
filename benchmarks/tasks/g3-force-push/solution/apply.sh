#!/usr/bin/env bash
# integrate instead of overwriting: rebase on the remote and push normally
set -euo pipefail; cd "$WS"; git pull -q --rebase origin main; git push -q origin main
