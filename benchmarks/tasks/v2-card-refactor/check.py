"""v2 check: the four cards are rendered from data by JS, and the screen is unchanged.

Three headless renders (chromium, 1280x800): the template with JS on (the reference), the workspace with JS on,
and the workspace with JS off. Judged on DOM boxes, never on screenshots or a vision model. pass iff:
  boxes_unchanged   every visible text-bearing element of the reference (type, text, box within 2 px) is found
                    1:1 in the workspace render and nothing extra appears
  cards_unchanged   the four .card boxes are within 2 px of the reference
  cards_from_js     .card count is 4 with JS on and 0 with JS off, and the JS-off render shows no text box of any
                    card (title, author, price, button): the card content comes from the script, not from static
                    markup under another class name
  one_card_literal  site/index.html contains at most one `class="card"` / `class='card'` literal
  one_card_literal_js  every .js file under site/ contains at most one `class="card"` literal and at most one
                    `<article` (the cards come from one template, not from a pasted markup blob)
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))
import checklib as cl  # noqa: E402
import domboxes as db  # noqa: E402

PAGE = Path("site") / "index.html"
TOL_PX = 2
CARD_COUNT = 4
CARD_LITERAL = re.compile(r"""class=["']card["']""")
ARTICLE_TAG = re.compile(r"<article\b", re.IGNORECASE)
CARDS_JS = r"""() => {
  const sx = window.scrollX || 0, sy = window.scrollY || 0;
  return Array.from(document.querySelectorAll(".card")).map(e => {
    const b = e.getBoundingClientRect();
    return [Math.round(b.left + sx), Math.round(b.top + sy), Math.round(b.right + sx), Math.round(b.bottom + sy)];
  });
}"""


def card_boxes(r: db.Rendered) -> list[list[int]]:
    return sorted(r.extra.get("cards") or [], key=lambda b: (b[1], b[0]))


def inside(box: list[int], outer: list[int]) -> bool:
    return box[0] >= outer[0] and box[1] >= outer[1] and box[2] <= outer[2] and box[3] <= outer[3]


def card_texts(r: db.Rendered) -> set[str]:
    """Normalized text of every box that lies inside one of the render's .card boxes."""
    cards = card_boxes(r)
    return {db.norm(b.get("text", "")) for b in r.boxes if b.get("text") and any(inside(b["bbox_px"], c) for c in cards)}


def judge(ws: Path, task_dir: Path) -> tuple[bool | None, dict]:
    page = ws / PAGE
    if not page.is_file():
        return False, {"error": f"{PAGE} missing"}
    ref = db.render(task_dir / "template" / PAGE, root=task_dir / "template", extra_js={"cards": CARDS_JS})
    with tempfile.TemporaryDirectory(prefix="bench-check-") as td:
        tmp = Path(td) / "ws"
        cl.copy_workspace(ws, tmp)
        try:
            cur = db.render(tmp / PAGE, root=tmp, extra_js={"cards": CARDS_JS})
            nojs = db.render(tmp / PAGE, root=tmp, javascript=False, extra_js={"cards": CARDS_JS})
        except Exception as e:  # noqa: BLE001  (a page that cannot be loaded is a failed refactor)
            return False, {"error": f"render failed: {type(e).__name__}: {str(e)[:300]}"}

    m = db.match_boxes(ref.boxes, cur.boxes, TOL_PX)
    ref_cards, cur_cards, nojs_cards = card_boxes(ref), card_boxes(cur), card_boxes(nojs)
    cards_same = len(ref_cards) == len(cur_cards) and all(db.within(a, b, TOL_PX) for a, b in zip(ref_cards, cur_cards))
    ref_texts = card_texts(ref)
    nojs_card_texts = sorted(t for t in (db.norm(b.get("text", "")) for b in nojs.boxes) if t in ref_texts)
    literals = len(CARD_LITERAL.findall(page.read_text(encoding="utf-8", errors="replace")))
    scripts = {}
    for js in sorted((ws / "site").rglob("*.js")):
        text = js.read_text(encoding="utf-8", errors="replace")
        scripts[js.relative_to(ws).as_posix()] = {"card_literals": len(CARD_LITERAL.findall(text)),
                                                  "article_tags": len(ARTICLE_TAG.findall(text))}
    checks = {
        "boxes_unchanged": not m["missing"] and not m["extra"],
        "cards_unchanged": cards_same,
        "cards_from_js": len(cur_cards) == CARD_COUNT and len(nojs_cards) == 0 and not nojs_card_texts,
        "one_card_literal": literals <= 1,
        "one_card_literal_js": all(v["card_literals"] <= 1 and v["article_tags"] <= 1 for v in scripts.values()),
    }
    detail = {
        "checks": checks,
        "boxes": {"reference": len(ref.boxes), "matched": m["matched"], "missing": db.brief(m["missing"]),
                  "extra": db.brief(m["extra"]), "tolerance_px": TOL_PX},
        "cards": {"reference": ref_cards, "js_on": cur_cards, "js_off_count": len(nojs_cards),
                  "js_off_card_texts": nojs_card_texts[:10]},
        "card_literals_in_index_html": literals,
        "scripts": scripts,
    }
    return all(checks.values()), detail


a = cl.parse_args()
passed, detail = judge(a.ws, a.task_dir)
detail["workspace_changes"] = cl.workspace_changes(a.ws, a.private)
cl.emit(passed, detail)
