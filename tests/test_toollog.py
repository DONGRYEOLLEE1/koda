from __future__ import annotations

from koda.agents.toollog import ToolLog, _brief

RENDER_OK = ("렌더 완료 · .koda/renders/before.png · 1280x800 · 콘솔 에러 0건 · 외부 차단 0건\n"
             "파일: file:///ws/.koda/renders/before.png\nDOM 요소 12개 · .koda/renders/before.dom.json")
DIFF_EQUAL = "동등 · DOM 비교 · 요소 12/12 일치 · 이동 0 · 글자 변경 0 (허용 2px)"
DIFF_CHANGED = ("차이 2건 · DOM 비교 · 누락 1 · 추가 0 · 글자 변경 1 · 이동 0 (허용 2px)\n"
                '누락: d4 BUTTON "받기" [700,600,820,660]\n글자 변경: d2 "오늘의 추천" → "오늘 추천"')
BEFORE, AFTER = ".koda/renders/before.png", ".koda/renders/after.png"


def _run(log: ToolLog, name: str, args: dict, output: str, run_id: str) -> None:
    log.on_tool_start({"name": name}, "", run_id=run_id, inputs=args)
    log.on_tool_end(output, run_id=run_id)


def _screen_block(log: ToolLog) -> list[str]:
    ev = log.evidence()
    return ev[ev.index("[화면 증거]"):].splitlines()


def test_brief_reads_target_name_and_before_after():
    assert _brief("parse_screen", {"path": "shots/bug.png", "focus": ""}) == "shots/bug.png"
    assert _brief("render_page", {"target": "site/index.html", "name": "before"}) == "site/index.html → before"
    assert _brief("render_page", {"target": "http://localhost:5173", "name": ""}) == "http://localhost:5173"
    assert _brief("screen_diff", {"before": BEFORE, "after": AFTER, "tolerance": 0}) == f"{BEFORE} vs {AFTER}"
    assert _brief("search_text", {"query": "받기"}) == "받기"
    assert _brief("explore", {"question": "어디?"}) == "어디?"
    assert _brief("some_tool", {"name": "x"}) == ""      # a name alone is not a target
    assert _brief("read_file", None) == ""


def test_error_output_is_recorded_as_failure_for_every_tool():
    log = ToolLog()
    _run(log, "render_page", {"target": "site/index.html"},
         "오류: playwright 가 설치되어 있지 않다. uv sync --extra tui --extra visual 뒤 .venv/bin/python -m playwright install chromium 을 실행하라", "r1")
    _run(log, "edit_file", {"path": "a.py"}, "오류: old 가 파일에 없다", "r2")
    _run(log, "run_command", {"command": "pytest -q"}, "exit_code=1\nstdout:\n1 failed", "r3")
    assert [c[2] for c in log.calls] == [False, False, True]      # a non-zero exit code is not an 오류: output
    ev = log.evidence()
    assert "- #1 render_page(site/index.html)\n  → 실패: 오류: playwright 가 설치되어 있지 않다" in ev
    assert "- #2 edit_file(a.py)\n  → 실패: 오류: old 가 파일에 없다" in ev
    assert "- #3 run_command(pytest -q)\n  → 성공: exit_code=1 · 1 failed" in ev
    assert log.screen_calls[0].ok is False
    assert "render_page 성공 0/실패 1 · parse_screen 성공 0/실패 0 · screen_diff 성공 0/실패 0" in ev


def test_ordinals_count_every_call_in_completion_order():
    log = ToolLog()
    _run(log, "read_file", {"path": "a.py"}, "x", "r1")
    log.on_tool_start({"name": "edit_file"}, "", run_id="r2", inputs={"path": "a.py"})
    log.on_tool_error(RuntimeError("boom"), run_id="r2")
    _run(log, "write_file", {"path": "b.py"}, "wrote", "r3")
    assert [c[2] for c in log.calls] == [True, False, True]
    assert log.first_edit_ordinal == 2
    ev = log.evidence()
    assert ev.startswith("- #1 read_file(a.py)\n  → 성공: x")
    assert "- #2 edit_file(a.py)\n  → 실패: RuntimeError: boom" in ev
    assert "- #3 write_file(b.py)\n  → 성공: wrote" in ev


def test_screen_evidence_block_is_always_appended():
    log = ToolLog()
    assert log.evidence() == ("(없음: 이 턴에서 실행된 도구가 하나도 없다)\n\n"
                              "[화면 증거]\n(없음: 이 턴에서 화면 도구 호출이 없다)")
    _run(log, "edit_file", {"path": "a.py"}, "ok", "r1")
    ev = log.evidence()
    assert "- #1 edit_file(a.py)" in ev
    assert ev.endswith("\n\n[화면 증거]\n(없음: 이 턴에서 화면 도구 호출이 없다)")


def test_screen_evidence_with_baseline_before_first_edit():
    log = ToolLog()
    _run(log, "render_page", {"target": "site/index.html", "name": "before"}, RENDER_OK, "r1")
    _run(log, "edit_file", {"path": "site/index.html"}, "ok", "r2")
    _run(log, "render_page", {"target": "site/index.html", "name": "after"}, RENDER_OK.replace("before", "after"), "r3")
    _run(log, "screen_diff", {"before": BEFORE, "after": AFTER}, DIFF_CHANGED, "r4")
    _run(log, "edit_file", {"path": "site/index.html"}, "ok", "r5")
    _run(log, "render_page", {"target": "site/index.html", "name": "after"}, RENDER_OK.replace("before", "after"), "r6")
    _run(log, "screen_diff", {"before": BEFORE, "after": AFTER}, DIFF_EQUAL, "r7")
    lines = _screen_block(log)
    assert lines[0] == "[화면 증거]"
    assert lines[1] == "render_page 성공 3/실패 0 · parse_screen 성공 0/실패 0 · screen_diff 성공 2/실패 0"
    assert lines[2] == "기준 렌더: 첫 편집(#2) 이전 render_page 호출 있음(#1)"
    assert lines[3] == ("#1 render_page(site/index.html → before) 성공: 렌더 완료 · .koda/renders/before.png"
                        " · 1280x800 · 콘솔 에러 0건 · 외부 차단 0건")
    assert lines[5] == f"#4 screen_diff({BEFORE} vs {AFTER}) 성공: {DIFF_CHANGED.splitlines()[0]}"
    assert [ln.split()[0] for ln in lines if ln.startswith("#")] == ["#1", "#3", "#4", "#6", "#7"]
    assert f"첫 screen_diff(#4): {DIFF_CHANGED.splitlines()[0]}" in lines
    assert "\n".join(lines).endswith(f"마지막 screen_diff(#7) 전문:\n{DIFF_EQUAL}")


def test_screen_evidence_marks_missing_baseline():
    log = ToolLog()
    _run(log, "edit_file", {"path": "site/index.html"}, "ok", "r1")
    _run(log, "render_page", {"target": "site/index.html", "name": "before"}, RENDER_OK, "r2")
    _run(log, "render_page", {"target": "site/index.html", "name": "after"}, RENDER_OK.replace("before", "after"), "r3")
    _run(log, "screen_diff", {"before": BEFORE, "after": AFTER}, DIFF_EQUAL, "r4")
    assert "기준 렌더: 첫 편집(#1) 이전 render_page 호출 없음(비교 기준으로 쓴 렌더가 첫 편집 뒤의 #2)" in _screen_block(log)

    failed = ToolLog()   # a render that returned 오류: is not a baseline either
    _run(failed, "render_page", {"target": "site/index.html", "name": "before"}, "오류: playwright 가 설치되어 있지 않다", "r1")
    _run(failed, "edit_file", {"path": "site/index.html"}, "ok", "r2")
    assert "기준 렌더: 첫 편집(#2) 이전 render_page 호출 없음" in _screen_block(failed)


def test_baseline_is_the_render_the_last_diff_compared():
    # A pre-edit render that is overwritten under the same name after the first edit is no baseline: the diff
    # compared the later files.
    log = ToolLog()
    _run(log, "render_page", {"target": "site/index.html", "name": "before"}, RENDER_OK, "r1")
    _run(log, "edit_file", {"path": "site/index.html"}, "ok", "r2")
    _run(log, "render_page", {"target": "site/index.html", "name": "before"}, RENDER_OK, "r3")
    _run(log, "edit_file", {"path": "site/index.html"}, "ok", "r4")
    _run(log, "render_page", {"target": "site/index.html", "name": "after"}, RENDER_OK.replace("before", "after"), "r5")
    _run(log, "screen_diff", {"before": BEFORE, "after": AFTER}, DIFF_EQUAL, "r6")
    assert "기준 렌더: 첫 편집(#2) 이전 render_page 호출 없음(비교 기준으로 쓴 렌더가 첫 편집 뒤의 #3)" in _screen_block(log)

    # A pre-edit render of another page does not stand in for the one the diff used.
    other = ToolLog()
    _run(other, "render_page", {"target": "site/other.html", "name": "o"}, RENDER_OK.replace("before", "o"), "r1")
    _run(other, "edit_file", {"path": "site/index.html"}, "ok", "r2")
    _run(other, "render_page", {"target": "site/index.html", "name": "before"}, RENDER_OK, "r3")
    _run(other, "render_page", {"target": "site/index.html", "name": "after"}, RENDER_OK.replace("before", "after"), "r4")
    _run(other, "screen_diff", {"before": BEFORE, "after": AFTER}, DIFF_EQUAL, "r5")
    assert "기준 렌더: 첫 편집(#2) 이전 render_page 호출 없음(비교 기준으로 쓴 렌더가 첫 편집 뒤의 #3)" in _screen_block(other)

    # The dom.json spelling of the before input and a render after the diff do not change the answer.
    later = ToolLog()
    _run(later, "render_page", {"target": "site/index.html", "name": "before"}, RENDER_OK, "r1")
    _run(later, "edit_file", {"path": "site/index.html"}, "ok", "r2")
    _run(later, "render_page", {"target": "site/index.html", "name": "after"}, RENDER_OK.replace("before", "after"), "r3")
    _run(later, "screen_diff", {"before": ".koda/renders/before.dom.json", "after": AFTER}, DIFF_EQUAL, "r4")
    _run(later, "render_page", {"target": "site/index.html", "name": "before"}, RENDER_OK, "r5")
    assert "기준 렌더: 첫 편집(#2) 이전 render_page 호출 있음(#1)" in _screen_block(later)

    # No render by that name at all: plain 없음. A diff of a workspace image falls back to any pre-edit render.
    none = ToolLog()
    _run(none, "render_page", {"target": "site/index.html", "name": "x"}, RENDER_OK.replace("before", "x"), "r1")
    _run(none, "edit_file", {"path": "site/index.html"}, "ok", "r2")
    _run(none, "screen_diff", {"before": BEFORE, "after": AFTER}, DIFF_EQUAL, "r3")
    assert "기준 렌더: 첫 편집(#2) 이전 render_page 호출 없음" in _screen_block(none)
    design = ToolLog()
    _run(design, "render_page", {"target": "site/index.html", "name": "x"}, RENDER_OK.replace("before", "x"), "r1")
    _run(design, "edit_file", {"path": "site/index.html"}, "ok", "r2")
    _run(design, "screen_diff", {"before": "design.png", "after": ".koda/renders/x.png"}, DIFF_EQUAL, "r3")
    assert "기준 렌더: 첫 편집(#2) 이전 render_page 호출 있음(#1)" in _screen_block(design)


def test_screen_evidence_without_edit_or_diff():
    log = ToolLog()
    _run(log, "parse_screen", {"path": "bug.png"},
         "요소 3개 · 이슈 1개 · 크기 1280x800 · 저장 .koda/renders/src__bug.parse.json\n장면: 페이지\n이슈: overlap e1·e2 (62%)", "r1")
    lines = _screen_block(log)
    assert lines[2] == "기준 렌더: 편집 없음"
    assert lines[3] == "#1 parse_screen(bug.png) 성공: 요소 3개 · 이슈 1개 · 크기 1280x800 · 저장 .koda/renders/src__bug.parse.json"
    assert not any("screen_diff(#" in ln for ln in lines)     # no diff lines without a diff call


def test_last_screen_diff_output_is_cut_to_limit():
    log = ToolLog()
    long = DIFF_CHANGED + "\n" + "\n".join(f'누락: d{i} TEXT "항목 {i}" [0,0,10,10]' for i in range(100))
    _run(log, "screen_diff", {"before": "a.png", "after": "b.png"}, long, "r1")
    tail = log.evidence().split("전문:\n")[1]
    assert tail.endswith("…") and len(tail) == 601
    summary = log.calls[0][3]                                     # the main block keeps its own 3-line, 160-char cap
    assert summary.startswith("차이 2건") and "항목 0" not in summary and len(summary) <= 161


def test_summary_counts_tools():
    log = ToolLog()
    _run(log, "render_page", {"target": "a.html"}, RENDER_OK, "r1")
    _run(log, "render_page", {"target": "a.html"}, RENDER_OK, "r2")
    _run(log, "screen_diff", {"before": "a", "after": "b"}, DIFF_EQUAL, "r3")
    assert log.summary() == "render_page×2 · screen_diff"
