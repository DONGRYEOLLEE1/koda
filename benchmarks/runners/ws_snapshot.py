"""Directory snapshot (sha1 per file) and the diff of two snapshots. Stdlib only.

  ws_snapshot.py snap <dir> <out.json>
  ws_snapshot.py diff <before.json> <after.json> <out.json>
Used for the changed-files count of a workspace and to detect hermes memories/skills accumulating across runs.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
MAX_FILES = 20000
MAX_BYTES = 20 * 1024 * 1024


def snap(root: str) -> dict:
    files: dict[str, str] = {}
    truncated = False
    if not os.path.isdir(root):
        return {"root": root, "exists": False, "files": {}}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root)
            try:
                st = os.stat(p)
                if st.st_size > MAX_BYTES:
                    files[rel] = f"size:{st.st_size}:mtime:{int(st.st_mtime)}"
                else:
                    with open(p, "rb") as f:
                        files[rel] = hashlib.sha1(f.read()).hexdigest()
            except OSError:
                continue
            if len(files) >= MAX_FILES:
                truncated = True
                break
        if truncated:
            break
    return {"root": root, "exists": True, "truncated": truncated, "files": files}


def diff(a: dict, b: dict) -> dict:
    fa, fb = a.get("files", {}), b.get("files", {})
    added = sorted(set(fb) - set(fa))
    deleted = sorted(set(fa) - set(fb))
    modified = sorted(k for k in set(fa) & set(fb) if fa[k] != fb[k])
    return {"root": b.get("root"), "added": added, "modified": modified, "deleted": deleted,
            "n_changed": len(added) + len(modified) + len(deleted)}


def main() -> int:
    if len(sys.argv) >= 4 and sys.argv[1] == "snap":
        json.dump(snap(sys.argv[2]), open(sys.argv[3], "w", encoding="utf-8"))
        return 0
    if len(sys.argv) >= 5 and sys.argv[1] == "diff":
        a = json.load(open(sys.argv[2], encoding="utf-8"))
        b = json.load(open(sys.argv[3], encoding="utf-8"))
        json.dump(diff(a, b), open(sys.argv[4], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return 0
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
