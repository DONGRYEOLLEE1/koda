"""Screen evidence for the coding agent: parse schema, bbox geometry, parse diff, headless render and the renders store."""

from __future__ import annotations

from .diff import (
    DEFAULT_TOLERANCE,
    DEFAULT_TOLERANCE_PX,
    MAX_TOLERANCE,
    MIN_TOLERANCE,
    ContainerDiff,
    DiffElement,
    DiffResult,
    Move,
    TextChange,
    TypeChange,
    clamp_tolerance,
    clamp_tolerance_px,
    diff_dom,
    diff_parses,
    match_key,
    pixel_identical_result,
    render_diff,
    summarize,
)
from .dom import DOM_BOXES_JS, EVALUATE_TIMEOUT_MS, TAG_TYPES, DomBox, dom_type, evaluate_isolated, extract_dom_boxes
from .geometry import (
    CONTAINER_TYPES,
    LEAF_TYPES,
    OVERLAP_CONTAINER_TYPES,
    OVERLAP_MIN_CONTAINMENT,
    Issue,
    center_distance,
    containment,
    find_issues,
    iou,
)
# render() itself is not re-exported: the name would shadow the koda.screen.render submodule.
from .render import DEFAULT_VIEWPORT, PLAYWRIGHT_MISSING, RenderError, RenderResult
from .schema import (
    ELEMENT_TYPES,
    Element,
    ElementOut,
    ElementType,
    Scene,
    ScreenParse,
    ScreenParseOut,
    Size,
    coerce_type,
    normalize_parse,
    normalize_text,
)
from .store import (
    IMAGE_EXTENSIONS,
    MAX_IMAGE_BYTES,
    MAX_PARSE_BYTES,
    RENDERS_DIR,
    DomDoc,
    RenderMeta,
    diff_path,
    file_sha256,
    image_size,
    is_render_path,
    load_dom,
    load_meta,
    load_parse,
    parse_name,
    parse_path,
    registry_status,
    renders_dir,
    resolve_image,
    resolve_meta,
    resolve_parse,
    save_diff,
    save_dom,
    save_meta,
    save_parse,
)

__all__ = [
    "CONTAINER_TYPES", "DEFAULT_TOLERANCE", "DEFAULT_TOLERANCE_PX", "DEFAULT_VIEWPORT", "DOM_BOXES_JS",
    "ELEMENT_TYPES", "EVALUATE_TIMEOUT_MS", "IMAGE_EXTENSIONS", "LEAF_TYPES", "MAX_IMAGE_BYTES", "MAX_PARSE_BYTES",
    "MAX_TOLERANCE", "MIN_TOLERANCE", "OVERLAP_CONTAINER_TYPES", "OVERLAP_MIN_CONTAINMENT", "PLAYWRIGHT_MISSING",
    "RENDERS_DIR", "TAG_TYPES",
    "ContainerDiff", "DiffElement", "DiffResult", "DomBox", "DomDoc", "Element", "ElementOut", "ElementType",
    "Issue", "Move", "RenderError", "RenderMeta", "RenderResult", "Scene", "ScreenParse", "ScreenParseOut",
    "Size", "TextChange", "TypeChange",
    "center_distance", "clamp_tolerance", "clamp_tolerance_px", "coerce_type", "containment", "diff_dom",
    "diff_parses", "diff_path", "dom_type", "evaluate_isolated", "extract_dom_boxes", "file_sha256", "find_issues",
    "image_size", "iou", "is_render_path", "load_dom", "load_meta", "load_parse", "match_key", "normalize_parse",
    "normalize_text", "parse_name", "parse_path", "pixel_identical_result", "registry_status",
    "render_diff", "renders_dir", "resolve_image", "resolve_meta", "resolve_parse", "save_diff", "save_dom",
    "save_meta", "save_parse", "summarize",
]
