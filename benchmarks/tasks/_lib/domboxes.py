"""Headless DOM boxes for the visual tasks' checks (stdlib + playwright only; runs in the checker venv).

The box extraction is the one JS string koda's render_page uses, DOM_BOXES_JS in src/koda/screen/dom.py, and it
runs the same way: in an isolated world over CDP with a timeout (evaluate_isolated from that module), so a page
that patches getBoundingClientRect or innerText in its own scripts cannot forge what the judge measures. The
module is loaded from that file by path: importing it as `koda.screen.dom` would run koda/screen/__init__.py,
which needs pydantic.

Render rules match render_page: chromium headless, viewport 1280x800, device scale factor 1, wait for `load`
plus 300 ms, animations and transitions disabled, and only file: requests under the rendered copy's root (plus
data:, blob: and about:) load; everything else is aborted, as render_page confines a page to its workspace.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

VIEWPORT = (1280, 800)
SETTLE_MS = 300
LAUNCH_TIMEOUT_MS = 30_000
GOTO_TIMEOUT_MS = 15_000

NO_MOTION_CSS = "*, *::before, *::after { animation: none !important; transition: none !important; }"
INJECT_STYLE_JS = ("css => { const s = document.createElement('style'); s.textContent = css; "
                   "(document.head || document.documentElement).appendChild(s); }")
DOC_HEIGHT_JS = ("Math.max(document.documentElement.scrollHeight, "
                 "document.body ? document.body.scrollHeight : 0)")
INNER_TEXT_JS = "document.body ? document.body.innerText : ''"


def koda_dir() -> Path:
    env = os.environ.get("KODA_DIR")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[3]


def _load_dom_module():
    path = koda_dir() / "src" / "koda" / "screen" / "dom.py"
    spec = importlib.util.spec_from_file_location("_koda_screen_dom", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses in dom.py look the module up by name while it executes
    spec.loader.exec_module(mod)
    return mod


_DOM = _load_dom_module()
DOM_BOXES_JS = _DOM.DOM_BOXES_JS
EVAL_TIMEOUT_MS = _DOM.EVALUATE_TIMEOUT_MS
evaluate_isolated = _DOM.evaluate_isolated


def norm(text: str) -> str:
    return " ".join((text or "").split())


def lines(text: str) -> list[str]:
    """innerText split into non-empty, whitespace-normalized lines."""
    return [n for n in (norm(ln) for ln in (text or "").splitlines()) if n]


def intersects(a: list[int], b: list[int]) -> bool:
    """Strict overlap of two [x0, y0, x1, y1] boxes (touching edges do not count)."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def within(a: list[int], b: list[int], tol: int) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@dataclass
class Rendered:
    boxes: list[dict] = field(default_factory=list)   # DOM_BOXES_JS rows: {tag, type, text, bbox_px}
    inner_text: str = ""
    doc_height: int = 0
    extra: dict = field(default_factory=dict)          # results of the caller's extra JS, by key

    def find(self, text: str, types: tuple[str, ...] = ()) -> dict | None:
        """First box whose normalized text equals `text`; with `types`, only those types, in that preference."""
        want = norm(text)
        hits = [b for b in self.boxes if norm(b.get("text", "")) == want and (not types or b.get("type") in types)]
        for t in types:
            for b in hits:
                if b.get("type") == t:
                    return b
        return hits[0] if hits else None


def _file_inside(url: str, root: Path) -> bool:
    try:
        p = Path(url2pathname(urlparse(url).path)).resolve()
    except (ValueError, OSError):
        return False
    return p == root or root in p.parents


def _only_local(route, root: Path) -> None:
    url = route.request.url
    if url.startswith(("data:", "blob:", "about:")) or (url.startswith("file:") and _file_inside(url, root)):
        route.continue_()
    else:
        route.abort("blockedbyclient")


def call(page, function_js: str, *args):
    """Call a JS function expression in the isolated world with JSON-serializable arguments."""
    argv = ", ".join(json.dumps(a, ensure_ascii=False) for a in args)
    return evaluate_isolated(page, f"({function_js})({argv})", EVAL_TIMEOUT_MS)


@contextmanager
def open_page(html: Path, *, root: Path | None = None, viewport: tuple[int, int] = VIEWPORT,
              javascript: bool = True):
    """Yield a loaded, settled Playwright page for a local HTML file.

    `root` is the directory the page may load files from (the copied workspace); it defaults to the
    file's own directory. Popups are closed and service workers and WebSockets never connect.
    """
    from playwright.sync_api import sync_playwright

    html = Path(html).resolve()
    root = (root or html.parent).resolve()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, timeout=LAUNCH_TIMEOUT_MS)
        try:
            context = browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=1,
                java_script_enabled=javascript, reduced_motion="reduce", timezone_id="UTC", locale="ko-KR",
                service_workers="block",
            )
            context.route("**/*", lambda route: _only_local(route, root))
            context.route_web_socket("**/*", lambda ws: None)
            page = context.new_page()
            context.on("page", lambda popup: popup is not page and popup.close())
            page.set_default_timeout(EVAL_TIMEOUT_MS)
            page.goto(html.as_uri(), wait_until="load", timeout=GOTO_TIMEOUT_MS)
            # add_style_tag waits for a load event that never fires when page scripts are disabled
            call(page, INJECT_STYLE_JS, NO_MOTION_CSS)
            page.wait_for_timeout(SETTLE_MS)
            yield page
        finally:
            browser.close()


def render(html: Path, *, root: Path | None = None, viewport: tuple[int, int] = VIEWPORT, javascript: bool = True,
           extra_js: dict[str, str] | None = None) -> Rendered:
    """Load the file and return its DOM boxes, body innerText, document height and any extra evaluations.

    `extra_js` values are function expressions (`() => {...}`), called in the isolated world like the
    box extraction."""
    with open_page(html, root=root, viewport=viewport, javascript=javascript) as page:
        boxes = call(page, DOM_BOXES_JS)
        text = evaluate_isolated(page, INNER_TEXT_JS, EVAL_TIMEOUT_MS)
        height = int(evaluate_isolated(page, DOC_HEIGHT_JS, EVAL_TIMEOUT_MS) or 0)
        extra = {k: call(page, js) for k, js in (extra_js or {}).items()}
    return Rendered(boxes=boxes if isinstance(boxes, list) else [], inner_text=str(text or ""),
                    doc_height=height, extra=extra)


def match_boxes(ref: list[dict], cur: list[dict], tol: int = 2) -> dict:
    """1:1 greedy match of boxes by (type, text) with every box edge within `tol` px.

    Returns the unmatched reference boxes (missing) and unmatched current boxes (extra)."""
    free = list(range(len(cur)))
    missing: list[dict] = []
    for r in ref:
        hit = None
        for i in free:
            c = cur[i]
            if c.get("type") == r.get("type") and norm(c.get("text", "")) == norm(r.get("text", "")) \
                    and within(c["bbox_px"], r["bbox_px"], tol):
                hit = i
                break
        if hit is None:
            missing.append(r)
        else:
            free.remove(hit)
    extra = [cur[i] for i in free]
    return {"matched": len(ref) - len(missing), "missing": missing, "extra": extra}


def brief(boxes: list[dict], limit: int = 10) -> list[str]:
    return [f"{b.get('type')} {b.get('text', '')[:40]!r} {b.get('bbox_px')}" for b in boxes[:limit]]
