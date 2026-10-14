from __future__ import annotations

import math

import pytest

from koda.screen.geometry import (
    CONTAINER_TYPES,
    CONTAINMENT_LIMIT,
    LEAF_TYPES,
    OVERLAP_CONTAINER_TYPES,
    OVERLAP_IOU,
    OVERLAP_MIN_CONTAINMENT,
    Issue,
    center_distance,
    containment,
    find_issues,
    iou,
)
from koda.screen.schema import ELEMENT_TYPES, Element


def _el(eid: str, etype: str, bbox: list[int], text: str = "") -> Element:
    return Element(id=eid, type=etype, text=text, bbox=bbox)


def test_leaf_and_container_types_partition_the_sixteen():
    assert LEAF_TYPES == {"TEXT", "HEADING", "BUTTON", "LINK", "INPUT", "CHECKBOX", "RADIO", "SWITCH", "DROPDOWN", "ICON"}
    assert CONTAINER_TYPES == {"CARD", "NAV", "DIALOG", "LIST_ITEM", "IMAGE", "OTHER"}
    assert LEAF_TYPES | CONTAINER_TYPES == set(ELEMENT_TYPES)
    assert OVERLAP_CONTAINER_TYPES == {"NAV", "DIALOG"} and OVERLAP_CONTAINER_TYPES < CONTAINER_TYPES


def test_iou_values():
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert iou([0, 0, 10, 10], [10, 0, 20, 10]) == 0.0
    assert iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(50 / 150)
    assert iou([0, 0, 0, 0], [0, 0, 0, 0]) == 0.0


def test_containment_is_relative_to_the_smaller_box():
    assert containment([0, 0, 100, 100], [10, 10, 20, 20]) == 1.0
    assert containment([10, 10, 20, 20], [0, 0, 100, 100]) == 1.0
    assert containment([0, 0, 10, 10], [5, 0, 15, 10]) == 0.5
    assert containment([0, 0, 0, 0], [0, 0, 10, 10]) == 0.0


def test_center_distance_is_euclidean():
    assert center_distance([0, 0, 10, 10], [0, 0, 10, 10]) == 0.0
    assert center_distance([0, 0, 10, 10], [30, 40, 40, 50]) == pytest.approx(math.hypot(30, 40))


def test_overlap_at_iou_boundary():
    # Two 100x100 boxes shifted so that IoU is 1/7 (below 0.2, containment 0.25 below 0.3) and then 1/3.
    a = _el("e1", "TEXT", [0, 0, 100, 100])
    below = _el("e2", "TEXT", [75, 0, 175, 100])     # inter 2500, union 17500
    above = _el("e3", "TEXT", [50, 0, 150, 100])     # inter 5000, union 15000
    assert iou(a.bbox, below.bbox) < 0.2 < iou(a.bbox, above.bbox)
    assert containment(a.bbox, below.bbox) == 0.25 < OVERLAP_MIN_CONTAINMENT
    assert find_issues([a, below]) == []
    issues = find_issues([a, above])
    assert [(i.kind, i.elements) for i in issues] == [("overlap", ["e1", "e3"])]
    assert issues[0].detail == "TEXT 과 TEXT 가 겹침 (IoU 33%, 작은 쪽 50% 덮임)"


def test_overlap_exactly_point_two_counts():
    a = _el("e1", "BUTTON", [0, 0, 100, 100])
    b = _el("e2", "LINK", [50, 0, 150, 200])        # inter 5000, union 25000
    assert iou(a.bbox, b.bbox) == 0.2
    assert containment(a.bbox, b.bbox) == 0.5
    assert [i.elements for i in find_issues([a, b])] == [["e1", "e2"]]


def test_overlap_by_containment_when_iou_is_low():
    # Spec 7.8: a wide heading box crossing a small menu-link box has IoU well under 0.2, so the share of the
    # smaller box that is covered decides. Boundaries: 0.29 no, 0.3 yes, 0.9 no.
    assert (OVERLAP_IOU, OVERLAP_MIN_CONTAINMENT, CONTAINMENT_LIMIT) == (0.2, 0.3, 0.9)
    heading = _el("e1", "HEADING", [0, 50, 400, 150])
    link = _el("e2", "LINK", [300, 0, 400, 100])          # inter 5000 of 10000: containment 0.5, IoU 0.11
    assert iou(heading.bbox, link.bbox) < OVERLAP_IOU
    assert [(i.elements, i.detail) for i in find_issues([heading, link])] == [(["e1", "e2"], "HEADING 과 LINK 가 겹침 (IoU 11%, 작은 쪽 50% 덮임)")]

    # The 0.9 nesting limit applies to pairs where nesting is legitimate (here a NAV holding links).
    big = _el("e3", "NAV", [0, 0, 1000, 100])             # 100000, so IoU stays tiny for every case below
    at_29 = _el("e4", "LINK", [0, 71, 100, 171])          # inter 100*29 = 2900 of 10000
    at_30 = _el("e5", "LINK", [0, 70, 100, 170])          # 3000 of 10000
    at_90 = _el("e6", "LINK", [0, 10, 100, 110])          # 9000 of 10000: nesting, not collision
    assert containment(big.bbox, at_29.bbox) == pytest.approx(0.29)
    assert containment(big.bbox, at_30.bbox) == pytest.approx(0.30)
    assert containment(big.bbox, at_90.bbox) == pytest.approx(0.90)
    assert find_issues([big, at_29]) == []
    assert [i.elements for i in find_issues([big, at_30])] == [["e3", "e5"]]
    assert find_issues([big, at_90]) == []


def test_leaf_fully_covered_by_another_leaf_is_overlap():
    # A heading that completely covers a menu link is the v1 fault: full containment counts for leaf pairs.
    heading = _el("e1", "HEADING", [350, 10, 650, 90])
    link = _el("e2", "LINK", [420, 50, 460, 70])
    assert containment(heading.bbox, link.bbox) == 1.0
    assert [(i.elements, i.detail) for i in find_issues([heading, link])] == [
        (["e1", "e2"], "HEADING 과 LINK 가 겹침 (IoU 3%, 작은 쪽 100% 덮임)"),
    ]
    a = _el("e3", "BUTTON", [0, 0, 100, 100])
    b = _el("e4", "LINK", [0, 0, 100, 500])         # IoU 0.2 and a sits entirely inside b
    assert [i.elements for i in find_issues([a, b])] == [["e3", "e4"]]


def test_expected_nesting_between_leaves_is_not_overlap():
    # An icon inside a button, placeholder text inside an input, and the same label reported twice
    # (a BUTTON and its TEXT) are one element seen twice, not a collision.
    button = _el("e1", "BUTTON", [0, 0, 100, 40], text="메뉴 보기")
    icon = _el("e2", "ICON", [5, 10, 25, 30])
    assert find_issues([button, icon]) == []
    field = _el("e3", "INPUT", [0, 100, 300, 140])
    placeholder = _el("e4", "TEXT", [10, 110, 120, 130], text="검색어 입력")
    assert find_issues([field, placeholder]) == []
    label = _el("e5", "TEXT", [20, 8, 80, 32], text="메뉴  보기")
    assert find_issues([button, label]) == []
    other = _el("e6", "TEXT", [20, 8, 80, 32], text="다른 글자")
    assert [i.elements for i in find_issues([button, other])] == [["e1", "e6"]]


def test_containment_limit_applies_only_to_nesting_pairs():
    big = _el("e1", "INPUT", [0, 0, 100, 100])
    nested = _el("e2", "ICON", [0, 0, 100, 10])      # 1000 inside 10000: IoU 0.1 anyway
    assert find_issues([big, nested]) == []
    nav = _el("e3", "NAV", [0, 0, 100, 100])
    at_90 = _el("e4", "LINK", [0, 10, 100, 110])     # inter 9000 / smaller 10000 = 0.9: nesting
    assert containment(nav.bbox, at_90.bbox) == pytest.approx(0.9)
    assert find_issues([nav, at_90]) == []
    at_89 = _el("e5", "LINK", [0, 11, 100, 111])     # 0.89: reported
    assert [i.elements for i in find_issues([nav, at_89])] == [["e3", "e5"]]
    # Two plain leaves at the same 0.9 are reported: nothing nests legitimately in a TEXT.
    a = _el("e6", "TEXT", [0, 0, 100, 100])
    b = _el("e7", "TEXT", [0, 10, 100, 110])
    assert [i.elements for i in find_issues([a, b])] == [["e6", "e7"]]


def test_containers_are_excluded_from_overlap():
    card = _el("e1", "CARD", [0, 0, 100, 100])
    nav = _el("e2", "NAV", [50, 0, 150, 100])
    text = _el("e3", "TEXT", [50, 0, 150, 100])
    assert find_issues([card, nav]) == []
    assert find_issues([card, text]) == []
    assert find_issues([text, _el("e4", "IMAGE", [0, 0, 100, 100])]) == []
    assert find_issues([text, _el("e5", "LIST_ITEM", [0, 0, 100, 100])]) == []
    assert find_issues([text, _el("e6", "OTHER", [0, 0, 100, 100])]) == []


def test_leaf_colliding_with_nav_or_dialog_is_reported():
    # Spec 7.1: a heading sliding under the menu bar is the v1 bug, so leaf x NAV/DIALOG counts.
    nav = _el("e1", "NAV", [0, 0, 500, 100])
    heading = _el("e2", "HEADING", [40, 50, 460, 150])        # inter 21000, union 71000: IoU 0.30
    dialog = _el("e3", "DIALOG", [300, 300, 700, 700])
    button = _el("e4", "BUTTON", [250, 250, 350, 350])        # corner under the dialog, IoU 0.015
    text = _el("e5", "TEXT", [500, 280, 900, 720])            # half under the dialog

    issues = find_issues([nav, heading, dialog, button, text])

    assert [(i.elements, i.detail) for i in issues] == [
        (["e1", "e2"], "NAV 과 HEADING 가 겹침 (IoU 30%, 작은 쪽 50% 덮임)"),
        (["e3", "e5"], "DIALOG 과 TEXT 가 겹침 (IoU 31%, 작은 쪽 50% 덮임)"),
    ]

    # A menu item fully inside its NAV is containment, not overlap, and two containers never pair.
    link = _el("e6", "LINK", [10, 10, 90, 90])
    assert find_issues([nav, link]) == []
    assert find_issues([nav, _el("e7", "DIALOG", [100, 0, 600, 100])]) == []
    assert find_issues([_el("e8", "CARD", [0, 0, 500, 100]), heading]) == []


def test_size_issues_come_first_and_are_kept():
    oob = Issue(kind="out_of_bounds", elements=["e1"], detail="x")
    a = _el("e1", "TEXT", [0, 0, 100, 100])
    b = _el("e2", "TEXT", [50, 0, 150, 100])
    issues = find_issues([a, b], [oob])
    assert issues[0] == oob
    assert [i.kind for i in issues] == ["out_of_bounds", "overlap"]


def test_find_issues_with_no_elements():
    assert find_issues([]) == []
    assert find_issues([], []) == []
