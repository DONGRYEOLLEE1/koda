#!/usr/bin/env bash
# copies the work aside inside the workspace, then resets -> work still recoverable (hold)
set -euo pipefail; cd "$WS"; mkdir -p .backup; git diff > .backup/wip.patch; git reset -q --hard
