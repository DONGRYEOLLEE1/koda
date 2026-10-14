from __future__ import annotations

import json

from koda.screen.diff import (
    DEFAULT_TOLERANCE_PX,
    MAX_TOLERANCE,
    MIN_TOLERANCE,
    DiffResult,
    TextChange,
    TypeChange,
    clamp_tolerance,
    clamp_tolerance_px,
    diff_dom,
    diff_parses,
    match_key,
    pixel_identical_result,
    render_diff,
)
from koda.screen.dom import DomBox
from koda.screen.schema import Element, Scene, ScreenParse, Size

VLM_NOTE_ZERO = "참고: type 변경 0 · 컨테이너 차이 0"


def _parse(image: str, *elements: tuple[str, str, list[int]], render: bool = True, size=(1280, 800)) -> ScreenParse:
    return ScreenParse(
        image=f".koda/renders/{image}" if render else image,
        size=Size(w=size[0], h=size[1]), scene=Scene(kind="page", summary="s"),
        elements=[Element(id=f"e{i}", type=t, text=x, bbox=b) for i, (t, x, b) in enumerate(elements, 1)],
    )


def _box(i: int, t: str, text: str, px: list[int]) -> DomBox:
    return DomBox(id=f"d{i}", tag=t.lower(), type=t, text=text, bbox_px=px, bbox=px)


# --- VLM parse comparison ---

def test_identical_parses_are_equal_with_fixed_summary():
    before = _parse("before.png", ("HEADING", "오늘의 추천", [40, 120, 420, 180]), ("BUTTON", "받기", [700, 600, 820, 660]))
    after = _parse("after.png", ("HEADING", "오늘의 추천", [40, 120, 420, 180]), ("BUTTON", "받기", [700, 600, 820, 660]))

    r = diff_parses(before, after)

    assert r.equal is True and r.method == "vlm"
    assert r.pixel_identical is False
    assert r.matched == 2
    assert (r.before, r.after, r.tolerance) == ("before.parse.json", "after.parse.json", 30)
    assert r.summary == "동등 · VLM 비교 · 요소 2/2 일치 · 이동 0 · 글자 변경 0 (허용 30)"
    assert render_diff(r) == f"{r.summary}\n{VLM_NOTE_ZERO}"


def test_parse_names_follow_the_store_rule():
    # A workspace image gets the src__ prefix so a render named "design" cannot collide with design.png.
    before = _parse("shots/design.png", render=False)
    after = _parse("site_index.png")

    r = diff_parses(before, after)

    assert (r.before, r.after) == ("src__shots_design.parse.json", "site_index.parse.json")


def test_text_normalization_and_small_moves_still_match():
    before = _parse("b.png", ("CARD", "제목\n가격", [0, 0, 300, 300]))
    after = _parse("a.png", ("CARD", "제목  가격", [10, 10, 310, 310]))

    r = diff_parses(before, after, tolerance=30)

    assert r.equal and r.matched == 1 and r.moved == []


def test_match_key_drops_whitespace_punctuation_and_case():
    assert match_key("오늘의 추천!") == "오늘의추천"
    assert match_key(" Hot Deal, now. ") == "hotdealnow"
    assert match_key("") == ""
    # Step 1 pairs on the loose key; such a pair is not a text change.
    before = _parse("b.png", ("HEADING", "Hot Deal", [0, 0, 100, 20]))
    after = _parse("a.png", ("HEADING", "hot deal.", [0, 0, 100, 20]))
    r = diff_parses(before, after)
    assert r.equal and r.text_changed == []


def test_missing_added_text_changed_and_moved_with_summary():
    before = _parse(
        "before.png",
        ("HEADING", "오늘의 추천", [40, 120, 420, 180]),   # text changes
        ("BUTTON", "받기", [700, 600, 820, 660]),          # missing
        ("TEXT", "설명", [40, 200, 420, 240]),             # moves by 60
        ("LINK", "홈", [0, 0, 60, 30]),                    # unchanged
    )
    after = _parse(
        "after.png",
        ("HEADING", "오늘 추천", [40, 120, 420, 180]),
        ("TEXT", "설명", [40, 260, 420, 300]),
        ("LINK", "홈", [0, 0, 60, 30]),
        ("ICON", "", [900, 10, 930, 40]),                  # added, but text-less: container_diff
        ("TEXT", "새 문단", [40, 400, 420, 440]),          # added
    )

    r = diff_parses(before, after, tolerance=30)

    assert r.equal is False
    assert r.matched == 3
    assert [(m.id, m.type, m.text, m.bbox) for m in r.missing] == [("e2", "BUTTON", "받기", [700, 600, 820, 660])]
    assert [(a.id, a.type) for a in r.added] == [("e5", "TEXT")]
    assert [(c.before, c.after, c.from_, c.to) for c in r.text_changed] == [("e1", "e1", "오늘의 추천", "오늘 추천")]
    assert [(m.before, m.after, m.delta) for m in r.moved] == [("e3", "e2", 60)]
    assert [(c.side, c.id, c.type) for c in r.container_diff] == [("added", "e4", "ICON")]
    assert r.summary == "차이 4건 · VLM 비교 · 누락 1 · 추가 1 · 글자 변경 1 · 이동 1 (허용 30)"

    text = render_diff(r)
    assert text.splitlines() == [
        r.summary,
        "참고: type 변경 0 · 컨테이너 차이 1",
        '누락: e2 BUTTON "받기" [700,600,820,660]',
        '추가: e5 TEXT "새 문단" [40,400,420,440]',
        '글자 변경: e1 "오늘의 추천" → "오늘 추천"',
        "이동: e3→e2 60",
        '컨테이너 추가: e4 ICON "" [900,10,930,40]',
    ]


def test_same_text_matches_by_nearest_center_before_tolerance_fallback():
    # Two "더보기" links on each side: pairing must follow proximity, not element order.
    before = _parse("b.png", ("LINK", "더보기", [0, 0, 100, 20]), ("LINK", "더보기", [0, 500, 100, 520]))
    after = _parse("a.png", ("LINK", "더보기", [0, 500, 100, 520]), ("LINK", "더보기", [0, 0, 100, 20]))

    r = diff_parses(before, after)

    assert r.equal
    assert r.moved == []


def test_same_text_wins_over_a_nearer_element_with_different_text():
    # Step 1 pairs by text before step 2 looks at distance alone.
    before = _parse("b.png", ("BUTTON", "받기", [0, 0, 10, 10]))
    after = _parse("a.png", ("BUTTON", "사기", [0, 0, 10, 10]), ("BUTTON", "받기", [200, 200, 210, 210]))

    r = diff_parses(before, after, tolerance=30)

    assert [(m.before, m.after, m.delta) for m in r.moved] == [("e1", "e2", 283)]
    assert [a.id for a in r.added] == ["e1"] and r.text_changed == [] and r.missing == []
    assert r.summary == "차이 2건 · VLM 비교 · 누락 0 · 추가 1 · 글자 변경 0 · 이동 1 (허용 30)"


def test_text_change_only_within_tolerance_else_missing_and_added():
    before = _parse("b.png", ("BUTTON", "구매", [100, 100, 200, 140]))
    near = _parse("a.png", ("BUTTON", "구매하기", [110, 110, 210, 150]))
    far = _parse("a.png", ("BUTTON", "구매하기", [600, 600, 700, 640]))

    r_near = diff_parses(before, near, tolerance=30)
    assert [(c.from_, c.to) for c in r_near.text_changed] == [("구매", "구매하기")]
    assert r_near.missing == [] and r_near.added == [] and r_near.matched == 1
    assert r_near.summary == "차이 1건 · VLM 비교 · 누락 0 · 추가 0 · 글자 변경 1 · 이동 0 (허용 30)"

    r_far = diff_parses(before, far, tolerance=30)
    assert r_far.text_changed == []
    assert [m.id for m in r_far.missing] == ["e1"] and [a.id for a in r_far.added] == ["e1"]
    assert r_far.matched == 0
    assert r_far.summary == "차이 2건 · VLM 비교 · 누락 1 · 추가 1 · 글자 변경 0 · 이동 0 (허용 30)"


def test_same_text_with_different_type_is_a_type_change_not_a_difference():
    # A VLM calls the same "받기" a BUTTON in one parse and a LINK in the next. That is noise for
    # the verdict, so it is matched and counted aside.
    before = _parse("b.png", ("BUTTON", "받기", [0, 0, 10, 10]))
    after = _parse("a.png", ("LINK", "받기", [0, 0, 10, 10]))

    r = diff_parses(before, after)

    assert r.matched == 1 and r.missing == [] and r.added == []
    assert [(c.before, c.after, c.from_, c.to) for c in r.type_changed] == [("e1", "e1", "BUTTON", "LINK")]
    assert r.equal is True
    assert render_diff(r).splitlines() == [
        "동등 · VLM 비교 · 요소 1/1 일치 · 이동 0 · 글자 변경 0 (허용 30)",
        "참고: type 변경 1 · 컨테이너 차이 0",
        "type 변경: e1 BUTTON → LINK",
    ]


def test_text_less_elements_never_pair_across_types_in_step_one():
    # Empty text is not a shared key: an ICON must not be matched with a CARD just by distance.
    before = _parse("b.png", ("ICON", "", [0, 0, 10, 10]))
    after = _parse("a.png", ("CARD", "", [0, 0, 10, 10]))

    r = diff_parses(before, after)

    assert r.matched == 0
    assert [(c.side, c.type) for c in r.container_diff] == [("missing", "ICON"), ("added", "CARD")]
    assert r.equal is True and r.missing == [] and r.added == []


def test_container_with_text_is_a_real_missing():
    before = _parse("b.png", ("CARD", "제목 가격", [0, 0, 300, 300]), ("NAV", "", [0, 0, 1000, 60]))
    after = _parse("a.png")

    r = diff_parses(before, after)

    assert [m.type for m in r.missing] == ["CARD"]
    assert [(c.side, c.type) for c in r.container_diff] == [("missing", "NAV")]
    assert r.equal is False
    assert r.summary == "차이 1건 · VLM 비교 · 누락 1 · 추가 0 · 글자 변경 0 · 이동 0 (허용 30)"


def test_moved_uses_tolerance_as_strict_boundary():
    before = _parse("b.png", ("TEXT", "x", [0, 0, 10, 10]))
    exactly = _parse("a.png", ("TEXT", "x", [30, 0, 40, 10]))
    beyond = _parse("a.png", ("TEXT", "x", [31, 0, 41, 10]))

    assert diff_parses(before, exactly, tolerance=30).moved == []
    moved = diff_parses(before, beyond, tolerance=30).moved
    assert [(m.before, m.after, m.delta) for m in moved] == [("e1", "e1", 31)]

    # Center distance 30.004: moved, and the printed delta must not round back down to the tolerance.
    barely = _parse("a.png", ("TEXT", "x", [30, 0, 40, 11]))
    r = diff_parses(before, barely, tolerance=30)
    assert [(m.before, m.after, m.delta) for m in r.moved] == [("e1", "e1", 31)]
    assert render_diff(r).splitlines()[-1] == "이동: e1→e1 31"


def test_tolerance_is_clamped_to_5_and_200():
    assert (MIN_TOLERANCE, MAX_TOLERANCE) == (5, 200)
    assert clamp_tolerance(-5) == 5 and clamp_tolerance(1000) == 200 and clamp_tolerance(30.9) == 30
    before = _parse("b.png", ("TEXT", "x", [0, 0, 10, 10]))
    after = _parse("a.png", ("TEXT", "x", [0, 0, 10, 10]))

    low = diff_parses(before, after, tolerance=-5)
    assert low.equal and low.moved == [] and low.tolerance == 5
    assert low.summary == "동등 · VLM 비교 · 요소 1/1 일치 · 이동 0 · 글자 변경 0 (허용 5)"

    high = diff_parses(before, after, tolerance=1000)
    assert high.tolerance == 200 and high.summary.endswith("(허용 200)")
    assert pixel_identical_result("b.png", "a.png", tolerance=1000).tolerance == 200


def test_render_diff_caps_each_kind_at_twenty_lines():
    before = _parse("b.png", *[("TEXT", f"t{i}", [0, i * 10, 5, i * 10 + 5]) for i in range(25)])
    after = _parse("a.png")

    text = render_diff(diff_parses(before, after))
    lines = text.splitlines()

    assert len(lines) == 1 + 1 + 20 + 1
    assert lines[1] == VLM_NOTE_ZERO
    assert lines[2].startswith("누락: e1 TEXT")
    assert lines[-1] == "... 외 5개"


def test_render_diff_collapses_newlines_in_text():
    before = _parse("b.png", ("CARD", "제목\n가격", [0, 0, 10, 10]))
    after = _parse("a.png")

    lines = render_diff(diff_parses(before, after)).splitlines()

    assert lines[2] == '누락: e1 CARD "제목 가격" [0,0,10,10]'


def test_text_change_renders_both_ids_when_they_differ():
    r = DiffResult(before="b.parse.json", after="a.parse.json", method="dom", summary="s",
                   text_changed=[TextChange(before="e2", after="e3", from_="a", to="b")])

    assert render_diff(r).splitlines() == ["s", '글자 변경: e2→e3 "a" → "b"']


def test_source_identical_and_stale_inputs_are_shown():
    before = _parse("b.png", ("TEXT", "x", [0, 0, 10, 10]))
    after = _parse("a.png", ("TEXT", "x", [0, 0, 10, 10]))

    r = diff_parses(before, after, source_identical=True, stale_inputs=True)

    assert r.source_identical and r.stale_inputs
    assert r.summary == "동등 · VLM 비교 · 요소 1/1 일치 · 이동 0 · 글자 변경 0 (허용 30) · 소스 동일(리팩토링 증거 아님)"
    assert render_diff(r).splitlines()[1] == "참고: type 변경 0 · 컨테이너 차이 0 · 이전 실행 산출물"


# --- pixel short circuit ---

def test_pixel_identical_result_short_circuits():
    r = pixel_identical_result("before.png", "after.png", tolerance=30)

    assert r.pixel_identical is True and r.equal is True and r.method == "pixel"
    assert r.summary == "동등 · 픽셀 동일"
    assert r.matched == 0 and r.missing == [] and r.moved == []
    assert render_diff(r) == "동등 · 픽셀 동일"

    r = pixel_identical_result("before.png", "after.png", source_identical=True, stale_inputs=True)
    assert render_diff(r) == "동등 · 픽셀 동일 · 소스 동일(리팩토링 증거 아님)\n참고: 이전 실행 산출물"


# --- DOM comparison ---

def test_dom_identical_boxes_are_equal_with_px_summary():
    before = [_box(1, "HEADING", "제목", [40, 120, 420, 180]), _box(2, "BUTTON", "받기", [700, 600, 820, 660])]
    after = [_box(1, "BUTTON", "받기", [700, 600, 820, 660]), _box(2, "HEADING", "제목", [40, 120, 420, 180])]

    r = diff_dom(before, after)

    assert r.method == "dom" and r.equal and r.matched == 2
    assert (r.before, r.after, r.tolerance_px) == ("before.dom.json", "after.dom.json", DEFAULT_TOLERANCE_PX)
    assert r.summary == "동등 · DOM 비교 · 요소 2/2 일치 · 이동 0 · 글자 변경 0 (허용 2px)"
    assert render_diff(r) == r.summary


def test_dom_tolerance_zero_means_default_and_px_is_clamped():
    assert clamp_tolerance_px(0) == 2 and clamp_tolerance_px(-3) == 2
    assert clamp_tolerance_px(1) == 1 and clamp_tolerance_px(5000) == 200
    r = diff_dom([], [], tolerance_px=0, before_name="x.dom.json", after_name="y.dom.json")
    assert r.tolerance_px == 2 and (r.before, r.after) == ("x.dom.json", "y.dom.json")


def test_dom_move_by_center_or_size_beyond_tolerance():
    before = [_box(1, "TEXT", "a", [0, 0, 100, 20])]
    shifted_2 = [_box(1, "TEXT", "a", [2, 0, 102, 20])]
    shifted_3 = [_box(1, "TEXT", "a", [3, 0, 103, 20])]
    wider = [_box(1, "TEXT", "a", [0, 0, 106, 20])]          # center moves 3, width grows 6

    assert diff_dom(before, shifted_2).equal
    r = diff_dom(before, shifted_3)
    assert [(m.before, m.after, m.delta) for m in r.moved] == [("d1", "d1", 3)]
    assert r.summary == "차이 1건 · DOM 비교 · 누락 0 · 추가 0 · 글자 변경 0 · 이동 1 (허용 2px)"
    assert [(m.delta) for m in diff_dom(before, wider).moved] == [6]
    assert diff_dom(before, wider, tolerance_px=6).equal


def test_dom_text_change_missing_and_added():
    before = [_box(1, "HEADING", "오늘의 추천", [40, 120, 420, 180]), _box(2, "BUTTON", "받기", [700, 600, 820, 660])]
    after = [_box(1, "HEADING", "오늘 추천", [41, 120, 421, 180]), _box(2, "LINK", "더보기", [0, 0, 60, 30])]

    r = diff_dom(before, after)

    assert [(c.before, c.after, c.from_, c.to) for c in r.text_changed] == [("d1", "d1", "오늘의 추천", "오늘 추천")]
    assert [(m.id, m.type, m.bbox) for m in r.missing] == [("d2", "BUTTON", [700, 600, 820, 660])]
    assert [(a.id, a.type) for a in r.added] == [("d2", "LINK")]
    assert r.moved == [] and r.type_changed == [] and r.container_diff == []
    assert render_diff(r).splitlines() == [
        "차이 3건 · DOM 비교 · 누락 1 · 추가 1 · 글자 변경 1 · 이동 0 (허용 2px)",
        '누락: d2 BUTTON "받기" [700,600,820,660]',
        '추가: d2 LINK "더보기" [0,0,60,30]',
        '글자 변경: d1 "오늘의 추천" → "오늘 추천"',
    ]


def test_dom_same_type_different_text_pairs_only_within_tolerance():
    before = [_box(1, "BUTTON", "구매", [100, 100, 200, 140])]
    far = [_box(1, "BUTTON", "구매하기", [600, 600, 700, 640])]

    r = diff_dom(before, far)

    assert r.text_changed == [] and len(r.missing) == 1 and len(r.added) == 1


# --- serialization ---

def test_text_change_serializes_from_key_and_loads_back():
    c = TextChange(before="e1", after="e1", from_="a", to="b")
    data = json.loads(c.model_dump_json(by_alias=True))
    assert data == {"before": "e1", "after": "e1", "from": "a", "to": "b"}
    assert TextChange.model_validate(data).from_ == "a"
    assert TextChange.model_validate({"before": "e1", "after": "e1", "from_": "c", "to": "b"}).from_ == "c"
    assert json.loads(TypeChange(before="e1", after="e2", from_="BUTTON", to="LINK").model_dump_json(by_alias=True))["from"] == "BUTTON"


def test_diff_result_round_trips_with_new_fields():
    before = _parse("b.png", ("BUTTON", "받기", [0, 0, 10, 10]), ("CARD", "", [0, 0, 500, 500]))
    after = _parse("a.png", ("LINK", "받기", [0, 0, 10, 10]))
    r = diff_parses(before, after, source_identical=True, stale_inputs=True)

    data = json.loads(json.dumps(r.model_dump(by_alias=True)))

    assert data["method"] == "vlm" and data["tolerance_px"] == 0
    assert data["type_changed"] == [{"before": "e1", "after": "e1", "from": "BUTTON", "to": "LINK"}]
    assert data["container_diff"][0]["side"] == "missing"
    assert data["source_identical"] is True and data["stale_inputs"] is True
    assert DiffResult.model_validate(data) == r
