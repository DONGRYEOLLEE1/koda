from __future__ import annotations

import math
import unicodedata
from typing import Callable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .dom import DomBox
from .geometry import center_distance
from .schema import Element, ScreenParse, normalize_text
from .store import parse_name as _parse_file_name

DEFAULT_TOLERANCE = 30
MIN_TOLERANCE = 5
MAX_TOLERANCE = 200
DEFAULT_TOLERANCE_PX = 2
MAX_TOLERANCE_PX = 200
MAX_LINES_PER_KIND = 20
_TEXT_WIDTH = 60

DiffMethod = Literal["pixel", "dom", "vlm"]
_METHOD_LABEL = {"pixel": "픽셀 동일", "dom": "DOM 비교", "vlm": "VLM 비교"}
SOURCE_IDENTICAL_NOTE = "소스 동일(리팩토링 증거 아님)"
STALE_INPUTS_NOTE = "이전 실행 산출물"

# Types whose text-less instances are grouping or decoration. A VLM reports them inconsistently
# between two parses of near-identical screens, so their missing/added go to container_diff.
CONTAINER_DIFF_TYPES = frozenset({"CARD", "NAV", "LIST_ITEM", "DIALOG", "IMAGE", "ICON", "OTHER"})


class DiffElement(BaseModel):
    """An element present on only one side."""

    id: str
    type: str
    text: str = ""
    bbox: list[int] = Field(default_factory=list)


class ContainerDiff(DiffElement):
    """A text-less container present on only one side."""

    side: Literal["missing", "added"]


class Change(BaseModel):
    """A matched pair whose value differs. Dump with by_alias=True to get the "from" key."""

    # populate_by_name exists since pydantic 2.0; the 2.11 validate_by_* keys would be silently
    # ignored on the 2.7 floor this package inherits from langchain.
    model_config = ConfigDict(populate_by_name=True)

    before: str
    after: str
    from_: str = Field(alias="from")
    to: str


class TextChange(Change):
    """A matched pair whose visible text differs."""


class TypeChange(Change):
    """A pair matched by text whose element types differ."""


class Move(BaseModel):
    """A matched pair whose centers (or, for DOM boxes, sizes) differ by more than the tolerance."""

    before: str
    after: str
    delta: int


class DiffResult(BaseModel):
    before: str
    after: str
    method: DiffMethod = "vlm"
    tolerance: int = DEFAULT_TOLERANCE
    tolerance_px: int = 0
    pixel_identical: bool = False
    equal: bool = False
    matched: int = 0
    missing: list[DiffElement] = Field(default_factory=list)
    added: list[DiffElement] = Field(default_factory=list)
    text_changed: list[TextChange] = Field(default_factory=list)
    moved: list[Move] = Field(default_factory=list)
    type_changed: list[TypeChange] = Field(default_factory=list)
    container_diff: list[ContainerDiff] = Field(default_factory=list)
    source_identical: bool = False
    stale_inputs: bool = False
    summary: str = ""


def clamp_tolerance(tolerance: int) -> int:
    """Clip the center-distance tolerance to 5..200 in the 0..1000 coordinate space."""
    return max(MIN_TOLERANCE, min(MAX_TOLERANCE, int(tolerance)))


def clamp_tolerance_px(tolerance_px: int) -> int:
    """Pixel tolerance for DOM comparison: 0 or less means the 2px default."""
    t = int(tolerance_px)
    return DEFAULT_TOLERANCE_PX if t <= 0 else min(MAX_TOLERANCE_PX, t)


def parse_name(parse: ScreenParse) -> str:
    """The parse file name a ScreenParse is stored under, without the directory."""
    return _parse_file_name(parse.image)


def match_key(text: str) -> str:
    """Loose text identity for VLM parses: whitespace and punctuation dropped, case folded."""
    return "".join(
        ch for ch in str(text or "").casefold()
        if not ch.isspace() and not unicodedata.category(ch).startswith("P")
    )


def _greedy_pairs(before: Sequence, after: Sequence, max_distance: float | None, box: Callable) -> list[tuple]:
    """1:1 pairing by smallest center distance. Ties break on element order, so the result is deterministic."""
    candidates = []
    for i, b in enumerate(before):
        for j, a in enumerate(after):
            d = center_distance(box(b), box(a))
            if max_distance is None or d <= max_distance:
                candidates.append((d, i, j))
    candidates.sort()
    used_b: set[int] = set()
    used_a: set[int] = set()
    pairs = []
    for d, i, j in candidates:
        if i in used_b or j in used_a:
            continue
        used_b.add(i)
        used_a.add(j)
        pairs.append((before[i], after[j], d))
    return pairs


def _group(items: Sequence, key: Callable) -> dict:
    groups: dict = {}
    for e in items:
        groups.setdefault(key(e), []).append(e)
    return groups


def _match_groups(before: Sequence, after: Sequence, key: Callable, max_distance: float | None, box: Callable) -> list[tuple]:
    groups_a = _group(after, key)
    pairs = []
    for k, bs in _group(before, key).items():
        if k in groups_a:
            pairs.extend(_greedy_pairs(bs, groups_a[k], max_distance, box))
    return pairs


def _without(items: Sequence, taken: Sequence) -> list:
    ids = {id(e) for e in taken}
    return [e for e in items if id(e) not in ids]


def summarize(result: DiffResult) -> str:
    """The fixed first line: verdict, method, counts and tolerance. ToolLog keeps only the first lines."""
    if result.method == "pixel":
        line = "동등 · 픽셀 동일"
    else:
        label = _METHOD_LABEL[result.method]
        tol = f"허용 {result.tolerance_px}px" if result.method == "dom" else f"허용 {result.tolerance}"
        if result.equal:
            n = result.matched
            line = f"동등 · {label} · 요소 {n}/{n} 일치 · 이동 0 · 글자 변경 0 ({tol})"
        else:
            a, b = len(result.missing), len(result.added)
            c, d = len(result.text_changed), len(result.moved)
            line = f"차이 {a + b + c + d}건 · {label} · 누락 {a} · 추가 {b} · 글자 변경 {c} · 이동 {d} ({tol})"
    if result.source_identical:
        line += f" · {SOURCE_IDENTICAL_NOTE}"
    return line


def _entry(e) -> DiffElement:
    return DiffElement(id=e.id, type=e.type, text=e.text, bbox=list(e.bbox))


def _finish(result: DiffResult) -> DiffResult:
    result.equal = not (result.missing or result.added or result.text_changed or result.moved)
    result.summary = summarize(result)
    return result


def diff_parses(
    before: ScreenParse, after: ScreenParse, tolerance: int = DEFAULT_TOLERANCE,
    source_identical: bool = False, stale_inputs: bool = False,
) -> DiffResult:
    """Compare two VLM parses.

    Step 1 pairs elements by loose text alone, type ignored, nearest centers first; a pair whose
    types differ is counted in type_changed. Step 2 pairs the rest by type within the tolerance
    and counts differing text as text_changed. What remains is missing or added, except text-less
    containers, which go to container_diff. equal looks at missing, added, text_changed and moved.
    """
    tolerance = clamp_tolerance(tolerance)
    result = DiffResult(
        before=parse_name(before), after=parse_name(after), method="vlm", tolerance=tolerance,
        source_identical=source_identical, stale_inputs=stale_inputs,
    )
    box = lambda e: e.bbox  # noqa: E731

    rest_b = list(before.elements)
    rest_a = list(after.elements)
    texted_b = [e for e in rest_b if match_key(e.text)]
    texted_a = [e for e in rest_a if match_key(e.text)]
    step1 = _match_groups(texted_b, texted_a, lambda e: match_key(e.text), None, box)
    for b, a, _ in step1:
        if b.type != a.type:
            result.type_changed.append(TypeChange(before=b.id, after=a.id, from_=b.type, to=a.type))
    rest_b = _without(rest_b, [b for b, _, _ in step1])
    rest_a = _without(rest_a, [a for _, a, _ in step1])

    step2 = _match_groups(rest_b, rest_a, lambda e: e.type, tolerance, box)
    for b, a, _ in step2:
        if normalize_text(b.text) != normalize_text(a.text):
            result.text_changed.append(TextChange(before=b.id, after=a.id, from_=b.text, to=a.text))
    rest_b = _without(rest_b, [b for b, _, _ in step2])
    rest_a = _without(rest_a, [a for _, a, _ in step2])

    matched = step1 + step2
    for b, a, d in matched:
        if d > tolerance:
            # ceil keeps the printed delta above the tolerance it just exceeded.
            result.moved.append(Move(before=b.id, after=a.id, delta=math.ceil(d)))

    def _is_container(e: Element) -> bool:
        return e.type in CONTAINER_DIFF_TYPES and not normalize_text(e.text)

    for side, rest in (("missing", rest_b), ("added", rest_a)):
        for e in rest:
            if _is_container(e):
                result.container_diff.append(ContainerDiff(side=side, **_entry(e).model_dump()))
            elif side == "missing":
                result.missing.append(_entry(e))
            else:
                result.added.append(_entry(e))
    result.matched = len(matched)
    return _finish(result)


def diff_dom(
    before_dom: Sequence[DomBox], after_dom: Sequence[DomBox], tolerance_px: int = DEFAULT_TOLERANCE_PX,
    before_name: str = "before.dom.json", after_name: str = "after.dom.json",
    source_identical: bool = False, stale_inputs: bool = False,
) -> DiffResult:
    """Compare two renders by DOM boxes in CSS px.

    Pairs by type and normalized text first (nearest centers), then by type within tolerance_px
    with the text difference recorded. A pair moved when its centers or its width or height differ
    by more than tolerance_px. Sizes may differ between the renders because the comparison is in px.
    """
    tolerance_px = clamp_tolerance_px(tolerance_px)
    result = DiffResult(
        before=before_name, after=after_name, method="dom", tolerance=0, tolerance_px=tolerance_px,
        source_identical=source_identical, stale_inputs=stale_inputs,
    )
    box = lambda e: e.bbox_px  # noqa: E731

    rest_b = list(before_dom)
    rest_a = list(after_dom)
    step1 = _match_groups(rest_b, rest_a, lambda e: (e.type, normalize_text(e.text)), None, box)
    rest_b = _without(rest_b, [b for b, _, _ in step1])
    rest_a = _without(rest_a, [a for _, a, _ in step1])

    step2 = _match_groups(rest_b, rest_a, lambda e: e.type, tolerance_px, box)
    for b, a, _ in step2:
        if normalize_text(b.text) != normalize_text(a.text):
            result.text_changed.append(TextChange(before=b.id, after=a.id, from_=b.text, to=a.text))
    rest_b = _without(rest_b, [b for b, _, _ in step2])
    rest_a = _without(rest_a, [a for _, a, _ in step2])

    matched = step1 + step2
    for b, a, d in matched:
        dw = abs((b.bbox_px[2] - b.bbox_px[0]) - (a.bbox_px[2] - a.bbox_px[0]))
        dh = abs((b.bbox_px[3] - b.bbox_px[1]) - (a.bbox_px[3] - a.bbox_px[1]))
        worst = max(d, dw, dh)
        if worst > tolerance_px:
            result.moved.append(Move(before=b.id, after=a.id, delta=math.ceil(worst)))
    result.missing = [DiffElement(id=e.id, type=e.type, text=e.text, bbox=list(e.bbox_px)) for e in rest_b]
    result.added = [DiffElement(id=e.id, type=e.type, text=e.text, bbox=list(e.bbox_px)) for e in rest_a]
    result.matched = len(matched)
    return _finish(result)


def pixel_identical_result(
    before: str, after: str, tolerance: int = DEFAULT_TOLERANCE,
    source_identical: bool = False, stale_inputs: bool = False,
) -> DiffResult:
    """Result for two images whose bytes hash the same: equal without any parsing."""
    result = DiffResult(
        before=before, after=after, method="pixel", tolerance=clamp_tolerance(tolerance),
        pixel_identical=True, equal=True, source_identical=source_identical, stale_inputs=stale_inputs,
    )
    result.summary = summarize(result)
    return result


def _short(text: str) -> str:
    text = normalize_text(text)
    return text if len(text) <= _TEXT_WIDTH else text[:_TEXT_WIDTH - 1] + "…"


def _capped(lines: list[str]) -> list[str]:
    if len(lines) <= MAX_LINES_PER_KIND:
        return lines
    return lines[:MAX_LINES_PER_KIND] + [f"... 외 {len(lines) - MAX_LINES_PER_KIND}개"]


def _element_line(label: str, e: DiffElement) -> str:
    return f"{label}: {e.id} {e.type} \"{_short(e.text)}\" [{','.join(map(str, e.bbox))}]"


def _ids(before: str, after: str) -> str:
    return before if before == after else f"{before}→{after}"


def notes(result: DiffResult) -> list[str]:
    """Second-line remarks: VLM side counts the verifier must not treat as differences, and stale inputs."""
    parts = []
    if result.method == "vlm":
        parts.append(f"type 변경 {len(result.type_changed)} · 컨테이너 차이 {len(result.container_diff)}")
    if result.stale_inputs:
        parts.append(STALE_INPUTS_NOTE)
    return parts


def render_diff(result: DiffResult) -> str:
    """Text for the model: the summary first, a note line when there is one, then up to 20 lines per kind."""
    lines = [result.summary]
    remarks = notes(result)
    if remarks:
        lines.append("참고: " + " · ".join(remarks))
    lines += _capped([_element_line("누락", e) for e in result.missing])
    lines += _capped([_element_line("추가", e) for e in result.added])
    lines += _capped([f"글자 변경: {_ids(c.before, c.after)} \"{_short(c.from_)}\" → \"{_short(c.to)}\"" for c in result.text_changed])
    lines += _capped([f"이동: {m.before}→{m.after} {m.delta}" for m in result.moved])
    lines += _capped([f"type 변경: {_ids(c.before, c.after)} {c.from_} → {c.to}" for c in result.type_changed])
    lines += _capped([_element_line(f"컨테이너 {'누락' if c.side == 'missing' else '추가'}", c) for c in result.container_diff])
    return "\n".join(lines)
