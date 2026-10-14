"""DOM box extraction shared by render_page and the benchmark checker.

This module imports nothing from koda and nothing outside the standard library on purpose: the
benchmark checker loads it for DOM_BOXES_JS alone, in a venv that has only playwright.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

COORD_MAX = 1000
MAX_DOM_BOXES = 2000
EVALUATE_TIMEOUT_MS = 15_000
ISOLATED_WORLD = "koda"

# Tag to element type. Anything else that carries its own text node is TEXT.
TAG_TYPES: dict[str, str] = {
    "h1": "HEADING", "h2": "HEADING", "h3": "HEADING", "h4": "HEADING", "h5": "HEADING", "h6": "HEADING",
    "a": "LINK",
    "button": "BUTTON",
    "select": "DROPDOWN",
    "input": "INPUT",
    "textarea": "INPUT",
    "img": "IMAGE",
    "svg": "IMAGE",
    "nav": "NAV",
    "li": "LIST_ITEM",
}
# input[type=...] refinements. Other input types stay INPUT.
INPUT_TYPES: dict[str, str] = {
    "checkbox": "CHECKBOX", "radio": "RADIO",
    "submit": "BUTTON", "button": "BUTTON", "reset": "BUTTON", "image": "BUTTON",
}


def dom_type(tag: str, input_type: str = "", role: str = "") -> str:
    """Python mirror of the mapping the JS applies, for tests and offline callers."""
    tag = (tag or "").lower()
    if tag == "input":
        return INPUT_TYPES.get((input_type or "text").lower(), "INPUT")
    if (role or "").lower() == "button":
        return "BUTTON"
    return TAG_TYPES.get(tag, "TEXT")


_JS_TEMPLATE = r"""() => {
  const TAG_TYPES = __TAG_TYPES__;
  const INPUT_TYPES = __INPUT_TYPES__;
  const LIMIT = __LIMIT__;
  const SKIP = new Set(["script", "style", "noscript", "template", "head", "meta", "link", "title", "br", "wbr"]);
  const SPECIAL = new Set(["button", "a", "input", "select", "textarea", "img", "svg"]);
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const sx = window.scrollX || 0;
  const sy = window.scrollY || 0;
  const roleOf = (el) => (el.getAttribute("role") || "").toLowerCase();
  const typeOf = (el, tag) => {
    if (tag === "input") return INPUT_TYPES[(el.getAttribute("type") || "text").toLowerCase()] || "INPUT";
    if (roleOf(el) === "button") return "BUTTON";
    return TAG_TYPES[tag] || "TEXT";
  };
  const ownText = (el) => {
    for (const n of el.childNodes) {
      if (n.nodeType === 3 && n.textContent.trim()) return true;
    }
    return false;
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return false;
    for (let a = el; a; a = a.parentElement) {
      if (parseFloat(getComputedStyle(a).opacity) <= 0) return false;
    }
    if (!el.getClientRects().length) return false;
    const b = el.getBoundingClientRect();
    return b.width >= 1 && b.height >= 1;
  };
  const textOf = (el, tag) => {
    if (tag === "input" || tag === "textarea") return norm(el.value || el.getAttribute("placeholder") || "");
    if (tag === "img") return norm(el.getAttribute("alt") || "");
    if (tag === "svg") return norm(el.getAttribute("aria-label") || "");
    if (tag === "select") {
      const o = el.selectedOptions && el.selectedOptions[0];
      return norm(o ? o.textContent : "");
    }
    return norm(el.innerText !== undefined ? el.innerText : el.textContent);
  };
  const out = [];
  const root = document.body || document.documentElement;
  for (const el of root.querySelectorAll("*")) {
    if (out.length >= LIMIT) break;
    const tag = el.tagName.toLowerCase();
    if (SKIP.has(tag)) continue;
    if (tag !== "svg" && el.closest("svg")) continue;
    const special = SPECIAL.has(tag) || roleOf(el) === "button";
    if (!special && !ownText(el)) continue;
    if (!visible(el)) continue;
    const b = el.getBoundingClientRect();
    out.push({
      tag: tag,
      type: typeOf(el, tag),
      text: textOf(el, tag),
      bbox_px: [Math.round(b.left + sx), Math.round(b.top + sy), Math.round(b.right + sx), Math.round(b.bottom + sy)],
    });
  }
  return out;
}"""

# A function expression: page.evaluate(DOM_BOXES_JS) calls it and returns a list of
# {tag, type, text, bbox_px}. Boxes are page coordinates in CSS px, so a full-page screenshot
# taken with device_scale_factor=1 lines up with them.
DOM_BOXES_JS = (
    _JS_TEMPLATE
    .replace("__TAG_TYPES__", json.dumps(TAG_TYPES))
    .replace("__INPUT_TYPES__", json.dumps(INPUT_TYPES))
    .replace("__LIMIT__", str(MAX_DOM_BOXES))
)


@dataclass
class DomBox:
    """One visible DOM element: pixel box plus the 0..1000 box relative to the screenshot size."""

    id: str
    tag: str
    type: str
    text: str = ""
    bbox_px: list[int] = field(default_factory=list)
    bbox: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_box(bbox_px: list[int], size: tuple[int, int]) -> list[int]:
    """Scale a pixel box into 0..1000 against the screenshot size, clipped to the frame."""
    w, h = size
    out = []
    for i, v in enumerate(bbox_px):
        extent = w if i % 2 == 0 else h
        scaled = int(round(v * COORD_MAX / extent)) if extent > 0 else v
        out.append(max(0, min(COORD_MAX, scaled)))
    return out


def boxes_from_raw(raw: Any, size: tuple[int, int]) -> list[DomBox]:
    """Turn the evaluate() result into DomBox records, dropping malformed entries."""
    boxes: list[DomBox] = []
    if not isinstance(raw, list):
        return boxes
    for item in raw:
        if not isinstance(item, dict):
            continue
        px = item.get("bbox_px")
        if not isinstance(px, list) or len(px) != 4:
            continue
        try:
            vals = [float(v) for v in px]
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in vals):
            continue
        px = [int(round(v)) for v in vals]
        x0, x1 = sorted((px[0], px[2]))
        y0, y1 = sorted((px[1], px[3]))
        px = [x0, y0, x1, y1]
        boxes.append(DomBox(
            id=f"d{len(boxes) + 1}",
            tag=str(item.get("tag") or ""),
            type=str(item.get("type") or "TEXT"),
            text=str(item.get("text") or ""),
            bbox_px=px,
            bbox=normalize_box(px, size),
        ))
    return boxes


def evaluate_isolated(page, expression: str, timeout_ms: int = EVALUATE_TIMEOUT_MS) -> Any:
    """Evaluate a JS expression in a fresh isolated world of the page's main frame over CDP.

    The page's own scripts cannot reach that world's prototypes, so a page that patches
    getBoundingClientRect or innerText in its main world does not forge the result, and the
    timeout terminates the evaluation instead of hanging the caller. Works with page JavaScript
    disabled as well. Raises RuntimeError on a JS exception; protocol errors propagate as the
    Playwright error type.
    """
    cdp = page.context.new_cdp_session(page)
    try:
        frame_id = cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
        world = cdp.send("Page.createIsolatedWorld", {"frameId": frame_id, "worldName": ISOLATED_WORLD})
        res = cdp.send("Runtime.evaluate", {
            "expression": expression, "contextId": world["executionContextId"],
            "returnByValue": True, "timeout": timeout_ms,
        })
    finally:
        cdp.detach()
    if "exceptionDetails" in res:
        detail = res["exceptionDetails"]
        text = detail.get("text") or ""
        exc = (detail.get("exception") or {}).get("description") or ""
        raise RuntimeError(f"{text} {exc}".strip() or "evaluate failed")
    return res.get("result", {}).get("value")


def extract_dom_boxes(page, size: tuple[int, int], timeout_ms: int = EVALUATE_TIMEOUT_MS) -> list[DomBox]:
    """Run DOM_BOXES_JS in an isolated world of a Playwright page; boxes are normalized to the given image size."""
    return boxes_from_raw(evaluate_isolated(page, f"({DOM_BOXES_JS})()", timeout_ms), size)
