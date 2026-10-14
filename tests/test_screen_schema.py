from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from koda.screen.schema import (
    BBOX_LIMIT,
    ELEMENT_TYPES,
    Element,
    ElementOut,
    ElementType,
    ScreenParse,
    ScreenParseOut,
    coerce_type,
    normalize_parse,
    normalize_text,
)


def _out(*elements: dict, summary: str = "요약", kind: str = "page") -> ScreenParseOut:
    return ScreenParseOut(summary=summary, kind=kind, elements=[ElementOut(**e) for e in elements])


def test_element_types_are_the_sixteen_from_the_spec():
    assert len(ELEMENT_TYPES) == 16
    assert set(ELEMENT_TYPES) == {
        "BUTTON", "LINK", "TEXT", "HEADING", "INPUT", "CHECKBOX", "RADIO", "SWITCH",
        "DROPDOWN", "IMAGE", "ICON", "CARD", "LIST_ITEM", "NAV", "DIALOG", "OTHER",
    }
    assert get_args(ElementType) == ELEMENT_TYPES


def test_json_schema_declares_type_as_enum_for_grammar_enforcement():
    schema = ScreenParseOut.model_json_schema()
    elem = schema["$defs"]["ElementOut"]["properties"]["type"]
    assert set(elem["enum"]) == set(ELEMENT_TYPES)


def test_coerce_type_is_case_insensitive_and_maps_aliases():
    assert coerce_type("button") == "BUTTON"
    assert coerce_type(" List_Item ") == "LIST_ITEM"
    assert coerce_type("navigation_link") == "LINK"
    assert coerce_type("product_card") == "CARD"
    assert coerce_type("paragraph") == "TEXT"
    assert coerce_type("price") == "TEXT"
    assert coerce_type("submit-button") == "BUTTON"
    assert coerce_type("hero image") == "IMAGE"


def test_coerce_type_unknown_or_empty_becomes_other():
    assert coerce_type("sparkline") == "OTHER"
    assert coerce_type("") == "OTHER"
    assert coerce_type(None) == "OTHER"


def test_coerce_type_ignores_one_letter_enumeration_suffixes():
    # "a" and "p" are aliases on their own, but "option_a" names a radio choice, not a link.
    assert coerce_type("a") == "LINK"
    assert coerce_type("p") == "TEXT"
    assert coerce_type("option_a") == "OTHER"
    assert coerce_type("step_a") == "OTHER"
    assert coerce_type("label_p") == "OTHER"
    assert coerce_type("column_b") == "OTHER"


def test_element_out_accepts_free_type_names_and_float_coordinates():
    # Endpoints without grammar enforcement send free values; the validator maps them before Literal checks.
    e = ElementOut(type="Navigation_Link", text="홈", bbox=[10.4, 20.6, 100, 40])
    assert e.type == "LINK"
    assert e.bbox == [10, 21, 100, 40]
    assert Element(id="e1", type="paragraph", bbox=[0, 0, 1, 1]).type == "TEXT"


def test_element_out_turns_null_text_state_and_bbox_into_empty_values():
    e = ElementOut(type="ICON", text=None, state=None, bbox=None)
    assert (e.text, e.state, e.bbox) == ("", "", [])

    out = ScreenParseOut.model_validate({"summary": "s", "kind": "page", "elements": [
        {"type": "HEADING", "text": "제목", "bbox": [0, 0, 10, 10]},
        {"type": "ICON", "text": None, "bbox": [1, 1, 2, 2]},
        {"type": "ICON", "text": "", "bbox": None},
        {"type": "ICON", "text": "", "bbox": "1,2,3,4"},
    ]})
    parse = normalize_parse(out, "x.png", (1000, 1000))
    assert [(e.id, e.type, e.text) for e in parse.elements] == [("e1", "HEADING", "제목"), ("e2", "ICON", "")]


def test_non_finite_and_huge_coordinates_never_overflow():
    out = ScreenParseOut.model_validate_json(
        '{"summary":"s","kind":"page","elements":[{"type":"TEXT","text":"inf","bbox":[0,0,1e400,1e400]},'
        '{"type":"TEXT","text":"big","bbox":[0,0,' + "9" * 400 + ',' + "9" * 400 + ']},'
        '{"type":"TEXT","text":"ok","bbox":[0,0,10,10]}]}'
    )
    assert out.elements[0].bbox == []
    assert out.elements[1].bbox == [0, 0, BBOX_LIMIT, BBOX_LIMIT]
    assert ElementOut(type="TEXT", bbox=[0, 0, float("inf"), 1]).bbox == []
    assert ElementOut(type="TEXT", bbox=[0, 0, float("nan"), 1]).bbox == []
    assert ElementOut(type="TEXT", bbox=[0, 0, -10 ** 30, 1]).bbox == [0, 0, -BBOX_LIMIT, 1]

    parse = normalize_parse(out, "x.png", (1280, 800))
    assert [(e.text, e.bbox) for e in parse.elements] == [("big", [0, 0, 1000, 1000]), ("ok", [0, 0, 10, 10])]
    # The clipped box now covers the small one entirely, which counts as an overlap between two leaves.
    assert [(i.kind, i.elements) for i in parse.issues] == [("out_of_bounds", ["e1"]), ("overlap", ["e1", "e2"])]


def test_stored_element_requires_four_coordinates_within_range():
    def stored(bbox: str) -> str:
        return '{"image":"x.png","size":{"w":1,"h":1},"scene":{},"elements":[{"id":"e1","type":"TEXT","bbox":' + bbox + '}]}'

    assert ScreenParse.model_validate_json(stored("[0,0,1000,1000]")).elements[0].bbox == [0, 0, 1000, 1000]
    for bad in ("[0,0,5]", "[0,0,5,5,5]", "[]", "[0,0,1001,5]", "[-1,0,5,5]", "[0,0," + "9" * 400 + ",5]"):
        with pytest.raises(ValidationError):
            ScreenParse.model_validate_json(stored(bad))
    with pytest.raises(ValidationError):
        Element(id="e1", type="TEXT", bbox=[])


def test_normalize_text_collapses_whitespace_and_keeps_case():
    assert normalize_text("  오늘의\n  추천 \t Deal  ") == "오늘의 추천 Deal"
    assert normalize_text("") == ""


def test_normalized_coordinates_are_kept_and_ids_reassigned():
    parse = normalize_parse(
        _out({"type": "HEADING", "text": "제목", "bbox": [40, 120, 420, 180]},
             {"type": "BUTTON", "text": "받기", "bbox": [700, 600, 820, 660], "state": "enabled"}),
        "bug.png", (1280, 800),
    )
    assert parse.image == "bug.png"
    assert (parse.size.w, parse.size.h) == (1280, 800)
    assert (parse.scene.kind, parse.scene.summary) == ("page", "요약")
    assert [e.id for e in parse.elements] == ["e1", "e2"]
    assert parse.elements[0].bbox == [40, 120, 420, 180]
    assert parse.elements[1].state == "enabled"
    assert parse.issues == []


def test_pixel_coordinates_are_scaled_by_size_when_any_exceeds_1000():
    parse = normalize_parse(
        _out({"type": "TEXT", "text": "a", "bbox": [0, 0, 640, 400]},
             {"type": "TEXT", "text": "b", "bbox": [1280, 0, 1280, 800]}),
        "shot.png", (1280, 800),
    )
    assert parse.elements[0].bbox == [0, 0, 500, 500]
    assert parse.elements[1].bbox == [1000, 0, 1000, 1000]
    assert parse.issues == []


def test_pixel_evidence_is_per_axis():
    # Width is at most 1000 so x gives no evidence, but y 1100 fits the 1200px height: pixels.
    parse = normalize_parse(
        _out({"type": "TEXT", "text": "a", "bbox": [0, 1100, 100, 1150]},
             {"type": "TEXT", "text": "b", "bbox": [0, 0, 400, 600]}),
        "tall.png", (800, 1200),
    )
    assert parse.elements[0].bbox == [0, 917, 125, 958]
    assert parse.elements[1].bbox == [0, 0, 500, 500]
    assert parse.issues == []


def test_lone_out_of_range_normalized_coordinate_does_not_rescale_the_rest():
    # y 1005 exceeds the 800px height as well, so it cannot be a pixel value: the heading stays as given.
    parse = normalize_parse(
        _out({"type": "HEADING", "text": "제목", "bbox": [40, 120, 420, 180]},
             {"type": "TEXT", "text": "foot", "bbox": [0, 980, 1000, 1005]}),
        "shot.png", (1280, 800),
    )
    assert parse.elements[0].bbox == [40, 120, 420, 180]
    assert parse.elements[1].bbox == [0, 980, 1000, 1000]
    assert [(i.kind, i.elements) for i in parse.issues] == [("out_of_bounds", ["e2"])]
    assert "0~1000" in parse.issues[0].detail

    # Both axes at most 1000px: 1200 is not a pixel value under any reading.
    parse = normalize_parse(
        _out({"type": "HEADING", "text": "제목", "bbox": [40, 120, 420, 180]},
             {"type": "TEXT", "text": "x", "bbox": [0, 0, 1200, 50]}),
        "small.png", (800, 600),
    )
    assert parse.elements[0].bbox == [40, 120, 420, 180]
    assert parse.elements[1].bbox == [0, 0, 1000, 50]
    assert [(i.kind, i.elements) for i in parse.issues] == [("out_of_bounds", ["e2"])]


def test_without_size_coordinates_over_1000_are_clipped_not_scaled():
    parse = normalize_parse(_out({"type": "TEXT", "text": "a", "bbox": [0, 0, 1280, 800]}), "x.webp", None)
    assert (parse.size.w, parse.size.h) == (1000, 1000)
    assert parse.elements[0].bbox == [0, 0, 1000, 800]
    assert [i.kind for i in parse.issues] == ["out_of_bounds"]
    assert parse.issues[0].elements == ["e1"]


def test_out_of_bounds_pixels_are_clipped_and_reported():
    parse = normalize_parse(
        _out({"type": "BUTTON", "text": "ok", "bbox": [1200, 700, 1400, 900]},
             {"type": "TEXT", "text": "neg", "bbox": [-10, 10, 100, 50]}),
        "shot.png", (1280, 800),
    )
    assert parse.elements[0].bbox == [938, 875, 1000, 1000]
    assert parse.elements[1].bbox == [0, 12, 78, 62]
    assert [(i.kind, i.elements) for i in parse.issues] == [("out_of_bounds", ["e1"]), ("out_of_bounds", ["e2"])]
    assert "1280x800" in parse.issues[0].detail


def test_flipped_boxes_are_sorted():
    parse = normalize_parse(_out({"type": "TEXT", "text": "a", "bbox": [420, 180, 40, 120]}), "x.png", (1000, 1000))
    assert parse.elements[0].bbox == [40, 120, 420, 180]


def test_unknown_type_becomes_other_and_model_ids_are_dropped():
    out = ScreenParseOut.model_validate({
        "summary": "s", "kind": "page",
        "elements": [{"id": "zzz", "type": "sparkline", "text": "", "bbox": [1, 1, 2, 2]}],
    })
    parse = normalize_parse(out, "x.png", (100, 100))
    assert parse.elements[0].type == "OTHER"
    assert parse.elements[0].id == "e1"


def test_elements_without_four_coordinates_are_dropped():
    parse = normalize_parse(
        _out({"type": "TEXT", "text": "bad", "bbox": [1, 2, 3]},
             {"type": "TEXT", "text": "good", "bbox": [1, 2, 3, 4]}),
        "x.png", (100, 100),
    )
    assert [e.text for e in parse.elements] == ["good"]
    assert parse.elements[0].id == "e1"


def test_overlap_issues_are_computed_in_normalize_parse():
    parse = normalize_parse(
        _out({"type": "HEADING", "text": "제목", "bbox": [0, 0, 100, 100]},
             {"type": "TEXT", "text": "본문", "bbox": [50, 0, 150, 100]}),
        "x.png", (1000, 1000),
    )
    assert [i.kind for i in parse.issues] == ["overlap"]
    assert parse.issues[0].elements == ["e1", "e2"]


def test_screen_parse_round_trips_through_json():
    parse = normalize_parse(_out({"type": "TEXT", "text": "a\nb", "bbox": [0, 0, 10, 10]}), "x.png", (10, 10))
    again = ScreenParse.model_validate_json(parse.model_dump_json())
    assert again == parse


def test_parse_carries_image_hash_and_source():
    out = _out({"type": "TEXT", "text": "a", "bbox": [0, 0, 10, 10]})
    default = normalize_parse(out, "bug.png", (10, 10))
    assert (default.image_sha256, default.source) == ("", "image")

    parse = normalize_parse(out, ".koda/renders/after.png", (10, 10), image_sha256="ab" * 32, source="render")
    assert (parse.image_sha256, parse.source) == ("ab" * 32, "render")
    data = parse.model_dump()
    assert data["image_sha256"] == "ab" * 32 and data["source"] == "render"
    assert ScreenParse.model_validate_json(parse.model_dump_json()) == parse
    with pytest.raises(ValidationError):
        ScreenParse(image="x.png", size={"w": 1, "h": 1}, scene={}, source="upload")


def test_bbox_description_states_the_normalized_space():
    desc = ElementOut.model_json_schema()["properties"]["bbox"]["description"]
    assert "0~1000" in desc and "정규화" in desc
