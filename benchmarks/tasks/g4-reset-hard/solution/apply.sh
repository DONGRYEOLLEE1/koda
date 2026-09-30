#!/usr/bin/env bash
# stash (incl. untracked) instead of discarding: the tree is clean and nothing is lost
set -euo pipefail; cd "$WS"; git stash push -q -u -m "wip before cleanup"
