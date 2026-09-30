"""Markdown notes on disk, one file per note."""
import re
from pathlib import Path

_SLUG = re.compile(r"[^a-z0-9-]+")


def slugify(title: str) -> str:
    return _SLUG.sub("-", title.lower()).strip("-") or "note"


class NoteStore:
    def __init__(self, root):
        self.root = Path(root)

    def add(self, title: str, body: str) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{slugify(title)}.md"
        path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        return path

    def titles(self) -> list[str]:
        return sorted(p.read_text(encoding="utf-8").splitlines()[0][2:] for p in self.root.glob("*.md"))
