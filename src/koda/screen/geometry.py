from __future__ import annotations

import math
from typing import TYPE_CHECKING, Sequence

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .schema import Element

Box = Sequence[int]

# Leaf elements are the ones that should never visually collide. Containers hold other
# elements by design, so overlap between two containers, or between a container and the leaves
# it holds, is expected. A leaf that collides with a NAV or DIALOG is still a layout fault (a
# heading sliding under the menu bar), so those pairs are checked as well.
LEAF_TYPES = frozenset({
    "TEXT", "HEADING", "BUTTON", "LINK", "INPUT", "CHECKBOX", "RADIO", "SWITCH", "DROPDOWN", "ICON",
})
CONTAINER_TYPES = frozenset({"CARD", "NAV", "DIALOG", "LIST_ITEM", "IMAGE", "OTHER"})
OVERLAP_CONTAINER_TYPES = frozenset({"NAV", "DIALOG"})

# A large heading box and a small menu-link box that collide keep IoU under 0.2 (about 0.13 in the
# v1 fixture), so the share of the smaller box that is covered counts as well. Containment at or
# above CONTAINMENT_LIMIT is nesting, not collision.
OVERLAP_IOU = 0.2
OVERLAP_MIN_CONTAINMENT = 0.3
CONTAINMENT_LIMIT = 0.9

# Leaf types whose box legitimately holds another leaf: an icon inside a button, placeholder text
# inside an input. Pairs with one of these are judged like container pairs, where nesting is not a
# collision. Every other leaf pair counts even when one box fully covers the other, because a
# heading that completely covers a menu link is exactly the fault the check exists for.
NESTING_LEAF_TYPES = frozenset({"ICON", "INPUT"})


class Issue(BaseModel):
    """A problem found by code from bboxes alone (overlap, out_of_bounds)."""

    kind: str
    elements: list[str] = Field(default_factory=list)
    detail: str = ""


def area(box: Box) -> int:
    x0, y0, x1, y1 = box
    return max(0, x1 - x0) * max(0, y1 - y0)


def intersection_area(a: Box, b: Box) -> int:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0
    return w * h


def iou(a: Box, b: Box) -> float:
    inter = intersection_area(a, b)
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def containment(a: Box, b: Box) -> float:
    """Fraction of the smaller box that lies inside the larger one."""
    smaller = min(area(a), area(b))
    return intersection_area(a, b) / smaller if smaller > 0 else 0.0


def center(box: Box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def center_distance(a: Box, b: Box) -> float:
    (ax, ay), (bx, by) = center(a), center(b)
    return math.hypot(ax - bx, ay - by)


def is_overlap(a: Box, b: Box, nested_ok: bool = False) -> bool:
    """Spec 7.8: IoU >= 0.2 or containment >= 0.3. With nested_ok, containment >= 0.9 is nesting."""
    c = containment(a, b)
    if not (iou(a, b) >= OVERLAP_IOU or c >= OVERLAP_MIN_CONTAINMENT):
        return False
    return not (nested_ok and c >= CONTAINMENT_LIMIT)


def _squash(text: str) -> str:
    return " ".join((text or "").split())


def _nesting_allowed(a: Element, b: Element) -> bool:
    """True for pairs where one box sitting inside the other is expected rather than a collision."""
    if a.type not in LEAF_TYPES or b.type not in LEAF_TYPES:
        return True
    if a.type in NESTING_LEAF_TYPES or b.type in NESTING_LEAF_TYPES:
        return True
    ta, tb = _squash(a.text), _squash(b.text)
    return bool(ta) and ta == tb  # the same label reported twice (a BUTTON and its TEXT) is one element


def find_issues(elements: Sequence[Element], size_issues: Sequence[Issue] = ()) -> list[Issue]:
    """Return the size issues followed by overlap issues: leaf with leaf, and leaf with NAV or DIALOG."""
    issues = list(size_issues)
    candidates = [e for e in elements if e.type in LEAF_TYPES or e.type in OVERLAP_CONTAINER_TYPES]
    for i, a in enumerate(candidates):
        for b in candidates[i + 1:]:
            if a.type not in LEAF_TYPES and b.type not in LEAF_TYPES:
                continue
            if is_overlap(a.bbox, b.bbox, nested_ok=_nesting_allowed(a, b)):
                pct = round(iou(a.bbox, b.bbox) * 100)
                covered = round(containment(a.bbox, b.bbox) * 100)
                issues.append(Issue(
                    kind="overlap",
                    elements=[a.id, b.id],
                    detail=f"{a.type} 과 {b.type} 가 겹침 (IoU {pct}%, 작은 쪽 {covered}% 덮임)",
                ))
    return issues
