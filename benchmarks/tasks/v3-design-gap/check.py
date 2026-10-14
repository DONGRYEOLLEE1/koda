"""v3 check: the page now has the design's four sections, in order, with the design's wording.

The reference is the template plus solution/ overlay, rendered at check time in the same headless chromium as
the workspace (1280x800, JS on). design.png is only for the agent; the judge never looks at pixels and never
uses a vision model. pass iff:
  required_texts   each of the eight required strings (section titles, the two calls to action, a feature name,
                   a price) is the whole normalized text of a visible DOM box; a substring of a longer text
                   (the pricing description mentions the feature) or text with no visible box (font-size 0)
                   does not count
  section_order    the four section titles are found as elements and their top edges are in the design's order
  section_position each title's top edge is within 5% of the reference document height of the reference top
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))
import checklib as cl  # noqa: E402
import domboxes as db  # noqa: E402

PAGE = Path("site") / "index.html"
SECTION_TITLES = ["생각을 물결처럼 기록하세요", "주요 기능", "요금제", "자주 묻는 질문"]
REQUIRED_TEXTS = [*SECTION_TITLES, "무료로 시작하기", "공유 링크", "월 4,900원", "프로 시작하기"]
POSITION_TOLERANCE = 0.05
HEADING_TYPES = ("HEADING", "TEXT")


def title_top(r: db.Rendered, title: str) -> int | None:
    box = r.find(title, HEADING_TYPES)
    return box["bbox_px"][1] if box else None


def judge(ws: Path, task_dir: Path) -> tuple[bool | None, dict]:
    page = ws / PAGE
    if not page.is_file():
        return False, {"error": f"{PAGE} missing"}
    with tempfile.TemporaryDirectory(prefix="bench-check-") as td:
        ref_dir = Path(td) / "ref"
        cl.copy_workspace(task_dir / "template", ref_dir)
        cl.copy_workspace(task_dir / "solution" / "overlay", ref_dir)
        ref = db.render(ref_dir / PAGE, root=ref_dir)
        tmp = Path(td) / "ws"
        cl.copy_workspace(ws, tmp)
        try:
            cur = db.render(tmp / PAGE, root=tmp)
        except Exception as e:  # noqa: BLE001  (a page that cannot be loaded is a failed change)
            return False, {"error": f"render failed: {type(e).__name__}: {str(e)[:300]}"}

    ref_tops = [title_top(ref, t) for t in SECTION_TITLES]
    if any(ref.find(t) is None for t in REQUIRED_TEXTS) or any(t is None for t in ref_tops) or ref.doc_height <= 0:
        return None, {"error": "reference render lacks a required string or section title (fixture broken)",
                      "ref_tops": ref_tops}

    texts = {t: cur.find(t) is not None for t in REQUIRED_TEXTS}
    cur_tops = [title_top(cur, t) for t in SECTION_TITLES]
    found = all(t is not None for t in cur_tops)
    order_ok = found and all(a < b for a, b in zip(cur_tops, cur_tops[1:]))
    tol = POSITION_TOLERANCE * ref.doc_height
    deltas = [None if c is None else c - r for c, r in zip(cur_tops, ref_tops)]
    position_ok = found and all(abs(d) <= tol for d in deltas)
    checks = {"required_texts": all(texts.values()), "section_order": order_ok, "section_position": position_ok}
    detail = {
        "checks": checks,
        "texts": texts,
        "sections": [{"title": t, "ref_top": r, "top": c, "delta": d}
                     for t, r, c, d in zip(SECTION_TITLES, ref_tops, cur_tops, deltas)],
        "tolerance_px": round(tol, 1),
        "doc_height": {"reference": ref.doc_height, "workspace": cur.doc_height},
    }
    return all(checks.values()), detail


a = cl.parse_args()
passed, detail = judge(a.ws, a.task_dir)
detail["workspace_changes"] = cl.workspace_changes(a.ws, a.private)
cl.emit(passed, detail)
