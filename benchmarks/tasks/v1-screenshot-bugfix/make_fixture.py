"""Regenerate template/bug.png: the broken template rendered by koda's own renderer (1280x800, dsf 1).

    <koda>/.venv/bin/python benchmarks/tasks/v1-screenshot-bugfix/make_fixture.py

The committed PNG is the canonical fixture. Regenerating it rewrites the bytes (font and chromium versions
leak into the pixels) and therefore changes suite_hash; do it only together with a CHANGELOG entry.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
KODA_SRC = TASK_DIR.parents[2] / "src"
sys.path.insert(0, str(KODA_SRC))

from koda.screen.render import render  # noqa: E402
from koda.workspace import Workspace  # noqa: E402


def main() -> int:
    out = TASK_DIR / "template" / "bug.png"
    with tempfile.TemporaryDirectory(prefix="v1-fixture-") as td:
        ws_dir = Path(td) / "ws"
        shutil.copytree(TASK_DIR / "template", ws_dir)
        (ws_dir / "bug.png").unlink(missing_ok=True)
        res = render(Workspace(ws_dir), "site/index.html", viewport="1280x800", name="bug")
        shutil.copy(res.png, out)
    data = out.read_bytes()
    print(f"{out.relative_to(TASK_DIR.parents[2])}: {res.width}x{res.height}, {len(data)} bytes, "
          f"sha256 {hashlib.sha256(data).hexdigest()[:16]}, console errors {len(res.console_errors)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
