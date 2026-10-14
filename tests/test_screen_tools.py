from __future__ import annotations

import json
import struct
import zlib

import pytest
from langchain_core.messages import HumanMessage

from koda.llm import _UNSUPPORTED_METHODS
from koda.screen import store
from koda.screen.dom import DomBox
from koda.screen.schema import ElementOut, ScreenParseOut
from koda.tools import screen as screen_tools
from koda.tools import vision
from koda.tools.screen import (
    CACHED_NOTE,
    DOM_ONLY_BETWEEN_RENDERS,
    EXTERNALLY_MODIFIED,
    IMAGE_REJECTED,
    NO_STRUCTURED_OUTPUT,
    OUTSIDE_WORKSPACE,
    SERVER_ERROR,
    STALE_NOTE,
    _image_rejected,
    make_screen_tools,
)
from koda.workspace import Workspace

PROPS = "http://vision.test/props"


def png_bytes(w: int, h: int, salt: bytes = b"") -> bytes:
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    return b"\x89PNG\r\n\x1a\n" + chunk + salt


def _out(*elements: dict, summary: str = "상단 내비게이션과 제목이 있는 페이지") -> ScreenParseOut:
    return ScreenParseOut(summary=summary, kind="page", elements=[ElementOut(**e) for e in elements])


_TWO = (
    {"type": "HEADING", "text": "오늘의 추천", "bbox": [40, 120, 420, 180]},
    {"type": "TEXT", "text": "본문", "bbox": [200, 120, 600, 180]},       # overlaps the heading
)


class _FakeVision:
    """with_structured_output stand-in: returns a fixed ScreenParseOut, None, or raises."""

    def __init__(self, result=None, exc: Exception | None = None, model_name: str = "fake-vl"):
        self.model_name = model_name
        self.result = result
        self.exc = exc
        self.calls = 0
        self.methods: list[str] = []
        self.messages: list = []

    def with_structured_output(self, schema, method=None):
        outer = self
        outer.methods.append(method)

        class _Runnable:
            def invoke(self, messages, config=None):
                outer.calls += 1
                outer.messages.append(messages)
                if outer.exc is not None:
                    raise outer.exc
                return outer.result

        return _Runnable()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # No HTTP probe: behave like a non-llama.cpp gateway. Registry and method cache start empty.
    monkeypatch.setitem(vision._VISION_SUPPORT_CACHE, PROPS, None)
    store.clear_registry()
    _UNSUPPORTED_METHODS.clear()
    yield
    store.clear_registry()
    _UNSUPPORTED_METHODS.clear()


@pytest.fixture
def ws(tmp_path) -> Workspace:
    (tmp_path / "shots").mkdir()
    (tmp_path / "shots" / "bug.png").write_bytes(png_bytes(1280, 800))
    (tmp_path / "design.png").write_bytes(png_bytes(1280, 800, b"design"))
    return Workspace(tmp_path)


def _tools(ws, model=None, methods=None):
    return {t.name: t for t in make_screen_tools(ws, model or _FakeVision(_out(*_TWO)), PROPS, methods)}


def _render(ws, name: str, boxes: list[DomBox], source_digest: str = "src", salt: bytes = b"") -> None:
    store.save_png(ws, name, png_bytes(1280, 800, salt or name.encode()))
    store.save_dom(ws, name, boxes, (1280, 800))
    store.save_meta(ws, name, store.RenderMeta(target="site/index.html", source_digest=source_digest, viewport="1280x800"))


def _box(i: int, t: str, text: str, px: list[int]) -> DomBox:
    return DomBox(id=f"d{i}", tag=t.lower(), type=t, text=text, bbox_px=px, bbox=px)


def test_make_screen_tools_returns_the_three_tools_in_order(ws):
    assert [t.name for t in make_screen_tools(ws, _FakeVision(), PROPS)] == ["parse_screen", "render_page", "screen_diff"]


# --- parse_screen ---

def test_parse_screen_saves_parse_and_formats_output(ws):
    model = _FakeVision(_out(*_TWO))
    out = _tools(ws, model)["parse_screen"].invoke({"path": "shots/bug.png"})
    lines = out.splitlines()

    assert lines[0] == "요소 2개 · 이슈 1개 · 크기 1280x800 · 저장 .koda/renders/src__shots_bug.parse.json"
    assert lines[1] == "장면: 상단 내비게이션과 제목이 있는 페이지"
    assert lines[2] == 'e1 HEADING "오늘의 추천" [40,120,420,180]'
    assert lines[3] == 'e2 TEXT "본문" [200,120,600,180]'
    assert lines[4].startswith("이슈: overlap e1·e2 (") and lines[4].endswith("%)")
    assert len(lines) == 5

    saved = ws.root / ".koda" / "renders" / "src__shots_bug.parse.json"
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["image"] == "shots/bug.png" and data["source"] == "image"
    assert data["image_sha256"] == store.file_sha256(ws.root / "shots" / "bug.png")
    assert store.registry_status(saved) == "fresh"

    # The vision call carries the prompt and the image as a data URL, with remember=False.
    assert model.calls == 1 and model.methods == ["json_schema"]
    msg = model.messages[0][0]
    assert isinstance(msg, HumanMessage)
    assert "0~1000" in msg.content[0]["text"] and "최대 80개" in msg.content[0]["text"]
    assert msg.content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_parse_screen_focus_is_appended_and_skips_the_cache(ws):
    model = _FakeVision(_out(*_TWO))
    tool = _tools(ws, model)["parse_screen"]
    tool.invoke({"path": "shots/bug.png"})
    tool.invoke({"path": "shots/bug.png", "focus": "가격 영역"})
    assert model.calls == 2
    assert "이 영역 또는 주제를 우선해 빠짐없이: 가격 영역" in model.messages[1][0].content[0]["text"]


def test_parse_screen_reuses_the_cache_and_reparses_on_hash_mismatch(ws):
    model = _FakeVision(_out(*_TWO))
    tool = _tools(ws, model)["parse_screen"]

    first = tool.invoke({"path": "shots/bug.png"})
    second = tool.invoke({"path": "shots/bug.png"})
    assert model.calls == 1
    assert second.splitlines()[2] == CACHED_NOTE
    assert second.replace(CACHED_NOTE + "\n", "") == first

    (ws.root / "shots" / "bug.png").write_bytes(png_bytes(1280, 800, b"edited"))
    third = tool.invoke({"path": "shots/bug.png"})
    assert model.calls == 2 and CACHED_NOTE not in third


def test_cached_parse_recomputes_issues_with_the_current_rule(ws):
    # A parse stored by an older overlap rule may carry an empty issue list; the elements are the truth.
    model = _FakeVision(_out(*_TWO))
    tool = _tools(ws, model)["parse_screen"]
    first = tool.invoke({"path": "shots/bug.png"})
    assert first.splitlines()[0].startswith("요소 2개 · 이슈 1개")
    saved = ws.root / ".koda" / "renders" / "src__shots_bug.parse.json"
    data = json.loads(saved.read_text(encoding="utf-8"))
    data["issues"] = []
    saved.write_text(json.dumps(data), encoding="utf-8")
    store.clear_registry()                                # as if an earlier koda process wrote it

    out = tool.invoke({"path": "shots/bug.png"})

    assert model.calls == 1 and CACHED_NOTE in out
    assert out.splitlines()[0].startswith("요소 2개 · 이슈 1개")
    assert "이슈: overlap e1·e2" in out


def test_parse_screen_refuses_a_tampered_cached_parse_and_notes_an_old_one(ws):
    # Spec 7.3 V4 applies to the parse cache too: a parse.json edited behind koda's back (elements and issues
    # emptied, image_sha256 kept) must not be reused by parse_screen or screen_diff.
    model = _FakeVision(_out(*_TWO))
    tools = _tools(ws, model)
    tools["parse_screen"].invoke({"path": "shots/bug.png"})
    saved = ws.root / ".koda" / "renders" / "src__shots_bug.parse.json"
    data = json.loads(saved.read_text(encoding="utf-8"))
    data["elements"], data["issues"] = [], []
    saved.write_text(json.dumps(data), encoding="utf-8")
    assert store.registry_status(saved) == "modified"

    assert tools["parse_screen"].invoke({"path": "shots/bug.png"}) == EXTERNALLY_MODIFIED
    assert tools["screen_diff"].invoke({"before": "shots/bug.png", "after": "design.png"}) == EXTERNALLY_MODIFIED
    assert model.calls == 1

    store.clear_registry()                                # a parse left by an earlier koda process
    out = tools["parse_screen"].invoke({"path": "shots/bug.png"})
    assert out.splitlines()[2:4] == [CACHED_NOTE, STALE_NOTE] and out.splitlines()[0].startswith("요소 0개")
    model.result = _out(*_TWO)
    out = tools["screen_diff"].invoke({"before": "shots/bug.png", "after": "design.png"})
    assert out.splitlines()[1] == "참고: type 변경 0 · 컨테이너 차이 0 · 이전 실행 산출물"


def test_image_rejected_only_on_a_real_http_400_or_image_input_refusal():
    for text in ("Error code: 400 - {'error': 'image input is not supported'}", "HTTP 400 Bad Request",
                 "status 400", "Invalid image_url", "this model has no image input"):
        assert _image_rejected(Exception(text)), text
    coded = Exception("rejected")
    coded.status_code = 400
    assert _image_rejected(coded)
    for text in ("Request timed out after 400ms", "model qwen-400b not found", "connection reset",
                 "Error code: 500 - image service down"):
        assert not _image_rejected(Exception(text)), text


def test_parse_screen_render_input_keeps_the_stem_and_marks_the_source(ws):
    store.save_png(ws, "after", png_bytes(640, 480))
    out = _tools(ws)["parse_screen"].invoke({"path": ".koda/renders/after.png"})
    assert out.splitlines()[0] == "요소 2개 · 이슈 1개 · 크기 640x480 · 저장 .koda/renders/after.parse.json"
    assert store.load_parse(ws.root / ".koda" / "renders" / "after.parse.json").source == "render"


def test_parse_screen_structured_none_and_image_rejection(ws):
    assert _tools(ws, _FakeVision(result=None))["parse_screen"].invoke({"path": "shots/bug.png"}) == NO_STRUCTURED_OUTPUT

    rejected = Exception("Error code: 400 - {'error': 'image input is not supported by this model'}")
    rejected.status_code = 400
    assert _tools(ws, _FakeVision(exc=rejected))["parse_screen"].invoke({"path": "shots/bug.png"}) == IMAGE_REJECTED
    assert not _UNSUPPORTED_METHODS                      # remember=False: the text calls keep their method

    assert _tools(ws, _FakeVision(exc=RuntimeError("Invalid image_url")))["parse_screen"].invoke({"path": "shots/bug.png"}) == IMAGE_REJECTED

    out = _tools(ws, _FakeVision(exc=RuntimeError("connection reset")))["parse_screen"].invoke({"path": "shots/bug.png"})
    assert out.startswith("오류: 화면 파싱 실패(RuntimeError)")
    assert not (ws.root / ".koda" / "renders" / "src__shots_bug.parse.json").exists()


def test_parse_screen_raising_structured_is_handled(ws, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("HTTP 400 Bad Request")
    monkeypatch.setattr(screen_tools, "structured", boom)
    assert _tools(ws)["parse_screen"].invoke({"path": "shots/bug.png"}) == IMAGE_REJECTED


def test_parse_screen_server_error_marker_and_mmproj(ws, monkeypatch):
    poisoned = _out({"type": "TEXT", "text": "x", "bbox": [0, 0, 1, 1]}, summary="Media upload error: 500")
    assert _tools(ws, _FakeVision(poisoned))["parse_screen"].invoke({"path": "shots/bug.png"}) == SERVER_ERROR

    monkeypatch.setitem(vision._VISION_SUPPORT_CACHE, PROPS, False)
    model = _FakeVision(_out(*_TWO))
    out = _tools(ws, model)["parse_screen"].invoke({"path": "shots/bug.png"})
    assert "mmproj" in out and model.calls == 0


def test_parse_screen_pins_methods_from_settings(ws):
    model = _FakeVision(_out(*_TWO))
    _tools(ws, model, methods=("function_calling",))["parse_screen"].invoke({"path": "shots/bug.png"})
    assert model.methods == ["function_calling"]


def test_parse_screen_path_errors(ws):
    tool = _tools(ws, _FakeVision(exc=AssertionError("must not be called")))["parse_screen"]
    assert tool.invoke({"path": "../x.png"}) == OUTSIDE_WORKSPACE
    assert tool.invoke({"path": "/etc/x.png"}) == OUTSIDE_WORKSPACE
    (ws.root / ".koda" / "sessions").mkdir(parents=True)
    (ws.root / ".koda" / "sessions" / "x.png").write_bytes(png_bytes(1, 1))
    out = tool.invoke({"path": ".koda/sessions/x.png"})
    assert out.startswith("오류:") and out != OUTSIDE_WORKSPACE
    (ws.root / "note.txt").write_text("x")
    assert "확장자" in tool.invoke({"path": "note.txt"})
    assert "없음" in tool.invoke({"path": "missing.png"})


def test_parse_screen_refuses_an_externally_modified_render(ws):
    png = store.save_png(ws, "after", png_bytes(10, 10))
    png.write_bytes(png_bytes(10, 10, b"tampered"))
    assert _tools(ws)["parse_screen"].invoke({"path": ".koda/renders/after.png"}) == EXTERNALLY_MODIFIED


def test_parse_screen_lists_at_most_eighty_elements(ws):
    many = [{"type": "TEXT", "text": f"t{i}", "bbox": [0, i * 10, 5, i * 10 + 5]} for i in range(85)]
    out = _tools(ws, _FakeVision(_out(*many)))["parse_screen"].invoke({"path": "shots/bug.png"})
    lines = out.splitlines()
    assert lines[0].startswith("요소 85개 · 이슈 0개")
    assert lines[2 + 80] == "... 외 5개" and len(lines) == 2 + 80 + 1


# --- screen_diff ---

def test_screen_diff_pixel_identical_needs_no_model(ws):
    (ws.root / "a.png").write_bytes(png_bytes(100, 100))
    (ws.root / "b.png").write_bytes(png_bytes(100, 100))
    model = _FakeVision(exc=AssertionError("must not be called"))

    out = _tools(ws, model)["screen_diff"].invoke({"before": "a.png", "after": "b.png"})

    assert out == "동등 · 픽셀 동일"
    saved = ws.root / ".koda" / "renders" / "src__a__src__b.diff.json"
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["method"] == "pixel" and data["pixel_identical"] is True and data["equal"] is True


def test_screen_diff_dom_path_for_two_renders(ws):
    _render(ws, "before", [_box(1, "HEADING", "제목", [40, 120, 420, 180]), _box(2, "BUTTON", "받기", [700, 600, 820, 660])], "s1")
    _render(ws, "after", [_box(1, "HEADING", "제목", [40, 120, 420, 180]), _box(2, "BUTTON", "받기", [700, 605, 820, 665])], "s2")
    model = _FakeVision(exc=AssertionError("must not be called"))
    tool = _tools(ws, model)["screen_diff"]

    out = tool.invoke({"before": ".koda/renders/before.png", "after": ".koda/renders/after.png"})

    assert out.splitlines() == [
        "차이 1건 · DOM 비교 · 누락 0 · 추가 0 · 글자 변경 0 · 이동 1 (허용 2px)",
        "이동: d2→d2 5",
    ]
    data = json.loads((ws.root / ".koda" / "renders" / "before__after.diff.json").read_text(encoding="utf-8"))
    assert data["method"] == "dom" and data["tolerance_px"] == 2 and data["source_identical"] is False

    # tolerance is px here, and the dom.json path is accepted as an input too
    out = tool.invoke({"before": ".koda/renders/before.dom.json", "after": ".koda/renders/after.png", "tolerance": 5})
    assert out == "동등 · DOM 비교 · 요소 2/2 일치 · 이동 0 · 글자 변경 0 (허용 5px)"


def test_screen_diff_marks_identical_sources(ws):
    boxes = [_box(1, "HEADING", "제목", [40, 120, 420, 180])]
    _render(ws, "before", boxes, "same")
    _render(ws, "after", boxes, "same", salt=b"other-bytes")
    out = _tools(ws)["screen_diff"].invoke({"before": ".koda/renders/before.png", "after": ".koda/renders/after.png"})
    assert out == "동등 · DOM 비교 · 요소 1/1 일치 · 이동 0 · 글자 변경 0 (허용 2px) · 소스 동일(리팩토링 증거 아님)"

    # pixel-identical renders of one source carry the mark as well
    _render(ws, "b2", boxes, "same", salt=b"same-bytes")
    _render(ws, "a2", boxes, "same", salt=b"same-bytes")
    assert _tools(ws)["screen_diff"].invoke({"before": ".koda/renders/b2.png", "after": ".koda/renders/a2.png"}) == \
        "동등 · 픽셀 동일 · 소스 동일(리팩토링 증거 아님)"


def test_screen_diff_vlm_path_when_one_side_is_a_workspace_image(ws):
    _render(ws, "after", [_box(1, "HEADING", "제목", [40, 120, 420, 180])])
    model = _FakeVision(_out(*_TWO))
    tool = _tools(ws, model)["screen_diff"]

    out = tool.invoke({"before": "design.png", "after": ".koda/renders/after.png"})
    lines = out.splitlines()

    assert lines[0] == "동등 · VLM 비교 · 요소 2/2 일치 · 이동 0 · 글자 변경 0 (허용 30)"
    assert lines[1] == "참고: type 변경 0 · 컨테이너 차이 0"
    assert model.calls == 2
    renders = ws.root / ".koda" / "renders"
    assert (renders / "src__design.parse.json").is_file() and (renders / "after.parse.json").is_file()
    assert (renders / "src__design__after.diff.json").is_file()

    # both parses are cached now: no new model call, and the tolerance is in the 0..1000 space
    out = tool.invoke({"before": "design.png", "after": ".koda/renders/after.png", "tolerance": 50})
    assert model.calls == 2 and out.splitlines()[0].endswith("(허용 50)")


def test_screen_diff_accepts_parse_json_inputs(ws):
    model = _FakeVision(_out(*_TWO))
    tools = _tools(ws, model)
    tools["parse_screen"].invoke({"path": "shots/bug.png"})
    model.result = _out({"type": "HEADING", "text": "오늘의 추천", "bbox": [40, 120, 420, 180]})
    tools["parse_screen"].invoke({"path": "design.png"})

    out = tools["screen_diff"].invoke({"before": ".koda/renders/src__shots_bug.parse.json", "after": ".koda/renders/src__design.parse.json"})

    assert model.calls == 2
    assert out.splitlines()[:3] == [
        "차이 1건 · VLM 비교 · 누락 1 · 추가 0 · 글자 변경 0 · 이동 0 (허용 30)",
        "참고: type 변경 0 · 컨테이너 차이 0",
        '누락: e2 TEXT "본문" [200,120,600,180]',
    ]
    assert (ws.root / ".koda" / "renders" / "src__shots_bug__src__design.diff.json").is_file()


def test_screen_diff_size_mismatch_is_an_error_on_the_vlm_path(ws):
    (ws.root / "tall.png").write_bytes(png_bytes(1280, 1600))
    out = _tools(ws)["screen_diff"].invoke({"before": "design.png", "after": "tall.png"})
    assert out == "오류: 이미지 크기가 다르다(1280x800 vs 1280x1600). 같은 viewport·full_page 로 렌더하라"

    # DOM comparison is in px, so differing sizes still proceed.
    _render(ws, "wide", [_box(1, "TEXT", "x", [0, 0, 10, 10])])
    store.save_png(ws, "narrow", png_bytes(640, 480))
    store.save_dom(ws, "narrow", [_box(1, "TEXT", "x", [0, 0, 10, 10])], (640, 480))
    out = _tools(ws)["screen_diff"].invoke({"before": ".koda/renders/wide.png", "after": ".koda/renders/narrow.png"})
    assert out.startswith("동등 · DOM 비교")


def test_screen_diff_detects_external_modification_and_notes_stale_inputs(ws):
    boxes = [_box(1, "TEXT", "x", [0, 0, 10, 10])]
    _render(ws, "before", boxes, "s1")
    _render(ws, "after", boxes, "s2")
    tool = _tools(ws)["screen_diff"]
    # meta.json is what the 소스 동일 mark comes from, so a forged one is refused like the dom.json
    meta = store.meta_path(ws, "after")
    meta.write_text(json.dumps({"target": "site/index.html", "source_digest": "forged"}), encoding="utf-8")
    assert store.registry_status(meta) == "modified"
    assert tool.invoke({"before": ".koda/renders/before.png", "after": ".koda/renders/after.png"}) == EXTERNALLY_MODIFIED

    _render(ws, "after", boxes, "s2")
    store.dom_path(ws, "after").write_text('{"image":"after.png","size":{"w":1280,"h":800},"boxes":[]}', encoding="utf-8")
    assert tool.invoke({"before": ".koda/renders/before.png", "after": ".koda/renders/after.png"}) == EXTERNALLY_MODIFIED

    store.clear_registry()                                 # as if a previous koda process made the files
    out = tool.invoke({"before": ".koda/renders/before.png", "after": ".koda/renders/after.png"})
    assert out.splitlines() == [
        "차이 1건 · DOM 비교 · 누락 1 · 추가 0 · 글자 변경 0 · 이동 0 (허용 2px)",
        "참고: 이전 실행 산출물",
        '누락: d1 TEXT "x" [0,0,10,10]',
    ]


def test_screen_diff_path_errors(ws):
    tool = _tools(ws, _FakeVision(exc=AssertionError("must not be called")))["screen_diff"]
    assert tool.invoke({"before": "../x.png", "after": "design.png"}) == OUTSIDE_WORKSPACE
    assert tool.invoke({"before": "design.png", "after": "/tmp/x.png"}) == OUTSIDE_WORKSPACE
    assert tool.invoke({"before": "design.png", "after": "missing.png"}).startswith("오류:")
    _render(ws, "after", [_box(1, "TEXT", "x", [0, 0, 10, 10])])
    assert tool.invoke({"before": ".koda/renders/after.dom.json", "after": "design.png"}) == DOM_ONLY_BETWEEN_RENDERS


def test_screen_diff_rejects_parse_files_outside_renders(ws):
    # parse_screen only ever writes under .koda/renders/, so two hand-written parses elsewhere in the
    # workspace are not evidence and must not produce a 동등 without a model call.
    parse = {"image": "x.png", "size": {"w": 10, "h": 10}, "scene": {"kind": "page", "summary": "s"},
             "elements": [{"id": "e1", "type": "TEXT", "text": "a", "bbox": [0, 0, 10, 10], "state": ""}],
             "issues": [], "image_sha256": "", "source": "image"}
    for name in ("before.parse.json", "after.parse.json"):
        (ws.root / name).write_text(json.dumps(parse), encoding="utf-8")
    tool = _tools(ws, _FakeVision(exc=AssertionError("must not be called")))["screen_diff"]
    out = tool.invoke({"before": "before.parse.json", "after": "after.parse.json"})
    assert out.startswith("오류: 파싱 파일은 .koda/renders/ 아래에만 있다")
    assert not list((ws.root / ".koda" / "renders").glob("*.diff.json"))


def test_screen_diff_applies_size_limits_to_a_renders_sidecars(ws, monkeypatch):
    # A dom.json read through the PNG path gets the same byte and box caps as one passed directly.
    boxes = [_box(i, "TEXT", f"t{i}", [0, i, 1, i + 1]) for i in range(1, 4)]
    _render(ws, "small", boxes, "s1")
    _render(ws, "big", boxes, "s2")
    tool = _tools(ws, _FakeVision(exc=AssertionError("must not be called")))["screen_diff"]

    monkeypatch.setattr(store, "MAX_PARSE_BYTES", 100)
    out = tool.invoke({"before": ".koda/renders/small.png", "after": ".koda/renders/big.png"})
    assert out.startswith("오류: DOM 파일이 너무 크다")
    monkeypatch.setattr(store, "MAX_PARSE_BYTES", 2 * 1024 * 1024)

    monkeypatch.setattr(screen_tools, "MAX_DOM_BOXES", 2)
    out = tool.invoke({"before": ".koda/renders/small.png", "after": ".koda/renders/big.png"})
    assert out == "오류: DOM 박스가 너무 많다(3개). 최대 2개."


def test_screen_diff_falls_back_to_vlm_when_a_render_has_no_dom(ws):
    store.save_png(ws, "old", png_bytes(1280, 800, b"old"))       # a render without dom.json
    _render(ws, "new", [_box(1, "TEXT", "x", [0, 0, 10, 10])])
    model = _FakeVision(_out(*_TWO))
    out = _tools(ws, model)["screen_diff"].invoke({"before": ".koda/renders/old.png", "after": ".koda/renders/new.png"})
    assert "VLM 비교" in out.splitlines()[0] and model.calls == 2
