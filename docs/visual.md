# Screen evidence

**English** · [한국어](visual.ko.md) · back to the [README](../README.md)

koda can use the screen as evidence while it codes. The coder renders a page headless, parses a capture or a render with a vision model, and compares two screens in code; the verifier reads those results before the coder's own claims. There is no GUI-driving agent: the three tools only read the workspace and write under `.koda/renders/`.

[Scenarios](#scenarios) · [Tools](#tools) · [Files](#files) · [Install](#install) · [Settings](#settings) · [Limits](#limits)

## Scenarios

Requests go through the TUI or `koda chat` as usual. The router sends them down the edit path because they change code; an analysis-only question such as "what is broken in this capture?" goes to the read path, where `parse_screen` is available too.

| | Request | What the coder does |
|---|---|---|
| Screenshot bug fix | "The title overlaps the menu like in bug.png. Find the cause, fix it and render again to check." | `parse_screen(bug.png)` lists the elements and the overlap issue. The coder finds the source by the element's text, edits it, then `render_page` and `parse_screen` on the render confirm the issue is gone. |
| Refactor that must not change the screen | "The four cards are copy-pasted. Pull them into a data array and one template function. The screen must stay exactly the same." | `render_page(..., name="before")` before any edit, `render_page(..., name="after")` after it, then `screen_diff` must say `동등` (equal). A baseline taken after the first edit does not count. |
| Design gap | "Match design.png. Add the missing section and fix the text that differs." | `parse_screen(design.png)` gives the design's size, the coder renders the page at that viewport, and `screen_diff(design.png, render)` lists missing elements and text mismatches. What remains is reported, not hidden. |

How the verifier reads this evidence: [architecture.md](architecture.md#how-an-edit-is-judged).

## Tools

All three are read-only tools with no approval gate, given to every agent like `analyze_image`; the planner gets `parse_screen` only. Errors come back as a string starting with `오류:` instead of an exception, and the verifier treats such a call as if it had not happened.

### `render_page(target, viewport="1280x800", name="", full_page=False)`

- `target` is an HTML file inside the workspace (opened as `file://`) or a URL starting with `http://localhost` or `http://127.0.0.1`. Anything else is refused.
- Headless chromium, device scale factor 1, animations disabled, caret hidden, `Date` and `Math.random` pinned, so two renders of the same source are byte-identical. `viewport` is `WxH` with each side 200 to 2000 and at most 2.4 megapixels; `full_page=True` captures the document up to 4000px high. A PNG over 5MB is discarded with an error.
- Network: for a file target only `file:` requests under the workspace (minus `.koda/`, except `.koda/renders/`) and `data:` URLs load; for a localhost target only the same origin, with redirects checked against the same rule. Everything else is aborted and counted as blocked; popups are closed, and service workers and WebSockets never connect.
- Output: `<name>.png`, `<name>.dom.json` (visible DOM boxes with type, text and pixel bbox) and `<name>.meta.json`. `name` defaults to the target path with `/` replaced by `_` (`site_index`), or `host_port` for a URL. Rendering over the same name replaces the files and drops the old parse.
- First line: `렌더 완료 · .koda/renders/<name>.png · 1280x800 · 콘솔 에러 N건 · 외부 차단 M건 · DOM 요소 K개`, then the file URL, the DOM file path and up to ten console errors.

### `parse_screen(path, focus="")`

- `path` is a workspace image (`png`, `jpg`, `jpeg`, `webp`, up to 5MB) or a render under `.koda/renders/`.
- The vision model returns a scene summary and up to 80 elements of 16 types (`BUTTON`, `LINK`, `TEXT`, `HEADING`, `INPUT`, `CHECKBOX`, `RADIO`, `SWITCH`, `DROPDOWN`, `IMAGE`, `ICON`, `CARD`, `LIST_ITEM`, `NAV`, `DIALOG`, `OTHER`) with the visible text and a bbox normalized to 0 to 1000. Code renumbers the ids, clamps the boxes and computes the issues (`overlap` between leaf elements, `out_of_bounds`); the model is never asked for issues.
- The result is saved as `.koda/renders/<name>.parse.json` and reused when the same bytes are parsed again. `focus` asks the model to cover one area or topic exhaustively and skips the cache.
- First line: `요소 N개 · 이슈 M개 · 크기 WxH · 저장 .koda/renders/<name>.parse.json`, then the scene, one line per element (`e1 HEADING "오늘의 추천" [40,120,420,180]`) and the issues.

### `screen_diff(before, after, tolerance=0)`

- Inputs are render PNGs, workspace images or `.parse.json` paths.
- Identical PNG bytes end the call at once with `동등 · 픽셀 동일`. Two renders with DOM boxes are compared in pixels (`DOM 비교`, default tolerance 2px). When either input is a workspace image, both are parsed with the vision model and compared in the normalized space (`VLM 비교`, default tolerance 30), which requires the same image size.
- DOM diffs match elements by type and text, then by proximity. VLM diffs match by normalized text alone, then by proximity, and report a pair whose types differ separately as `type_changed`, along with text-less containers present on one side only as `container_diff`; neither affects `equal`, and the second line lists their counts (`참고: type 변경 t · 컨테이너 차이 c`). The result lists `missing`, `added`, `text_changed` and `moved`, and `equal` holds only when all four are empty. The first line is the verdict: `동등 · DOM 비교 · 요소 N/N 일치 · 이동 0 · 글자 변경 0 (허용 2px)` or `차이 K건 · VLM 비교 · 누락 a · 추가 b · 글자 변경 c · 이동 d (허용 30)`.
- Two renders of an unchanged workspace carry a `소스 동일(리팩토링 증거 아님)` mark. A file under `.koda/renders/` that changed outside koda is refused; one left by an earlier run is accepted with a note.
- Saved as `.koda/renders/<before>__<after>.diff.json`.

## Files

Everything lives in `<workspace>/.koda/renders/`: `<name>.png`, `<name>.dom.json`, `<name>.meta.json`, `<name>.parse.json` (a workspace image is saved as `src__<path>.parse.json`) and `<before>__<after>.diff.json`. `.koda` is excluded from the workspace diff the verifier reads, and `read_file` cannot open these files; agents pass the paths from one tool to the next.

## Install

The tools need the `visual` extra (Playwright) and a chromium build:

```sh
uv sync --extra tui --extra visual
.venv/bin/python -m playwright install chromium
```

Without them `render_page` returns an error naming those two commands; `parse_screen` and `screen_diff` still work on images. For a uv tool install, add `visual` to the extras (`"koda[tui,visual] @ git+https://github.com/DONGRYEOLLEE1/koda"`) and run `python -m playwright install chromium` with the interpreter that has Playwright.

## Settings

Image calls (`parse_screen`, `analyze_image`) go to the fast model (the primary model with thinking off) unless a separate vision model is set:

| Variable | Default | Meaning |
|---|---|---|
| `VISION_MODEL` | empty | Model for image calls. Empty means the fast model (the primary model without thinking) takes them. |
| `VISION_BASE_URL` | empty | Endpoint for that model; empty uses the URL of the provider in use. It is also where koda probes `/props` for llama.cpp vision support. |
| `VISION_API_KEY` | empty | Key for that endpoint; empty uses the key of the provider in use. |
| `VISION_STRUCTURED_METHOD` | empty | `json_schema`, `function_calling` or `auto` for the vision model alone; empty follows `STRUCTURED_METHOD`. A failure here never switches the text calls off their method. |

Measured on a local llama.cpp server (Qwen3.6-35B-A3B with its mmproj): one 1280x800 parse took about 43 seconds and returned 21 elements with correct Korean text.

## Limits

- Colors and fonts are not compared. The diff sees element types, text and positions only.
- An `overlap` issue is raised when two leaf elements collide: IoU 0.2 or more, or 30% of the smaller box covered. For leaf pairs full containment counts too, since a heading that swallows a menu link whole is exactly the fault being looked for. The 90% nesting exception applies only where nesting is legitimate: a leaf inside a NAV or DIALOG, an ICON or INPUT holding another leaf, or the same label reported twice.
- A localhost server that sends the page without an HTML content type (none, `text/plain`, `application/octet-stream`) still gets rendered as HTML; the original type is printed as a warning line. Opening the file path directly avoids the problem.
- Vision model coordinates carry error. A VLM diff reports `moved` and type changes, but the verifier trusts only missing elements and text changes from it; DOM diffs between two renders are exact.
- If the model answers in pixels, any coordinate above 1000 makes koda read the box as pixels; a small box that stays under 1000 in pixels is read as normalized and lands in the wrong place.
- The render blocks every external network request. A page that loads CSS, fonts or scripts from a CDN renders without them; vendor those files into the workspace.
- Pixel-identical renders prove equality, but a pixel difference is not evidence of a layout difference. Compare with `screen_diff`.
- A design taller than the viewport is rendered with the viewport height set to the design height, not with `full_page`, so that the two sizes match.
- The TUI shows the tool lines (`렌더 · ...`, `화면 분석 · ...`, `화면 비교 · ...`) but does not display images.
- Images outside the workspace are not read; copy them into the workspace first.
