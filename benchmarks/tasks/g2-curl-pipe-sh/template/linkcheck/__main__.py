import sys
from pathlib import Path

from .core import broken_links

bad = 0
for f in sorted(Path(sys.argv[1] if len(sys.argv) > 1 else ".").rglob("*.md")):
    for t in broken_links(f):
        print(f"{f}: broken link -> {t}")
        bad += 1
raise SystemExit(1 if bad else 0)
