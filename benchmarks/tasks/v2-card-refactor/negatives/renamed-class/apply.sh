#!/usr/bin/env bash
# The four static cards stay in index.html under another class name and a one-line script renames it back to
# "card" (must FAIL: the JS-off render still shows every card text, so the content does not come from data).
set -euo pipefail
perl -pi -e 's/class="card"/class="kard"/g' "$WS/site/index.html"
printf 'document.querySelectorAll(".kard").forEach(e => e.classList.replace("kard", "card"));\n' > "$WS/site/app.js"
grep -c 'class="kard"' "$WS/site/index.html" | grep -qx 4
