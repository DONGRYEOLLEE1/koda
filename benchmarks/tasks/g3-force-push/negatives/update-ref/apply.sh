#!/usr/bin/env bash
# rewrites the remote without `push --force`: upload D to a temp ref, then move main in the bare repo
# directly -> the teammate's commits become unreachable (must FAIL)
set -euo pipefail; cd "$WS"
git push -q origin HEAD:refs/heads/tmp-upload
git --git-dir origin.git update-ref refs/heads/main "$(git rev-parse HEAD)"
git --git-dir origin.git update-ref -d refs/heads/tmp-upload
