from __future__ import annotations

import math
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, Field, field_validator

from .geometry import Issue, find_issues

ElementType = Literal[
    "BUTTON", "LINK", "TEXT", "HEADING", "INPUT", "CHECKBOX", "RADIO", "SWITCH",
    "DROPDOWN", "IMAGE", "ICON", "CARD", "LIST_ITEM", "NAV", "DIALOG", "OTHER",
]
ELEMENT_TYPES: tuple[str, ...] = get_args(ElementType)

COORD_MAX = 1000
# Raw model coordinates are clamped here so that scaling and center math stay within float range.
BBOX_LIMIT = 10_000_000

# Free-form names observed from models without grammar enforcement, mapped case-insensitively.
_TYPE_ALIASES: dict[str, ElementType] = {
    "navigation_link": "LINK", "nav_link": "LINK", "menu_link": "LINK", "anchor": "LINK", "hyperlink": "LINK", "a": "LINK",
    "product_card": "CARD", "tile": "CARD", "panel": "CARD",
    "paragraph": "TEXT", "price": "TEXT", "label": "TEXT", "caption": "TEXT", "span": "TEXT", "p": "TEXT",
    "body_text": "TEXT", "description": "TEXT", "subtitle": "TEXT",
    "title": "HEADING", "header": "HEADING", "h1": "HEADING", "h2": "HEADING", "h3": "HEADING",
    "h4": "HEADING", "h5": "HEADING", "h6": "HEADING",
    "btn": "BUTTON", "cta": "BUTTON", "submit": "BUTTON",
    "textbox": "INPUT", "text_input": "INPUT", "textarea": "INPUT", "field": "INPUT", "search": "INPUT", "search_box": "INPUT",
    "radio_button": "RADIO", "radiobutton": "RADIO",
    "check_box": "CHECKBOX", "toggle": "SWITCH",
    "select": "DROPDOWN", "combobox": "DROPDOWN", "combo_box": "DROPDOWN",
    "img": "IMAGE", "picture": "IMAGE", "photo": "IMAGE", "logo": "IMAGE", "illustration": "IMAGE",
    "navigation": "NAV", "navbar": "NAV", "nav_bar": "NAV", "menu": "NAV", "menu_bar": "NAV",
    "modal": "DIALOG", "popup": "DIALOG", "alert": "DIALOG",
    "listitem": "LIST_ITEM", "li": "LIST_ITEM", "item": "LIST_ITEM",
}


_TYPE_LOOKUP: dict[str, ElementType] = {t.lower(): t for t in get_args(ElementType)} | _TYPE_ALIASES


def coerce_type(value: Any) -> ElementType:
    """Map a model-given type name onto the 16 known types, OTHER when nothing matches."""
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key in _TYPE_LOOKUP:
        return _TYPE_LOOKUP[key]
    # "submit_button", "hero_image": the last token usually names the kind. A one-letter tail
    # ("option_a", "label_p") is an enumeration suffix, not a kind, so the aliases "a" and "p" must not apply.
    tail = key.rsplit("_", 1)[-1]
    if len(tail) < 2:
        return "OTHER"
    return _TYPE_LOOKUP.get(tail, "OTHER")


def normalize_text(s: str) -> str:
    """Collapse whitespace runs (including newlines) to one space and trim; case is kept."""
    return " ".join(str(s or "").split())


def _coerce_bbox(value: Any) -> list:
    """Make a lenient endpoint's bbox safe for int validation and later arithmetic.

    Floats are rounded and magnitudes clamped to BBOX_LIMIT. A missing bbox, a non-list, or a
    non-finite number (JSON 1e400 parses to inf) gives [] so normalize_parse drops that one
    element instead of the whole parse failing validation.
    """
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for v in value:
        if isinstance(v, float):
            if not math.isfinite(v):
                return []
            v = int(round(v))
        if isinstance(v, int) and not isinstance(v, bool):
            v = max(-BBOX_LIMIT, min(BBOX_LIMIT, v))
        out.append(v)
    return out


class ElementOut(BaseModel):
    """One element as the vision model reports it."""

    type: ElementType = Field(description="요소 종류. 16종 중 하나")
    text: str = Field(default="", description="보이는 글자 그대로. 글자가 없으면 빈 문자열")
    bbox: list[int] = Field(description="[x0, y0, x1, y1]. 좌표는 이미지 전체를 0~1000 으로 본 정규화 값")
    state: str = Field(default="", description="enabled·disabled·checked·focused 처럼 보이는 상태. 없으면 빈 문자열")

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v: Any) -> str:
        return coerce_type(v)

    @field_validator("text", "state", mode="before")
    @classmethod
    def _none_to_empty(cls, v: Any) -> Any:
        return "" if v is None else v

    @field_validator("bbox", mode="before")
    @classmethod
    def _round_bbox(cls, v: Any) -> Any:
        return _coerce_bbox(v)


class ScreenParseOut(BaseModel):
    """Structured output schema for the parse_screen vision call."""

    summary: str = Field(description="화면 전체를 한 문장으로 요약 (한국어)")
    kind: str = Field(description="장면 종류: page, dialog, form, list, card_grid, error, other 중 하나")
    elements: list[ElementOut] = Field(default_factory=list, description="보이는 UI 요소 전부")


Coord = Annotated[int, Field(ge=0, le=COORD_MAX)]


class Element(BaseModel):
    """A normalized element stored in a ScreenParse: four coordinates, each within 0..1000."""

    id: str
    type: ElementType
    text: str = ""
    bbox: list[Coord] = Field(min_length=4, max_length=4)
    state: str = ""

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v: Any) -> str:
        return coerce_type(v)


class Size(BaseModel):
    w: int
    h: int


class Scene(BaseModel):
    kind: str = ""
    summary: str = ""


ParseSource = Literal["render", "image"]


class ScreenParse(BaseModel):
    """Stored parse of one image: normalized elements plus code-computed issues.

    image_sha256 ties the parse to the exact bytes it came from, so a stale parse is never reused
    for a re-rendered or edited image. source says whether the image is a koda render or a file
    the user put in the workspace.
    """

    image: str
    size: Size
    scene: Scene
    elements: list[Element] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    image_sha256: str = ""
    source: ParseSource = "image"


def _uses_pixels(elements: list[ElementOut], size: tuple[int, int] | None) -> bool:
    """Pixel coordinates are assumed only on evidence: a value above 1000 that still fits the image
    on its own axis. A value no axis can hold is a normalized box that is merely out of range."""
    if size is None:
        return False
    w, h = size
    for e in elements:
        if len(e.bbox) != 4:
            continue
        x0, y0, x1, y1 = e.bbox
        if w > COORD_MAX and any(COORD_MAX < c <= w for c in (x0, x1)):
            return True
        if h > COORD_MAX and any(COORD_MAX < c <= h for c in (y0, y1)):
            return True
    return False


def normalize_parse(
    out: ScreenParseOut, image: str, size: tuple[int, int] | None,
    image_sha256: str = "", source: ParseSource = "image",
) -> ScreenParse:
    """Turn model output into a ScreenParse with 0..1000 bboxes, fresh ids and code-found issues.

    When the image size is known and some coordinate lies between 1000 and the image extent on
    that axis, all boxes are read as pixels and scaled by the size. Boxes outside the frame are
    clipped and reported as out_of_bounds. Elements without a 4-number bbox are dropped.

    An image at most 1000px on both axes gives no way to tell pixel coordinates from normalized
    ones, so they are kept as given.
    """
    w, h = size if size else (COORD_MAX, COORD_MAX)
    pixels = _uses_pixels(out.elements, size)
    limit_x, limit_y = (w, h) if pixels else (COORD_MAX, COORD_MAX)

    elements: list[Element] = []
    size_issues: list[Issue] = []
    for raw in out.elements:
        if len(raw.bbox) != 4:
            continue
        eid = f"e{len(elements) + 1}"
        x0, x1 = sorted((raw.bbox[0], raw.bbox[2]))
        y0, y1 = sorted((raw.bbox[1], raw.bbox[3]))
        out_of_bounds = x0 < 0 or y0 < 0 or x1 > limit_x or y1 > limit_y
        if pixels:
            x0, x1 = (_scale(v, w) for v in (x0, x1))
            y0, y1 = (_scale(v, h) for v in (y0, y1))
        bbox = [_clip(v) for v in (x0, y0, x1, y1)]
        etype = coerce_type(raw.type)
        if out_of_bounds:
            frame = f"{w}x{h}" if pixels else f"0~{COORD_MAX}"
            size_issues.append(Issue(
                kind="out_of_bounds",
                elements=[eid],
                detail=f"{etype} 좌표 {list(raw.bbox)} 가 {frame} 을 벗어남",
            ))
        elements.append(Element(id=eid, type=etype, text=raw.text, bbox=bbox, state=raw.state))

    return ScreenParse(
        image=image,
        size=Size(w=w, h=h),
        scene=Scene(kind=out.kind, summary=out.summary),
        elements=elements,
        issues=find_issues(elements, size_issues),
        image_sha256=image_sha256,
        source=source,
    )


def _scale(value: int, extent: int) -> int:
    return int(round(value * COORD_MAX / extent)) if extent > 0 else value


def _clip(value: int) -> int:
    return max(0, min(COORD_MAX, value))
