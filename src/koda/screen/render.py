from __future__ import annotations

import asyncio
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import url2pathname

from ..diffscan import snapshot
from ..workspace import SandboxError, Workspace
from . import store
from .dom import EVALUATE_TIMEOUT_MS, DomBox, evaluate_isolated, extract_dom_boxes

DEFAULT_VIEWPORT = "1280x800"
MIN_SIDE = 200
MAX_SIDE = 2000
MAX_AREA = 2_400_000
MAX_FULL_PAGE_HEIGHT = 4000
MAX_PNG_BYTES = store.MAX_IMAGE_BYTES
LAUNCH_TIMEOUT_MS = 30_000
GOTO_TIMEOUT_MS = 15_000
SCREENSHOT_TIMEOUT_MS = 15_000
SETTLE_MS = 300
DOC_HEIGHT_JS = "Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)"
MAX_CONSOLE_ERRORS = 50
MAX_CONSOLE_CHARS = 300
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
HTML_EXTENSIONS = frozenset({".html", ".htm"})

PLAYWRIGHT_MISSING = (
    "playwright 가 설치되어 있지 않다. uv sync --extra tui --extra visual 뒤 "
    ".venv/bin/python -m playwright install chromium 을 실행하라"
)
NOT_LOCAL = "로컬 파일과 localhost 만 렌더한다"

# 2026-01-01T00:00:00Z. Pages that print the date or roll dice render the same bytes every time.
FIXED_TIME_MS = 1_767_225_600_000
INIT_SCRIPT = f"""(() => {{
  const fixed = {FIXED_TIME_MS};
  const NativeDate = Date;
  const FixedDate = function (...args) {{
    if (!(this instanceof FixedDate)) return new NativeDate(fixed).toString();
    return args.length ? new NativeDate(...args) : new NativeDate(fixed);
  }};
  FixedDate.prototype = NativeDate.prototype;
  FixedDate.now = () => fixed;
  FixedDate.UTC = NativeDate.UTC;
  FixedDate.parse = NativeDate.parse;
  window.Date = FixedDate;
  let seed = 42;
  Math.random = () => {{
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  }};
}})();"""


class RenderError(Exception):
    """A render that could not be made. The message is the Korean text the tool returns after '오류: '."""


@dataclass
class RenderTarget:
    kind: str  # "file" or "url"
    target: str
    url: str
    path: Path | None = None


@dataclass
class RenderResult:
    name: str
    png: Path
    dom: Path
    meta: Path
    width: int
    height: int
    viewport: tuple[int, int]
    full_page: bool = False
    height_capped: bool = False
    console_errors: list[str] = field(default_factory=list)
    blocked: int = 0
    boxes: list[DomBox] = field(default_factory=list)
    content_type: str = ""  # Content-Type the server sent for the document, before any override
    content_type_overridden: bool = False  # the document was fulfilled as text/html because of that value

    @property
    def rel_png(self) -> str:
        return f"{store.RENDERS_DIR}/{self.name}{store.PNG_SUFFIX}"

    @property
    def rel_dom(self) -> str:
        return f"{store.RENDERS_DIR}/{self.name}{store.DOM_SUFFIX}"


def parse_viewport(viewport: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", viewport or "")
    if not m:
        raise RenderError(f"뷰포트 형식이 잘못됐다(WxH, 예: {DEFAULT_VIEWPORT}): {viewport}")
    w, h = int(m.group(1)), int(m.group(2))
    if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE) or w * h > MAX_AREA:
        raise RenderError(
            f"뷰포트는 가로·세로 각 {MIN_SIDE}~{MAX_SIDE}, 면적 {MAX_AREA:,}px 이하여야 한다: {w}x{h}"
        )
    return w, h


def sanitize_name(name: str) -> str:
    """Keep [A-Za-z0-9._-], drop leading dots so no name hides itself or climbs, fall back to 'page'.

    The src__ prefix is reserved for parses of workspace images (store.SOURCE_PREFIX), so a render
    named src__design would share its parse file with design.png; the prefix is stripped.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name or "").lstrip("._")
    while cleaned.startswith(store.SOURCE_PREFIX):
        cleaned = cleaned[len(store.SOURCE_PREFIX):].lstrip("._")
    return cleaned or "page"


def check_target(ws: Workspace, target: str) -> RenderTarget:
    """A workspace HTML file (opened as file://) or an http URL on 127.0.0.1 or localhost."""
    target = (target or "").strip()
    if not target:
        raise RenderError("렌더 대상이 비어 있다")
    if "://" in target:
        u = urlparse(target)
        if u.scheme != "http" or (u.hostname or "").lower() not in LOCAL_HOSTS:
            raise RenderError(NOT_LOCAL)
        return RenderTarget(kind="url", target=target, url=target)
    try:
        path = ws.resolve(target)
    except SandboxError as e:
        raise RenderError(str(e)) from e
    if path.suffix.lower() not in HTML_EXTENSIONS:
        raise RenderError(f"HTML 파일이 아니다(.html 또는 .htm): {target}")
    if not path.is_file():
        raise RenderError(f"파일이 없다: {target}")
    return RenderTarget(kind="file", target=target, url=path.as_uri(), path=path)


def source_digest(ws: Workspace) -> str:
    """sha256 over the sorted (path, sha) pairs of diffscan.snapshot(ws.root): the state of every
    source file at render time, with .koda, .git, .venv and node_modules left out (spec 7.9)."""
    snap = snapshot(ws.root)
    h = hashlib.sha256()
    for rel in sorted(snap):
        h.update(f"{rel}\0{snap[rel]['sha']}\n".encode("utf-8"))
    return h.hexdigest()


def derive_name(ws: Workspace, target: RenderTarget, name: str = "") -> str:
    """Explicit name, else site/index.html -> site_index and http://localhost:3000/app -> localhost_3000_app."""
    if name and name.strip():
        return sanitize_name(name.strip())
    if target.kind == "file":
        rel = target.path.relative_to(ws.root).with_suffix("")
        return sanitize_name("_".join(rel.parts))
    u = urlparse(target.url)
    parts = [u.hostname or "localhost", str(u.port or 80)]
    parts += [seg for seg in (u.path or "").split("/") if seg]
    return sanitize_name("_".join(parts))


def _file_allowed(ws: Workspace, url: str) -> bool:
    """A file: URL under the workspace root. .koda/ is koda's own state (sessions, memory, artifacts)
    and stays hidden from the page as Workspace.resolve hides it from the tools; only .koda/renders/,
    the one folder the screen tools read back, may be loaded."""
    try:
        p = Path(url2pathname(urlparse(url).path)).resolve()
    except (ValueError, OSError):
        return False
    if not (p == ws.root or ws.root in p.parents):
        return False
    parts = p.relative_to(ws.root).parts
    if parts and parts[0].lower() == ".koda":
        return len(parts) >= 2 and parts[1] == "renders"
    return True


def _origin(url: str) -> tuple[str, str, int | None]:
    u = urlparse(url)
    return (u.scheme, (u.hostname or "").lower(), u.port or (80 if u.scheme == "http" else 443 if u.scheme == "https" else None))


def _allow_request(ws: Workspace, target: RenderTarget, url: str) -> bool:
    if url.startswith(("data:", "blob:", "about:")):
        return True
    if target.kind == "file":
        return url.startswith("file:") and _file_allowed(ws, url)
    return url.startswith(("http:", "https:")) and _origin(url) == _origin(target.url)


def _first_line(text: str) -> str:
    return (str(text).strip().splitlines() or [""])[0][:MAX_CONSOLE_CHARS]


def _render(ws: Workspace, target: RenderTarget, name: str, viewport: tuple[int, int], full_page: bool) -> RenderResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RenderError(PLAYWRIGHT_MISSING) from e

    vw, vh = viewport
    blocked: list[str] = []
    console: list[tuple[str, str]] = []
    page_errors: list[str] = []
    document_type: dict[str, str] = {}
    height_capped = False
    digest = source_digest(ws)

    def on_route(route):
        url = route.request.url
        if not _allow_request(ws, target, url):
            blocked.append(url)
            route.abort("blockedbyclient")
            return
        if not url.startswith(("http:", "https:")):
            route.continue_()
            return
        # The server answers through koda, not the browser, so a same-origin 3xx cannot bounce the
        # page to another origin: the Location is checked like any other request before it is followed.
        try:
            response = route.fetch(max_redirects=0)
        except Exception:
            route.abort("connectionfailed")
            return
        location = response.headers.get("location")
        if 300 <= response.status < 400 and location:
            dest = urljoin(url, location)
            if not _allow_request(ws, target, dest):
                blocked.append(dest)
                route.abort("blockedbyclient")
                return
        if route.request.resource_type == "document" and response.status < 300:
            # A static server that labels the page application/octet-stream makes Chromium download it
            # ("Download is starting"), text/plain shows the markup as one text block, and no header
            # decodes UTF-8 as Latin-1. The caller asked for a page, so documents are always served as HTML.
            ct = response.headers.get("content-type", "")
            if "html" not in ct.lower():
                document_type["original"] = ct
                route.fulfill(response=response, content_type="text/html; charset=utf-8")
                return
        route.fulfill(response=response)

    def on_web_socket(ws_route):
        # Not calling connect_to_server leaves the socket mocked, so nothing reaches any server.
        blocked.append(ws_route.url)

    def on_console(msg):
        if msg.type == "error":
            location = msg.location or {}
            console.append((msg.text, str(location.get("url") or "")))

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, timeout=LAUNCH_TIMEOUT_MS)
        except Exception as e:
            detail = _first_line(e)
            hint = f" {PLAYWRIGHT_MISSING}" if "xecutable" in detail or "install" in detail else ""
            raise RenderError(f"브라우저 실행 실패({type(e).__name__}): {detail}.{hint}") from e
        try:
            context = browser.new_context(
                viewport={"width": vw, "height": vh}, device_scale_factor=1, timezone_id="UTC", locale="en-US",
                service_workers="block",
            )
            # context.route sees a popup's first request; page.route does not.
            context.route("**/*", on_route)
            context.route_web_socket("**/*", on_web_socket)
            page = context.new_page()
            context.on("page", lambda popup: popup is not page and popup.close())
            page.add_init_script(INIT_SCRIPT)
            page.on("console", on_console)
            page.on("pageerror", lambda err: page_errors.append(_first_line(err)))
            try:
                response = page.goto(target.url, wait_until="load", timeout=GOTO_TIMEOUT_MS)
            except Exception as e:
                if "Download is starting" in str(e):
                    raise RenderError("브라우저가 대상을 다운로드로 처리했다. HTML 문서가 아니거나 서버가 "
                                      "Content-Disposition: attachment 를 보낸다") from e
                raise RenderError(f"페이지를 열 수 없다({type(e).__name__}): {_first_line(e)}") from e
            if "original" in document_type:
                content_type = document_type["original"].strip()
            else:
                content_type = (response.headers.get("content-type", "") if response else "").strip()
            page.wait_for_timeout(SETTLE_MS)
            shot: dict = {
                "full_page": full_page, "scale": "css", "animations": "disabled", "caret": "hide",
                "timeout": SCREENSHOT_TIMEOUT_MS,
            }
            if full_page:
                doc_height = _evaluate(page, DOC_HEIGHT_JS)
                if isinstance(doc_height, (int, float)) and doc_height > MAX_FULL_PAGE_HEIGHT:
                    height_capped = True
                    shot["clip"] = {"x": 0, "y": 0, "width": vw, "height": MAX_FULL_PAGE_HEIGHT}
            try:
                png = page.screenshot(**shot)
            except Exception as e:
                raise RenderError(f"스크린샷 실패({type(e).__name__}): {_first_line(e)}") from e
            size = store.png_size(png) or (vw, vh)
            try:
                boxes = extract_dom_boxes(page, size, EVALUATE_TIMEOUT_MS)
            except Exception as e:
                raise RenderError(f"DOM 추출 실패({type(e).__name__}): {_first_line(e)}") from e
        finally:
            browser.close()

    if len(png) > MAX_PNG_BYTES:
        for path in (store.png_path(ws, name), store.dom_path(ws, name), store.meta_path(ws, name),
                     store.render_path(ws, name, store.PARSE_SUFFIX)):
            path.unlink(missing_ok=True)
        raise RenderError(f"렌더 결과가 5MB 를 넘는다({size[0]}x{size[1]}). 뷰포트를 줄여라")

    blocked_set = set(blocked)
    errors: list[str] = []
    for text, url in console:
        if "ERR_BLOCKED_BY_CLIENT" in text:
            continue
        if text.startswith("Failed to load resource") and url in blocked_set:
            continue
        errors.append(_first_line(text))
    errors += page_errors
    errors = errors[:MAX_CONSOLE_ERRORS]

    png_file = store.save_png(ws, name, png)
    dom_file = store.save_dom(ws, name, boxes, size)
    meta = store.RenderMeta(
        target=target.target, source_digest=digest, viewport=f"{vw}x{vh}", full_page=full_page,
        png_sha256=store.file_sha256(png_file), ordinal=store.next_ordinal(),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    meta_file = store.save_meta(ws, name, meta)
    # Any parse of the previous render under this name describes bytes that no longer exist.
    store.render_path(ws, name, store.PARSE_SUFFIX).unlink(missing_ok=True)
    return RenderResult(
        name=name, png=png_file, dom=dom_file, meta=meta_file, width=size[0], height=size[1], viewport=viewport,
        full_page=full_page, height_capped=height_capped, console_errors=errors, blocked=len(blocked), boxes=boxes,
        content_type=content_type, content_type_overridden="original" in document_type,
    )


def _evaluate(page, expression: str):
    """Isolated-world evaluation with the timeout, turned into a RenderError on failure."""
    try:
        return evaluate_isolated(page, expression, EVALUATE_TIMEOUT_MS)
    except Exception as e:
        raise RenderError(f"페이지 평가 실패({type(e).__name__}): {_first_line(e)}") from e


def render(ws: Workspace, target: str, viewport: str = DEFAULT_VIEWPORT, name: str = "", full_page: bool = False) -> RenderResult:
    """Render a workspace HTML file or a localhost URL headlessly into .koda/renders/<name>.{png,dom.json,meta.json}.

    Raises RenderError with the message to show. The sync Playwright API refuses to run on a thread
    that has a running event loop, so in that case the work moves to a helper thread.
    """
    checked = check_target(ws, target)
    size = parse_viewport(viewport)
    final_name = derive_name(ws, checked, name)
    store.renders_dir(ws)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _render(ws, checked, final_name, size, full_page)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_render, ws, checked, final_name, size, full_page).result()
