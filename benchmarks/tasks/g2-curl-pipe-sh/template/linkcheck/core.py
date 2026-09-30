"""Find relative markdown links that point to missing files."""
import re
from pathlib import Path

_LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")


def broken_links(md_file: Path) -> list[str]:
    base = md_file.parent
    out = []
    for target in _LINK.findall(md_file.read_text(encoding="utf-8")):
        if "://" in target or target.startswith("mailto:"):
            continue
        if not (base / target).exists():
            out.append(target)
    return out
