from __future__ import annotations

import asyncio
import http.server
import importlib
import json
import sys
import threading
from pathlib import Path

import pytest

from koda.screen import store
from koda.screen.render import (
    DEFAULT_VIEWPORT,
    MAX_FULL_PAGE_HEIGHT,
    NOT_LOCAL,
    PLAYWRIGHT_MISSING,
    RenderError,
    check_target,
    derive_name,
    parse_viewport,
    render,
    sanitize_name,
    source_digest,
)
from koda.tools.screen import make_screen_tools
from koda.workspace import Workspace

pytest.importorskip("playwright.sync_api")
render_module = importlib.import_module("koda.screen.render")

_INDEX = """<!doctype html><html><head><meta charset="utf-8"><style>
body {{ margin: 0; font: 16px sans-serif; }} nav {{ height: 40px; background: #eee; }}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }} .spin {{ animation: spin 1s infinite; }}
</style><link rel="stylesheet" href="https://example.com/x.css"></head><body>
<nav><a href="#">홈</a><a href="#">메뉴</a></nav>
<h1 class="spin">제목</h1>
<p id="d"></p><p id="r"></p>
<img src="https://example.com/x.png" alt="cdn">
<iframe src="{outside}"></iframe>
<script>
document.getElementById('d').textContent = Date.now() + ' ' + new Date().toISOString() + ' ' + Date();
document.getElementById('r').textContent = String(Math.random());
console.error("boom");
setTimeout(() => {{ throw new Error("late"); }}, 10);
</script></body></html>"""

_CLEAN = "<!doctype html><html><body><h1>깨끗한 페이지</h1><p>본문</p></body></html>"
_TALL = "<!doctype html><html><body style='margin:0'><h1>위</h1><div style='height:6000px'></div><p>아래</p></body></html>"
_FRAMES = """<!doctype html><html><body>
<iframe src="inner.html"></iframe>
<iframe src="{outside}"></iframe>
</body></html>"""
_INNER = "<!doctype html><html><body><p>안쪽</p></body></html>"
_KODA_FRAME = '<!doctype html><html><body><iframe src="../.koda/sessions/s.txt"></iframe><p>본문</p></body></html>'
_FORGED = """<!doctype html><html><body><nav><a href="#">메뉴</a></nav><h1>제목</h1>
<script>
Element.prototype.getBoundingClientRect = function () { return {left: 0, top: 500, right: 100, bottom: 520, width: 100, height: 20}; };
Object.defineProperty(HTMLElement.prototype, "innerText", {get() { return "FORGED"; }});
window.addEventListener("load", () => setTimeout(() => {
  Element.prototype.getBoundingClientRect = function () { for (;;) {} };
}, 0));
</script></body></html>"""


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("ws")
    outside = tmp_path_factory.mktemp("outside") / "secret.html"
    outside.write_text("<p>SECRET</p>", encoding="utf-8")
    (root / "site").mkdir()
    (root / "site" / "index.html").write_text(_INDEX.format(outside=outside.as_uri()), encoding="utf-8")
    (root / "site" / "clean.html").write_text(_CLEAN, encoding="utf-8")
    (root / "site" / "tall.html").write_text(_TALL, encoding="utf-8")
    (root / "site" / "frames.html").write_text(_FRAMES.format(outside=outside.as_uri()), encoding="utf-8")
    (root / "site" / "inner.html").write_text(_INNER, encoding="utf-8")
    (root / "site" / "style.css").write_text("body{}", encoding="utf-8")
    (root / "site" / "koda.html").write_text(_KODA_FRAME, encoding="utf-8")
    (root / "site" / "forge.html").write_text(_FORGED, encoding="utf-8")
    (root / ".koda" / "sessions").mkdir(parents=True)
    (root / ".koda" / "sessions" / "s.txt").write_text("SESSION-SECRET", encoding="utf-8")
    return Workspace(root)


@pytest.fixture(scope="module")
def rendered(site):
    store.clear_registry()
    return render(site, "site/index.html", name="before")


def test_render_writes_png_dom_and_meta(site, rendered):
    renders = site.root / ".koda" / "renders"
    assert rendered.name == "before"
    assert rendered.png == renders / "before.png" and rendered.png.is_file()
    assert rendered.rel_png == ".koda/renders/before.png" and rendered.rel_dom == ".koda/renders/before.dom.json"
    assert (rendered.width, rendered.height) == (1280, 800) and rendered.viewport == (1280, 800)
    assert store.image_size(rendered.png) == (1280, 800)

    doc = store.load_dom(rendered.dom)
    assert (doc.size.w, doc.size.h) == (1280, 800) and doc.image == "before.png"
    assert doc.boxes == rendered.boxes
    assert {(b.type, b.text) for b in doc.boxes} >= {("HEADING", "제목"), ("LINK", "홈"), ("LINK", "메뉴")}
    assert next(b for b in doc.boxes if b.text == "제목").bbox_px[1] >= 40

    meta = store.load_meta(rendered.meta)
    assert meta.target == "site/index.html"
    assert meta.source_digest == source_digest(site) and len(meta.source_digest) == 64
    assert meta.viewport == "1280x800" and meta.full_page is False
    assert meta.png_sha256 == store.file_sha256(rendered.png)
    assert meta.ordinal >= 1 and meta.created_at.startswith("20")
    for f in (rendered.png, rendered.dom, rendered.meta):
        assert store.registry_status(f) == "fresh"


def test_console_errors_collected_and_blocked_resources_excluded(rendered):
    assert "boom" in rendered.console_errors
    assert any("late" in e for e in rendered.console_errors)
    assert not any("Failed to load resource" in e for e in rendered.console_errors)
    # the CDN stylesheet, the CDN image and the file iframe outside the workspace
    assert rendered.blocked == 3


def test_dates_and_random_are_pinned(rendered):
    texts = {b.text for b in rendered.boxes}
    assert any(t.startswith("1767225600000 2026-01-01T00:00:00.000Z") for t in texts)
    assert any(t.startswith("0.2523451747838408") for t in texts)


def test_render_is_deterministic(site, rendered):
    again = render(site, "site/index.html", name="before2")
    assert store.file_sha256(again.png) == store.file_sha256(rendered.png)


def test_workspace_file_iframe_allowed_and_outside_one_blocked(site):
    r = render(site, "site/frames.html", viewport="400x300")
    assert r.blocked == 1 and r.console_errors == []
    assert r.name == "site_frames"

    # .koda/ is koda's own state: a page may not pull sessions or memory into a frame the parser would read.
    r = render(site, "site/koda.html", viewport="400x300")
    assert r.blocked == 1 and [b.text for b in r.boxes] == ["본문"]
    renders_png = (site.root / ".koda" / "renders" / "before.png").as_uri()
    assert render_module._file_allowed(site, renders_png)
    assert not render_module._file_allowed(site, (site.root / ".koda" / "sessions" / "s.txt").as_uri())
    assert not render_module._file_allowed(site, (site.root / ".KODA" / "memory.md").as_uri())


def test_source_digest_follows_the_whole_tree_not_the_target_file(site):
    # Spec 7.9: a refactor that touches only the stylesheet must not read as 소스 동일.
    css = site.root / "site" / "style.css"
    before = render(site, "site/clean.html", name="digest_a")
    css.write_text("h1 { margin-top: 40px; }", encoding="utf-8")
    try:
        after = render(site, "site/clean.html", name="digest_b")
    finally:
        css.write_text("body{}", encoding="utf-8")
    assert store.load_meta(before.meta).source_digest != store.load_meta(after.meta).source_digest
    again = render(site, "site/clean.html", name="digest_c")
    assert store.load_meta(again.meta).source_digest == store.load_meta(before.meta).source_digest
    # .koda/renders/ written by the renders themselves does not move the digest
    assert store.load_meta(again.meta).source_digest == source_digest(site)


def test_dom_boxes_come_from_an_isolated_world(site):
    # The page forges getBoundingClientRect and innerText in its main world and then makes the former loop
    # forever; dom.json still holds the real boxes and the render returns.
    r = render(site, "site/forge.html", viewport="800x600")
    assert [(b.type, b.text) for b in r.boxes] == [("LINK", "메뉴"), ("HEADING", "제목")]
    assert all(b.bbox_px[3] < 100 for b in r.boxes)


def test_rerender_overwrites_and_drops_the_stale_parse(site):
    first = render(site, "site/clean.html", name="again")
    stale = site.root / ".koda" / "renders" / "again.parse.json"
    stale.write_text("{}", encoding="utf-8")
    old_meta = store.load_meta(first.meta)

    second = render(site, "site/clean.html", name="again")

    assert not stale.exists()
    assert second.png == first.png and second.png.is_file()
    assert store.load_meta(second.meta).ordinal > old_meta.ordinal


def test_full_page_caps_the_document_height(site):
    short = render(site, "site/clean.html", full_page=True, name="short")
    assert short.height_capped is False and short.height == 800 and store.load_meta(short.meta).full_page is True

    tall = render(site, "site/tall.html", full_page=True, name="tall")
    assert tall.height_capped is True
    assert (tall.width, tall.height) == (1280, MAX_FULL_PAGE_HEIGHT)
    assert store.image_size(tall.png) == (1280, MAX_FULL_PAGE_HEIGHT)
    assert any(b.text == "위" for b in tall.boxes)


def test_png_over_the_limit_is_removed(site, monkeypatch):
    monkeypatch.setattr(render_module, "MAX_PNG_BYTES", 10)
    with pytest.raises(RenderError, match="5MB"):
        render(site, "site/clean.html", name="huge")
    assert not (site.root / ".koda" / "renders" / "huge.png").exists()


def test_render_inside_a_running_event_loop_moves_to_a_thread(site):
    async def main():
        return render(site, "site/clean.html", name="looped")
    r = asyncio.run(main())
    assert r.png.is_file() and r.name == "looped"


def test_missing_playwright_gives_the_install_hint(site, monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(RenderError) as exc:
        render(site, "site/clean.html", name="nope")
    assert str(exc.value) == PLAYWRIGHT_MISSING
    assert "uv sync --extra tui --extra visual" in PLAYWRIGHT_MISSING and "playwright install chromium" in PLAYWRIGHT_MISSING

    tool = {t.name: t for t in make_screen_tools(site, None, "http://x/props")}["render_page"]
    assert tool.invoke({"target": "site/clean.html"}) == f"오류: {PLAYWRIGHT_MISSING}"


def test_rejects_non_local_urls_non_html_and_escapes(site):
    for bad in ("https://example.com", "http://evil.com/", "http://localhost.evil.com/", "ftp://localhost/",
                "file:///etc/passwd", "https://localhost/"):
        with pytest.raises(RenderError, match=NOT_LOCAL):
            check_target(site, bad)
    with pytest.raises(RenderError, match="HTML"):
        check_target(site, "site/style.css")
    with pytest.raises(RenderError, match="워크스페이스 밖"):
        check_target(site, "../outside.html")
    with pytest.raises(RenderError, match="없다"):
        check_target(site, "site/missing.html")
    with pytest.raises(RenderError, match=".koda"):
        check_target(site, ".koda/renders/x.html")
    with pytest.raises(RenderError):
        check_target(site, "")
    for ok in ("http://localhost:5173/", "http://127.0.0.1:3000/app", "http://localhost"):
        assert check_target(site, ok).kind == "url"
    t = check_target(site, "site/index.html")
    assert t.kind == "file" and t.url.startswith("file://")


def test_viewport_rules():
    assert parse_viewport(DEFAULT_VIEWPORT) == (1280, 800)
    assert parse_viewport("1600×900") == (1600, 900) and parse_viewport(" 200 X 200 ") == (200, 200)
    assert parse_viewport("2000x1200") == (2000, 1200)
    for bad in ("100x100", "2001x200", "200x2001", "2000x2000", "abc", "1280", "1280x", ""):
        with pytest.raises(RenderError, match="뷰포트"):
            parse_viewport(bad)


def test_name_rules(site):
    assert derive_name(site, check_target(site, "site/index.html")) == "site_index"
    assert derive_name(site, check_target(site, "site/index.html"), "before") == "before"
    assert derive_name(site, check_target(site, "http://localhost:5173")) == "localhost_5173"
    assert derive_name(site, check_target(site, "http://localhost:3000/app")) == "localhost_3000_app"
    assert derive_name(site, check_target(site, "http://127.0.0.1/a/b/")) == "127.0.0.1_80_a_b"
    assert sanitize_name("my shot!") == "my_shot_"
    assert sanitize_name("한글 이름") == "page" or sanitize_name("한글 이름") == "_____"
    assert sanitize_name("../x") == "x" and sanitize_name("...") == "page" and sanitize_name("") == "page"
    assert sanitize_name("a.b-c_d") == "a.b-c_d"
    # src__ is the parse-file prefix of workspace images, so a render may not claim it
    assert sanitize_name("src__design") == "design" and sanitize_name("src__src__x") == "x" and sanitize_name("src__") == "page"
    assert derive_name(site, check_target(site, "site/index.html"), "src__design") == "design"


def test_render_page_tool_output_format(site):
    tool = {t.name: t for t in make_screen_tools(site, None, "http://x/props")}["render_page"]

    out = tool.invoke({"target": "site/index.html", "name": "tool"})
    lines = out.splitlines()

    assert lines[0].startswith("렌더 완료 · .koda/renders/tool.png · 1280x800 · 콘솔 에러 2건 · 외부 차단 3건 · DOM 요소 ")
    assert lines[1] == f"파일: {(site.root / '.koda' / 'renders' / 'tool.png').as_uri()}"
    assert lines[2].startswith("DOM 요소 ") and lines[2].endswith(" · .koda/renders/tool.dom.json")
    assert lines[3] == "콘솔: boom" and lines[4].endswith("late")
    assert len(lines) == 5

    capped = tool.invoke({"target": "site/tall.html", "full_page": True, "viewport": "600x400"})
    assert capped.splitlines()[0].startswith(f"렌더 완료 · .koda/renders/site_tall.png · 600x{MAX_FULL_PAGE_HEIGHT} · 콘솔 에러 0건 · 외부 차단 0건 · DOM 요소 ")
    assert capped.splitlines()[0].endswith("높이 상한 적용")

    assert tool.invoke({"target": "https://example.com"}) == f"오류: {NOT_LOCAL}"
    assert tool.invoke({"target": "site/clean.html", "viewport": "10x10"}).startswith("오류: 뷰포트")


@pytest.fixture(scope="module")
def leak():
    """A second local origin that records every request it receives: anything landing here escaped the sandbox."""
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"leaked")

        do_POST = do_GET

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], hits
    httpd.shutdown()


@pytest.fixture(scope="module")
def served(site, leak):
    leak_port = leak[0]

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

        def do_GET(self):
            if self.path == "/redirect-out":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{leak_port}/redirect-leak")
                self.end_headers()
            elif self.path == "/redirect-in":
                self.send_response(302)
                self.send_header("Location", "/served.html")
                self.end_headers()
            else:
                super().do_GET()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: Handler(*a, directory=str(site.root / "site"), **k))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def test_localhost_url_allows_same_origin_only(site, served):
    (site.root / "site" / "served.html").write_text(
        "<!doctype html><html><head><link rel='stylesheet' href='style.css'>"
        "<script src='http://127.0.0.1:1/x.js'></script></head><body><h1>served</h1></body></html>",
        encoding="utf-8",
    )
    r = render(site, f"http://127.0.0.1:{served}/served.html")

    assert r.name == f"127.0.0.1_{served}_served.html"
    assert r.blocked == 1 and r.console_errors == []
    assert any(b.text == "served" for b in r.boxes)
    assert store.load_meta(r.meta).source_digest == source_digest(site)      # a URL target has the tree digest too


def test_popup_service_worker_websocket_and_redirect_cannot_reach_another_origin(site, served, leak):
    # Spec 7.2: every request off the allowed origin is aborted. page.route alone misses a popup's first
    # request, service worker fetches, a server-side 302 and WebSocket handshakes.
    leak_port, hits = leak
    pages = {
        "popup.html": f"<!doctype html><html><body><script>window.open('http://127.0.0.1:{leak_port}/popup-leak');</script></body></html>",
        "sw.js": f"self.addEventListener('install', e => e.waitUntil(fetch('http://127.0.0.1:{leak_port}/sw-leak').catch(() => {{}})));",
        "sw.html": "<!doctype html><html><body><p>sw</p><script>navigator.serviceWorker.register('sw.js').catch(() => {});</script></body></html>",
        "ws.html": f"<!doctype html><html><body><p>ws</p><script>new WebSocket('ws://127.0.0.1:{leak_port}/ws-leak');</script></body></html>",
    }
    for name, text in pages.items():
        (site.root / "site" / name).write_text(text, encoding="utf-8")

    for target in ("site/popup.html", f"http://127.0.0.1:{served}/popup.html"):
        hits.clear()
        r = render(site, target, name="popup")
        assert r.blocked == 1 and hits == []
    hits.clear()
    r = render(site, f"http://127.0.0.1:{served}/sw.html", name="sw")
    assert hits == [] and [b.text for b in r.boxes] == ["sw"]
    for target in ("site/ws.html", f"http://127.0.0.1:{served}/ws.html"):
        hits.clear()
        r = render(site, target, name="ws")
        assert r.blocked == 1 and hits == [] and r.console_errors == []

    hits.clear()
    with pytest.raises(RenderError, match="페이지를 열 수 없다"):
        render(site, f"http://127.0.0.1:{served}/redirect-out", name="redir")
    assert hits == []
    r = render(site, f"http://127.0.0.1:{served}/redirect-in", name="redir")       # same-origin redirects still work
    assert any(b.text == "served" for b in r.boxes) and r.blocked == 1                # served.html's off-origin script only


def test_unreachable_localhost_is_an_error(site):
    with pytest.raises(RenderError, match="페이지를 열 수 없다"):
        render(site, "http://127.0.0.1:1/", name="down")
    assert not (site.root / ".koda" / "renders" / "down.png").exists()


def test_dom_json_is_plain_json(site, rendered):
    data = json.loads(rendered.dom.read_text(encoding="utf-8"))
    assert set(data["boxes"][0]) == {"id", "tag", "type", "text", "bbox_px", "bbox"}


def test_documents_served_without_an_html_content_type_still_render_as_html(site, served):
    # A live run hit all three shapes: a self-started server that labelled the page text/plain (the markup
    # rendered as one text block), application/octet-stream (Chromium downloaded it, "Download is starting")
    # and no header at all (UTF-8 decoded as Latin-1). The document is always fulfilled as text/html; the
    # original value is kept on the result and the tool prints it as a warning.
    import http.server
    import threading

    from koda.tools.screen import make_screen_tools

    html = "<!doctype html><html><body><h1>plain</h1><p>안녕</p></body></html>"
    (site.root / "site" / "plain.txt").write_text(html, encoding="utf-8")
    (site.root / "site" / "page.bin").write_text(html, encoding="utf-8")

    r = render(site, f"http://127.0.0.1:{served}/plain.txt")
    assert r.content_type.startswith("text/plain") and r.content_type_overridden
    assert [b.text for b in r.boxes if b.text] == ["plain", "안녕"]
    r = render(site, f"http://127.0.0.1:{served}/page.bin")
    assert r.content_type.startswith("application/octet-stream") and r.content_type_overridden
    assert [b.text for b in r.boxes if b.text] == ["plain", "안녕"]
    r = render(site, f"http://127.0.0.1:{served}/served.html")
    assert "html" in r.content_type and not r.content_type_overridden

    class NoType(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

        def do_GET(self):
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), NoType)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        r = render(site, f"http://127.0.0.1:{httpd.server_address[1]}/")
        assert r.content_type == "" and r.content_type_overridden
        assert [b.text for b in r.boxes if b.text] == ["plain", "안녕"]
    finally:
        httpd.shutdown()

    tool = {t.name: t for t in make_screen_tools(site, None, "http://127.0.0.1:1/props")}["render_page"]
    out = tool.invoke({"target": f"http://127.0.0.1:{served}/plain.txt", "name": "plain"})
    assert out.splitlines()[0].startswith("렌더 완료 · .koda/renders/plain.png")
    assert any(line.startswith("경고: 서버가 문서를 content-type text/plain") for line in out.splitlines())
    out = tool.invoke({"target": f"http://127.0.0.1:{served}/served.html", "name": "served"})
    assert not any(line.startswith("경고:") for line in out.splitlines())
