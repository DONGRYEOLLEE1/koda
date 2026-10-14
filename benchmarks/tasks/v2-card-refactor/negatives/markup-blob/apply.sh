#!/usr/bin/env bash
# Reference index.html, but app.js assigns the verbatim four-card markup to innerHTML with no data array and no
# template function (must FAIL: four class="card" literals and four <article> tags in the script).
set -euo pipefail
task_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cp "$task_dir/solution/overlay/site/index.html" "$WS/site/index.html"
{
  printf 'document.getElementById("cards").innerHTML = `\n'
  perl -0ne 'print $1 if /(<article class="card">.*<\/article>)\n  <\/section>/s' "$task_dir/template/site/index.html"
  printf '\n`;\n'
} > "$WS/site/app.js"
grep -c '<article' "$WS/site/app.js" | grep -qx 4
