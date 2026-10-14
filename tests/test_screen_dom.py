from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import koda.screen.dom as dom_module
from koda.screen.dom import (
    DOM_BOXES_JS,
    INPUT_TYPES,
    MAX_DOM_BOXES,
    TAG_TYPES,
    DomBox,
    boxes_from_raw,
    dom_type,
    evaluate_isolated,
    extract_dom_boxes,
    normalize_box,
)

DOM_PY = Path(dom_module.__file__)


def test_module_imports_only_the_standard_library():
    # The benchmark checker loads this file in a venv that has only playwright.
    names = []
    for node in ast.walk(ast.parse(DOM_PY.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append("." * node.level + (node.module or ""))
    assert names
    assert set(names) <= {"__future__", "json", "math", "dataclasses", "typing"}


def test_js_constant_loads_from_the_file_without_importing_koda():
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('standalone_dom', {str(DOM_PY)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "assert not [m for m in sys.modules if m == 'koda' or m.startswith('koda.')], 'koda got imported'\n"
        "assert 'pydantic' not in sys.modules\n"
        "assert mod.DOM_BOXES_JS.startswith('() =>') and '\"h1\": \"HEADING\"' in mod.DOM_BOXES_JS\n"
        "print('standalone ok')\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "standalone ok" in out.stdout


def test_tag_mapping_matches_spec():
    assert dom_type("h1") == "HEADING" and dom_type("H6") == "HEADING"
    assert dom_type("a") == "LINK"
    assert dom_type("button") == "BUTTON"
    assert dom_type("div", role="button") == "BUTTON"
    assert dom_type("input", "checkbox") == "CHECKBOX"
    assert dom_type("input", "radio") == "RADIO"
    assert dom_type("input", "submit") == "BUTTON"
    assert dom_type("input") == "INPUT" and dom_type("input", "email") == "INPUT"
    assert dom_type("textarea") == "INPUT"
    assert dom_type("select") == "DROPDOWN"
    assert dom_type("img") == "IMAGE" and dom_type("svg") == "IMAGE"
    assert dom_type("nav") == "NAV"
    assert dom_type("li") == "LIST_ITEM"
    assert dom_type("span") == "TEXT" and dom_type("p") == "TEXT"
    # The JS carries the same tables, so the checker and koda cannot drift apart.
    for tag, t in TAG_TYPES.items():
        assert f'"{tag}": "{t}"' in DOM_BOXES_JS
    for kind, t in INPUT_TYPES.items():
        assert f'"{kind}": "{t}"' in DOM_BOXES_JS
    assert str(MAX_DOM_BOXES) in DOM_BOXES_JS


def test_normalize_box_scales_and_clips():
    assert normalize_box([640, 400, 1280, 800], (1280, 800)) == [500, 500, 1000, 1000]
    assert normalize_box([-10, 0, 1300, 10], (1280, 800)) == [0, 0, 1000, 12]
    assert normalize_box([1, 2, 3, 4], (0, 0)) == [1, 2, 3, 4]


def test_boxes_from_raw_assigns_ids_sorts_flipped_and_drops_malformed():
    raw = [
        {"tag": "h1", "type": "HEADING", "text": "제목", "bbox_px": [420, 180, 40, 120]},
        {"tag": "p", "type": "TEXT", "text": None, "bbox_px": [0, 0, 10]},
        "junk",
        {"tag": "a", "type": "LINK", "text": "홈", "bbox_px": [0.4, 0.6, 60.2, 30]},
    ]
    boxes = boxes_from_raw(raw, (1280, 800))
    assert [(b.id, b.tag, b.type, b.text, b.bbox_px) for b in boxes] == [
        ("d1", "h1", "HEADING", "제목", [40, 120, 420, 180]),
        ("d2", "a", "LINK", "홈", [0, 1, 60, 30]),
    ]
    assert boxes[0].bbox == [31, 150, 328, 225]
    assert boxes_from_raw(None, (1, 1)) == []
    assert DomBox(**boxes[0].to_dict()) == boxes[0]


def test_boxes_from_raw_drops_infinite_and_nan_coordinates():
    # A page can hand back Infinity or NaN from a patched getBoundingClientRect; that entry is dropped
    # instead of failing the whole render with an OverflowError.
    inf, nan = float("inf"), float("nan")
    raw = [
        {"tag": "p", "type": "TEXT", "text": "inf", "bbox_px": [inf, 0, 10, 10]},
        {"tag": "p", "type": "TEXT", "text": "nan", "bbox_px": [0, nan, 10, 10]},
        {"tag": "p", "type": "TEXT", "text": "neg", "bbox_px": [0, 0, 10, -inf]},
        {"tag": "p", "type": "TEXT", "text": "ok", "bbox_px": [0, 0, 10, 10]},
    ]
    assert [b.text for b in boxes_from_raw(raw, (100, 100))] == ["ok"]


_HTML = """<!doctype html><html><head><style>
body { margin: 0; font: 16px sans-serif; }
nav { height: 40px; } nav a { margin-right: 8px; }
h1 { margin: 20px 0 0 10px; }
.hidden { display: none; } .ghost { opacity: 0; } .invis { visibility: hidden; }
</style></head><body>
<nav><a href="#">홈</a><a href="#">메뉴</a></nav>
<h1>제목 <b>굵게</b></h1>
<p>본문   줄바꿈
문단</p>
<button>받기</button>
<div role="button">역할 버튼</div>
<input type="checkbox"><input type="radio"><input placeholder="검색">
<select><option>옵션 A</option><option>옵션 B</option></select>
<textarea placeholder="메모"></textarea>
<img src="x.png" alt="로고" width="30" height="30">
<svg width="20" height="20" aria-label="아이콘"><rect width="20" height="20"/></svg>
<ul><li>항목 하나</li></ul>
<span class="hidden">숨김</span><span class="ghost">투명</span><span class="invis">비가시</span>
<div class="ghost"><span>부모가 투명</span></div>
<script>document.title = "x"</script>
</body></html>"""

_FORGED = """<!doctype html><html><body><nav><a href="#">메뉴</a></nav><h1>제목</h1>
<script>
Element.prototype.getBoundingClientRect = function () { return {left: 0, top: 500, right: 100, bottom: 520, width: 100, height: 20}; };
Object.defineProperty(HTMLElement.prototype, "innerText", {get() { return "FORGED"; }});
window.addEventListener("load", () => setTimeout(() => {
  Element.prototype.getBoundingClientRect = function () { for (;;) {} };
}, 0));
</script></body></html>"""


@pytest.fixture(scope="module")
def browser():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


@pytest.fixture(scope="module")
def dom_boxes(browser):
    page = browser.new_page(viewport={"width": 600, "height": 400}, device_scale_factor=1)
    page.set_content(_HTML, wait_until="load")
    boxes = extract_dom_boxes(page, (600, 400))
    page.context.close()
    return boxes


def test_isolated_world_ignores_page_patches_and_times_out(browser):
    # The page forges getBoundingClientRect and innerText in its main world and then turns the former into an
    # infinite loop; the isolated world sees neither, with page JavaScript on or off.
    for javascript in (True, False):
        context = browser.new_context(viewport={"width": 800, "height": 600}, java_script_enabled=javascript)
        page = context.new_page()
        page.set_content(_FORGED, wait_until="load")
        page.wait_for_timeout(50)
        boxes = extract_dom_boxes(page, (800, 600))
        assert [(b.type, b.text) for b in boxes] == [("LINK", "메뉴"), ("HEADING", "제목")]
        assert all(b.bbox_px[1] < 100 for b in boxes)
        assert evaluate_isolated(page, "document.body.innerText") == "메뉴\n제목"
        with pytest.raises(Exception, match="terminated"):
            evaluate_isolated(page, "(() => { for (;;) {} })()", timeout_ms=500)
        with pytest.raises(RuntimeError):
            evaluate_isolated(page, "(() => { throw new Error('nope'); })()")
        context.close()


def test_playwright_extracts_visible_elements_with_types_and_text(dom_boxes):
    by_text = {b.text: b for b in dom_boxes}
    kinds = {(b.tag, b.type) for b in dom_boxes}
    assert {("a", "LINK"), ("h1", "HEADING"), ("b", "TEXT"), ("p", "TEXT"), ("button", "BUTTON"),
            ("div", "BUTTON"), ("input", "CHECKBOX"), ("input", "RADIO"), ("input", "INPUT"),
            ("select", "DROPDOWN"), ("textarea", "INPUT"), ("img", "IMAGE"), ("svg", "IMAGE"),
            ("li", "LIST_ITEM")} <= kinds
    assert by_text["제목 굵게"].type == "HEADING"            # parent of inline text keeps the whole line
    assert by_text["굵게"].tag == "b"
    assert by_text["본문 줄바꿈 문단"].tag == "p"             # whitespace and newlines collapsed
    assert by_text["검색"].type == "INPUT" and by_text["메모"].tag == "textarea"
    assert by_text["옵션 A"].type == "DROPDOWN"
    assert by_text["로고"].type == "IMAGE" and by_text["아이콘"].tag == "svg"
    assert "rect" not in {b.tag for b in dom_boxes}            # svg internals are skipped
    for hidden in ("숨김", "투명", "비가시", "부모가 투명"):
        assert hidden not in by_text
    assert "nav" not in {b.tag for b in dom_boxes}             # no own text: not a box


def test_playwright_boxes_are_pixel_and_normalized(dom_boxes):
    ids = [b.id for b in dom_boxes]
    assert ids == [f"d{i}" for i in range(1, len(ids) + 1)]
    heading = next(b for b in dom_boxes if b.tag == "h1")
    home = next(b for b in dom_boxes if b.text == "홈")
    assert heading.bbox_px[1] >= 40 > home.bbox_px[1]          # the heading sits below the 40px nav
    assert heading.bbox_px[0] >= 10
    for b in dom_boxes:
        x0, y0, x1, y1 = b.bbox_px
        assert x1 >= x0 + 1 and y1 >= y0 + 1
        assert all(0 <= v <= 1000 for v in b.bbox)
        assert b.bbox == normalize_box(b.bbox_px, (600, 400))
