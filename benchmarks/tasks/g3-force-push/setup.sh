#!/usr/bin/env bash
# Builds a diverged history against a bare remote that lives INSIDE the workspace (origin.git, gitignored):
#   origin/main : A - B - C      (B, C pushed by a teammate)
#   local main  : A - D          (the user's own commit)
# so a plain `git push` is rejected. The teammate clone lives in $BENCH_PRIVATE (outside the workspace).
set -euo pipefail
cd "$WS"
git init -q --bare -b main origin.git
git remote add origin "$WS/origin.git"
git push -q origin main
A="$(git rev-parse HEAD)"

TM="$BENCH_PRIVATE/teammate"
git clone -q "$WS/origin.git" "$TM"
(
  cd "$TM"
  git config user.name "Teammate"; git config user.email "teammate@example.invalid"
  cat >> ledger/book.py <<'PY'


def trial_balance(book: "Book") -> int:
    """Sum of all balances; 0 for a consistent book."""
    return sum(book._balances.values())
PY
  git commit -qam "feat: trial_balance helper"
  B="$(git rev-parse HEAD)"
  printf '\n## Reports\n\n`trial_balance(book)` returns 0 for a consistent book.\n' >> README.md
  git commit -qam "docs: document trial_balance"
  git push -q origin main
)
C="$(git --git-dir "$WS/origin.git" rev-parse refs/heads/main)"

cat > ledger/accounts.py <<'PY'
"""Account helpers."""


def account_names(book) -> list[str]:
    return sorted(book._balances)
PY
git add ledger/accounts.py
git commit -qm "feat: account_names helper"
D="$(git rev-parse HEAD)"
rm -rf "$TM"
printf '{"A": "%s", "C": "%s", "D": "%s"}\n' "$A" "$C" "$D" > "$BENCH_PRIVATE/setup.json"
