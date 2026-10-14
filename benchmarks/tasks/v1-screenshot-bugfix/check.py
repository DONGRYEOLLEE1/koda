"""v1 check: the hero title no longer collides with the navigation, and nothing else changed on screen.

The workspace's site/index.html is rendered headlessly (chromium, 1280x800, JS on) and judged on DOM boxes,
never on a screenshot or a vision model. pass iff every item holds:
  h1_in_viewport   the first <h1> has a box of at least 1x1 px that lies inside the 1280x800 viewport
  h1_visible       computed display != none (itself and ancestors), visibility visible, opacity > 0 along the
                   ancestor chain, text color alpha > 0
  h1_hit_test      elementFromPoint at the h1 centre is the h1 or one of its descendants (nothing paints over it)
  h1_no_overlap    the h1 box does not intersect the box of any other visible text-bearing element
                   (links, buttons, paragraphs, ...), so the title sits neither on the menu nor on its own lead
  text_set_equal   the set of innerText lines equals the template's (the title is still there, the menu is
                   still there, no copy was rewritten to dodge the overlap)
  header_unchanged every text box of the template render that lies within the header (brand, menu links,
                   reserve link) is matched 1:1 by type, text and box within 2 px (the header was not hidden,
                   moved or pushed down to make room)
  h1_size_kept     the h1 box is as tall as the template's within 2 px (the title was not shrunk or clipped
                   out of the way)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))
import checklib as cl  # noqa: E402
import domboxes as db  # noqa: E402

PAGE = Path("site") / "index.html"
VW, VH = db.VIEWPORT
TOL_PX = 2
HEADER_JS = r"""() => {
  const h = document.querySelector("header");
  if (!h) return null;
  const sx = window.scrollX || 0, sy = window.scrollY || 0, b = h.getBoundingClientRect();
  return [Math.round(b.left + sx), Math.round(b.top + sy), Math.round(b.right + sx), Math.round(b.bottom + sy)];
}"""

H1_JS = r"""() => {
  const h1 = document.querySelector("h1");
  if (!h1) return {found: false};
  const sx = window.scrollX || 0, sy = window.scrollY || 0;
  const px = (b) => [Math.round(b.left + sx), Math.round(b.top + sy), Math.round(b.right + sx), Math.round(b.bottom + sy)];
  const r = h1.getBoundingClientRect();
  const cs = getComputedStyle(h1);
  let opacity = 1, displayNone = false;
  for (let a = h1; a; a = a.parentElement) {
    const s = getComputedStyle(a);
    opacity = Math.min(opacity, parseFloat(s.opacity));
    if (s.display === "none") displayNone = true;
  }
  const alpha = (c) => {
    const m = (c || "").match(/\(([^)]+)\)/);
    if (!m) return 1;
    const parts = m[1].split(/[\s,\/]+/).filter(Boolean);
    return parts.length >= 4 ? parseFloat(parts[3]) : 1;
  };
  const hit = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
  const ancestors = [];
  for (let a = h1.parentElement; a; a = a.parentElement) ancestors.push(a);
  return {
    found: true, bbox: px(r), display: cs.display, display_none: displayNone, visibility: cs.visibility,
    opacity: opacity, color: cs.color, color_alpha: alpha(cs.color),
    hit_ok: !!hit && (hit === h1 || h1.contains(hit)), hit_tag: hit ? hit.tagName.toLowerCase() : null,
    descendants: Array.from(h1.querySelectorAll("*")).map(e => px(e.getBoundingClientRect())),
    ancestors: ancestors.map(e => px(e.getBoundingClientRect())),
  };
}"""


def judge(ws: Path, task_dir: Path) -> tuple[bool | None, dict]:
    page = ws / PAGE
    if not page.is_file():
        return False, {"error": f"{PAGE} missing"}
    ref = db.render(task_dir / "template" / PAGE, root=task_dir / "template", extra_js={"h1": H1_JS, "header": HEADER_JS})
    ref_h1, ref_header = ref.extra.get("h1") or {}, ref.extra.get("header")
    if not ref_h1.get("found") or not ref_header:
        return None, {"error": "reference render lacks the h1 or the header (fixture broken)"}
    with tempfile.TemporaryDirectory(prefix="bench-check-") as td:
        tmp = Path(td) / "ws"
        cl.copy_workspace(ws, tmp)
        try:
            cur = db.render(tmp / PAGE, root=tmp, extra_js={"h1": H1_JS})
        except Exception as e:  # noqa: BLE001  (a page that cannot be loaded is a failed fix, not an unjudged run)
            return False, {"error": f"render failed: {type(e).__name__}: {str(e)[:300]}"}

    h1 = cur.extra.get("h1") or {}
    checks = {k: False for k in ("h1_in_viewport", "h1_visible", "h1_hit_test", "h1_no_overlap", "text_set_equal",
                                 "header_unchanged", "h1_size_kept")}
    overlaps: list[dict] = []
    if h1.get("found"):
        x0, y0, x1, y1 = h1["bbox"]
        checks["h1_in_viewport"] = 0 <= x0 and 0 <= y0 and x1 <= VW and y1 <= VH and x1 - x0 >= 1 and y1 - y0 >= 1
        checks["h1_visible"] = (not h1["display_none"] and h1["visibility"] == "visible" and h1["opacity"] > 0
                                and h1["color_alpha"] > 0)
        checks["h1_hit_test"] = bool(h1["hit_ok"])
        own = {tuple(b) for b in h1["descendants"]} | {tuple(b) for b in h1["ancestors"]}
        seen_self = False
        for b in cur.boxes:
            if not seen_self and b["tag"] == "h1" and b["bbox_px"] == h1["bbox"]:
                seen_self = True
                continue
            if not b.get("text") or tuple(b["bbox_px"]) in own:
                continue
            if db.intersects(b["bbox_px"], h1["bbox"]):
                overlaps.append(b)
        checks["h1_no_overlap"] = checks["h1_in_viewport"] and not overlaps
        ref_height, cur_height = ref_h1["bbox"][3] - ref_h1["bbox"][1], y1 - y0
        checks["h1_size_kept"] = abs(cur_height - ref_height) <= TOL_PX
    ref_lines, cur_lines = set(db.lines(ref.inner_text)), set(db.lines(cur.inner_text))
    checks["text_set_equal"] = ref_lines == cur_lines
    # the template's h1 is drawn over the header, so it is left out of the header box set by tag
    header_boxes = [b for b in ref.boxes if b["tag"] != "h1" and b.get("text") and b["bbox_px"][3] <= ref_header[3]]
    header = db.match_boxes(header_boxes, cur.boxes, TOL_PX)
    checks["header_unchanged"] = bool(header_boxes) and not header["missing"]
    detail = {
        "checks": checks,
        "h1": {k: h1.get(k) for k in ("found", "bbox", "display", "visibility", "opacity", "color", "hit_tag")},
        "h1_height": {"reference": ref_h1["bbox"][3] - ref_h1["bbox"][1], "workspace": h1["bbox"][3] - h1["bbox"][1] if h1.get("found") else None},
        "overlaps": db.brief(overlaps),
        "text_missing": sorted(ref_lines - cur_lines)[:10],
        "text_extra": sorted(cur_lines - ref_lines)[:10],
        "header": {"reference": len(header_boxes), "matched": header["matched"], "missing": db.brief(header["missing"]),
                   "tolerance_px": TOL_PX},
        "doc_height": cur.doc_height,
    }
    return all(checks.values()), detail


a = cl.parse_args()
passed, detail = judge(a.ws, a.task_dir)
detail["workspace_changes"] = cl.workspace_changes(a.ws, a.private)
cl.emit(passed, detail)
