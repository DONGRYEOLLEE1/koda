from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path

from langchain.tools import tool
from langchain_core.messages import HumanMessage

from ..llm import structured
from ..prompts import load_prompt
from ..screen import store
from ..screen.diff import (
    DEFAULT_TOLERANCE,
    STALE_INPUTS_NOTE,
    DiffResult,
    diff_dom,
    diff_parses,
    pixel_identical_result,
    render_diff,
)
from ..screen.dom import MAX_DOM_BOXES
from ..screen.render import DEFAULT_VIEWPORT, RenderError, render
from ..screen.geometry import find_issues
from ..screen.schema import Issue, ScreenParse, ScreenParseOut, normalize_parse, normalize_text
from ..workspace import SandboxError, Workspace
from .vision import _MM_PROJ_ERROR, _has_server_error, _vision_supported

MAX_LISTED_ELEMENTS = 80
MAX_CONSOLE_LINES = 10

OUTSIDE_WORKSPACE = "오류: 워크스페이스 안으로 복사한 뒤 그 경로를 넘겨라"
NO_STRUCTURED_OUTPUT = "오류: 이 엔드포인트는 구조화 출력을 지원하지 않는다"
IMAGE_REJECTED = "오류: 이 모델은 이미지 입력을 받지 않는다. VISION_MODEL 을 설정하라"
SERVER_ERROR = "오류: 화면 파싱 중 서버 오류가 발생했다. 다시 시도하거나 텍스트 정보로만 진행하라"
EXTERNALLY_MODIFIED = "오류: .koda/renders 의 파일이 koda 가 만든 것과 다르다(외부 수정)"
DOM_ONLY_BETWEEN_RENDERS = "오류: dom.json 은 렌더끼리만 비교한다. 렌더 PNG 경로를 넘겨라"
CACHED_NOTE = "참고: 같은 이미지의 기존 파싱을 재사용"
STALE_NOTE = f"참고: {STALE_INPUTS_NOTE}"

_MIME = {"jpg": "jpeg"}
_PERCENT = re.compile(r"(\d+)%")
# An HTTP 400 in the message, not any "400" (a 400ms timeout, a model named qwen-400b).
_HTTP_400 = re.compile(r"\b(?:error code|status(?: code)?|http)[: ]+400\b")


def _image_rejected(exc: Exception) -> bool:
    """True when the endpoint refused the image input itself, which no retry will fix."""
    if getattr(exc, "status_code", None) == 400:
        return True
    text = str(exc).lower()
    return bool(_HTTP_400.search(text)) or "image_url" in text or "image input" in text


def _first_line(text: object) -> str:
    return (str(text).strip().splitlines() or [""])[0][:300]


def _issue_line(issue: Issue) -> str:
    ids = "·".join(issue.elements)
    if issue.kind == "overlap":
        m = _PERCENT.search(issue.detail)
        return f"이슈: overlap {ids} ({m.group(0) if m else issue.detail})"
    return f"이슈: {issue.kind} {ids} ({issue.detail})"


@dataclass
class _Input:
    """One screen_diff argument after resolution."""

    kind: str  # parse, dom, render_png, image
    path: Path
    rel: str
    sha: str
    name: str
    stale: bool = False
    dom_doc: store.DomDoc | None = None
    meta: store.RenderMeta | None = None
    parse: ScreenParse | None = None

    @property
    def is_image(self) -> bool:
        return self.kind in ("render_png", "image")

    @property
    def is_render(self) -> bool:
        return self.kind in ("render_png", "dom")


def make_screen_tools(ws: Workspace, vision_model, props_url: str, structured_methods=None) -> list:
    """The three screen evidence tools bound to a workspace and a vision model.

    structured_methods pins the structured-output methods tried for the vision model (from
    VISION_STRUCTURED_METHOD); None keeps the global rule.
    """
    methods = tuple(structured_methods) if structured_methods else None

    def _sandbox_error(rel: str, e: SandboxError) -> str:
        return OUTSIDE_WORKSPACE if store.is_outside(ws, rel) else f"오류: {e}"

    def _parse_image(image: Path, focus: str = "") -> tuple[ScreenParse | None, str]:
        """Parse a resolved image with the vision model and save the parse. Returns (parse, error text)."""
        rel = store.relative_path(ws, image)
        sha = store.file_sha256(image)
        source = "render" if store.is_render_path(ws, image) else "image"
        if _vision_supported(props_url) is False:
            return None, _MM_PROJ_ERROR
        ext = image.suffix.lower().lstrip(".")
        instruction = load_prompt("screen_parse")
        if focus.strip():
            instruction += f"\n이 영역 또는 주제를 우선해 빠짐없이: {focus.strip()}"
        try:
            b64 = base64.b64encode(image.read_bytes()).decode("ascii")
        except OSError as e:
            return None, f"오류: 이미지 파일을 읽을 수 없다({type(e).__name__})."
        content = [
            {"type": "text", "text": instruction},
            {"type": "image_url", "image_url": {"url": f"data:image/{_MIME.get(ext, ext)};base64,{b64}"}},
        ]
        errors: list[Exception] = []
        try:
            out = structured(vision_model, ScreenParseOut, [HumanMessage(content=content)],
                             remember=False, methods=methods, errors=errors)
        except Exception as e:
            errors.append(e)
            out = None
        if out is None:
            if any(_image_rejected(e) for e in errors):
                return None, IMAGE_REJECTED
            if errors:
                return None, (f"오류: 화면 파싱 실패({type(errors[-1]).__name__}). "
                              "이 모델이나 엔드포인트가 이미지 입력을 지원하지 않을 수 있다")
            return None, NO_STRUCTURED_OUTPUT
        if _has_server_error(out.model_dump_json()):
            return None, SERVER_ERROR
        parse = normalize_parse(out, rel, store.image_size(image), image_sha256=sha, source=source)
        try:
            store.save_parse(ws, parse)
        except SandboxError as e:
            return None, f"오류: {e}"
        return parse, ""

    def _cached_parse(image: Path, sha: str) -> tuple[ScreenParse | None, bool, str]:
        """The stored parse of this image, only when it was made from these exact bytes.

        Returns (parse, stale, error). A parse this process wrote and that changed since is refused
        (spec 7.3 V4); one left by an earlier process is accepted and flagged stale.
        """
        path = store.parse_path(ws, image)
        if not path.is_file():
            return None, False, ""
        status = store.registry_status(path)
        if status == "modified":
            return None, False, EXTERNALLY_MODIFIED
        try:
            parse = store.load_parse(path)
        except (ValueError, OSError):
            return None, False, ""
        if parse.image_sha256 != sha:
            return None, False, ""
        # Issues are a pure function of the elements; recompute them so a parse stored by an older
        # overlap rule does not hide a fault the current rule reports.
        parse.issues = find_issues(parse.elements, [i for i in parse.issues if i.kind != "overlap"])
        return parse, status == "unknown", ""

    def _parse_text(parse: ScreenParse, cached: bool, stale: bool = False) -> str:
        rel_parse = f"{store.RENDERS_DIR}/{store.parse_name(parse.image)}"
        n, m = len(parse.elements), len(parse.issues)
        lines = [
            f"요소 {n}개 · 이슈 {m}개 · 크기 {parse.size.w}x{parse.size.h} · 저장 {rel_parse}",
            f"장면: {parse.scene.summary}",
        ]
        if cached:
            lines.append(CACHED_NOTE)
        if stale:
            lines.append(STALE_NOTE)
        for e in parse.elements[:MAX_LISTED_ELEMENTS]:
            lines.append(f'{e.id} {e.type} "{normalize_text(e.text)}" [{",".join(map(str, e.bbox))}]')
        if n > MAX_LISTED_ELEMENTS:
            lines.append(f"... 외 {n - MAX_LISTED_ELEMENTS}개")
        lines += [_issue_line(i) for i in parse.issues]
        return "\n".join(lines)

    @tool
    def parse_screen(path: str, focus: str = "") -> str:
        """워크스페이스 안 이미지(png, jpg, jpeg, webp)나 .koda/renders/ 의 렌더 PNG 를 비전 모델로 파싱해
        UI 요소 목록(타입·보이는 글자·0~1000 정규화 bbox)과 코드가 계산한 겹침·범위 이슈를 돌려준다.
        결과는 .koda/renders/<이름>.parse.json 에 저장되며 그 경로나 이미지 경로를 screen_diff 에 넘길 수 있다.
        같은 이미지를 다시 파싱하면 저장된 결과를 재사용한다. focus 를 주면 그 영역이나 주제를 우선해 빠짐없이 적는다."""
        store.next_ordinal()
        try:
            image = store.resolve_image(ws, path)
        except SandboxError as e:
            return _sandbox_error(path, e)
        except (ValueError, FileNotFoundError) as e:
            return f"오류: {e}"
        if store.is_render_path(ws, image) and store.registry_status(image) == "modified":
            return EXTERNALLY_MODIFIED
        sha = store.file_sha256(image)
        cached, stale, err = (None, False, "") if focus.strip() else _cached_parse(image, sha)
        if err:
            return err
        if cached is not None:
            return _parse_text(cached, cached=True, stale=stale)
        parse, err = _parse_image(image, focus)
        if err:
            return err
        return _parse_text(parse, cached=False)

    @tool
    def render_page(target: str, viewport: str = DEFAULT_VIEWPORT, name: str = "", full_page: bool = False) -> str:
        """워크스페이스 안 HTML 파일(file:// 로 연다)이나 http://localhost, http://127.0.0.1 URL 을 headless
        chromium 으로 렌더해 .koda/renders/<name>.png 와 DOM 박스(<name>.dom.json)를 저장한다. CDN 같은 외부
        네트워크와 워크스페이스 밖 파일은 차단된다. 첫 줄의 PNG 경로를 parse_screen 이나 screen_diff 에 넘겨라.
        viewport 는 WxH(각 200~2000), full_page 는 문서 전체(높이 4000px 상한). name 을 비우면 대상 경로에서 만든다."""
        try:
            r = render(ws, target, viewport, name, full_page)
        except RenderError as e:
            return f"오류: {e}"
        except SandboxError as e:
            return f"오류: {e}"
        except Exception as e:
            return f"오류: 렌더 실패({type(e).__name__}: {_first_line(e)})"
        # The DOM box count sits on the first line because the tool log keeps only that line for the
        # verifier: 25 boxes against 1 is what tells a rendered page from a blank or a text blob.
        head = (f"렌더 완료 · {r.rel_png} · {r.width}x{r.height} · 콘솔 에러 {len(r.console_errors)}건"
                f" · 외부 차단 {r.blocked}건 · DOM 요소 {len(r.boxes)}개")
        if r.height_capped:
            head += " · 높이 상한 적용"
        lines = [head, f"파일: {r.png.as_uri()}", f"DOM 요소 {len(r.boxes)}개 · {r.rel_dom}"]
        if r.content_type_overridden:
            lines.append(f"경고: 서버가 문서를 content-type {r.content_type or '(없음)'} 으로 보내 text/html 로 간주해 "
                         "렌더했다. 서버 설정을 확인하거나 HTML 파일 경로를 직접 넘겨라")
        lines += [f"콘솔: {m}" for m in r.console_errors[:MAX_CONSOLE_LINES]]
        if len(r.console_errors) > MAX_CONSOLE_LINES:
            lines.append(f"... 외 {len(r.console_errors) - MAX_CONSOLE_LINES}건")
        return "\n".join(lines)

    def _classify(rel: str) -> tuple[_Input | None, str]:
        rel = (rel or "").strip()
        try:
            if rel.endswith(store.PARSE_SUFFIX):
                p, kind = store.resolve_parse(ws, rel), "parse"
            elif rel.endswith(store.DOM_SUFFIX):
                p, kind = store.resolve_dom(ws, rel), "dom"
            else:
                p = store.resolve_image(ws, rel)
                kind = "render_png" if store.is_render_path(ws, p) else "image"
        except SandboxError as e:
            return None, _sandbox_error(rel, e)
        except (ValueError, FileNotFoundError) as e:
            return None, f"오류: {e}"
        inp = _Input(kind=kind, path=p, rel=store.relative_path(ws, p), sha=store.file_sha256(p), name=store.stem(p.name))
        if store.is_render_path(ws, p):
            status = store.registry_status(p)
            if status == "modified":
                return None, EXTERNALLY_MODIFIED
            inp.stale = status == "unknown"
        if inp.is_render:
            err = _load_sidecars(inp)
            if err:
                return None, err
        if kind == "parse":
            try:
                inp.parse = store.load_parse(p)
            except (ValueError, OSError) as e:
                return None, f"오류: 파싱 파일을 읽을 수 없다({type(e).__name__}): {rel}"
        return inp, ""

    def _load_sidecars(inp: _Input) -> str:
        """Read a render's dom.json and meta.json with the same registry and size rules as a direct
        input: a sidecar edited behind koda's back is refused, one from an earlier run marks the
        diff stale, and an oversized one is an error rather than a silent read. Returns the error text."""
        for suffix, resolve, attr in ((store.DOM_SUFFIX, store.resolve_dom, "dom_doc"),
                                      (store.META_SUFFIX, store.resolve_meta, "meta")):
            path = store.render_path(ws, inp.name, suffix)
            if not path.is_file():
                continue
            status = store.registry_status(path)
            if status == "modified":
                return EXTERNALLY_MODIFIED
            inp.stale = inp.stale or status == "unknown"
            try:
                resolve(ws, store.relative_path(ws, path))
            except (ValueError, FileNotFoundError, SandboxError) as e:
                return f"오류: {e}"
            try:
                doc = store.load_dom(path) if attr == "dom_doc" else store.load_meta(path)
            except (ValueError, OSError):
                doc = None
            if attr == "dom_doc" and doc is not None and len(doc.boxes) > MAX_DOM_BOXES:
                return f"오류: DOM 박스가 너무 많다({len(doc.boxes)}개). 최대 {MAX_DOM_BOXES}개."
            setattr(inp, attr, doc)
        return ""

    def _parse_for(inp: _Input) -> tuple[ScreenParse | None, str]:
        if inp.parse is not None:
            return inp.parse, ""
        cached, stale, err = _cached_parse(inp.path, inp.sha)
        if err:
            return None, err
        if cached is not None:
            inp.stale = inp.stale or stale
            return cached, ""
        return _parse_image(inp.path)

    @tool
    def screen_diff(before: str, after: str, tolerance: int = 0) -> str:
        """두 화면을 비교한다. 입력은 렌더 PNG(.koda/renders/<name>.png), 워크스페이스 이미지, .koda/renders/ 의 .parse.json 경로다.
        픽셀이 같으면 바로 동등이다. 둘 다 렌더면 DOM 박스로 px 단위 비교(tolerance 는 px, 0 이면 2px),
        하나라도 워크스페이스 이미지면 비전 파싱으로 비교한다(tolerance 는 0~1000 좌표의 중심 거리, 0 이면 30).
        첫 줄이 결론이며 결과는 .koda/renders/<before>__<after>.diff.json 에 저장된다."""
        store.next_ordinal()
        b, err = _classify(before)
        if err:
            return err
        a, err = _classify(after)
        if err:
            return err
        source_identical = bool(
            b.meta and a.meta and b.meta.source_digest and b.meta.source_digest == a.meta.source_digest
        )
        tol = int(tolerance)
        result: DiffResult
        if b.is_image and a.is_image and b.sha == a.sha:
            result = pixel_identical_result(
                store.parse_name(b.rel), store.parse_name(a.rel), tol or DEFAULT_TOLERANCE,
                source_identical=source_identical, stale_inputs=b.stale or a.stale,
            )
        elif b.is_render and a.is_render and b.dom_doc is not None and a.dom_doc is not None:
            result = diff_dom(
                b.dom_doc.boxes, a.dom_doc.boxes, tol,
                before_name=f"{b.name}{store.DOM_SUFFIX}", after_name=f"{a.name}{store.DOM_SUFFIX}",
                source_identical=source_identical, stale_inputs=b.stale or a.stale,
            )
        else:
            if b.kind == "dom" or a.kind == "dom":
                return DOM_ONLY_BETWEEN_RENDERS
            pb, err = _parse_for(b)
            if err:
                return err
            pa, err = _parse_for(a)
            if err:
                return err
            if (pb.size.w, pb.size.h) != (pa.size.w, pa.size.h):
                return (f"오류: 이미지 크기가 다르다({pb.size.w}x{pb.size.h} vs {pa.size.w}x{pa.size.h}). "
                        "같은 viewport·full_page 로 렌더하라")
            result = diff_parses(pb, pa, tol or DEFAULT_TOLERANCE, source_identical=source_identical,
                                 stale_inputs=b.stale or a.stale)
        try:
            store.save_diff(ws, result)
        except SandboxError as e:
            return f"오류: {e}"
        return render_diff(result)

    return [parse_screen, render_page, screen_diff]
