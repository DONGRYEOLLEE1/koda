#!/usr/bin/env bash
# A different correct fix: the hero becomes the positioning context and reserves room, the title stays
# absolute inside it (must PASS).
set -euo pipefail
cd "$WS"
perl -0pi -e 's/\.hero \{\n  max-width: 760px;\n  margin: 0 auto;\n  padding: 72px 48px 56px;/.hero {\n  position: relative;\n  max-width: 760px;\n  margin: 0 auto;\n  padding: 150px 48px 56px;/' site/style.css
perl -0pi -e 's/\.hero h1 \{\n  position: absolute;\n  top: 0;/.hero h1 {\n  position: absolute;\n  top: 56px;/' site/style.css
grep -q 'padding: 150px 48px 56px' site/style.css
