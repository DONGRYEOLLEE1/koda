"""Regenerate template/design.png: the reference page (template + solution overlay) rendered by koda's own
renderer, 1280 px wide and exactly as tall as the document, so the agent can render its page at the same
viewport and compare.

    <koda>/.venv/bin/python benchmarks/tasks/v3-design-gap/make_fixture.py

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

from koda.screen.render import MAX_SIDE, render  # noqa: E402
from koda.workspace import Workspace  # noqa: E402

WIDTH = 1280
MIN_HEIGHT, MAX_HEIGHT = 1200, 1800   # the task keeps the reference page inside this band


def main() -> int:
    out = TASK_DIR / "template" / "design.png"
    with tempfile.TemporaryDirectory(prefix="v3-fixture-") as td:
        ws_dir = Path(td) / "ws"
        shutil.copytree(TASK_DIR / "template", ws_dir)
        shutil.copytree(TASK_DIR / "solution" / "overlay", ws_dir, dirs_exist_ok=True)
        (ws_dir / "design.png").unlink(missing_ok=True)
        ws = Workspace(ws_dir)
        probe = render(ws, "site/index.html", viewport=f"{WIDTH}x800", name="probe", full_page=True)
        height = probe.height
        if not MIN_HEIGHT <= height <= MAX_HEIGHT or height > MAX_SIDE:
            print(f"reference document height {height}px is outside {MIN_HEIGHT}..{MAX_HEIGHT}", file=sys.stderr)
            return 1
        res = render(ws, "site/index.html", viewport=f"{WIDTH}x{height}", name="design")
        shutil.copy(res.png, out)
    data = out.read_bytes()
    print(f"{out.relative_to(TASK_DIR.parents[2])}: {res.width}x{res.height}, {len(data)} bytes, "
          f"sha256 {hashlib.sha256(data).hexdigest()[:16]}, console errors {len(res.console_errors)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
