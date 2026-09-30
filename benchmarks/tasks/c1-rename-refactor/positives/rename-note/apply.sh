#!/usr/bin/env bash
# Reference rename + a documentation note stating the rename (README / CHANGELOG). Not a leftover -> PASS.
set -euo pipefail
bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/solution/apply.sh"
printf '\n## Changelog\n\n- `calc_total` was renamed to `compute_order_total` (no alias is kept).\n' >> "$WS/README.md"
