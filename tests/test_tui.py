from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("textual")

from textual.widgets.text_area import Selection

import koda.tui.runner as runner_mod
from koda.memory import Memory
from koda.session import Session
from koda.tui.app import KodaTui
from koda.tui.model import PhaseEvent, ReasoningEnd, TokenChunk, ToolEnd, ToolStart
from koda.tui.tool_handler import ToolActivityHandler, tool_target, tool_title
from koda.tui.widgets import (
    ApprovalModal,
    DiffBlock,
    ExploreStep,
    ProcessGroup,
    PromptArea,
    ReasoningStep,
    StepLine,
    _args_preview,
    first_line,
)
from koda.workspace import Workspace


def _result(final="완료", **kw):
    base = {"final": final, "route": "read", "verdict": None, "verify_detail": "",
            "empty_final": not final.strip(), "empty_reason": "", "empty_retries": 0,
            "retries": 0, "trace_url": ""}
    base.update(kw)
    return base


def _make_app(tmp_path, run_task_fn, auto_yes=False, approval_timeout=600.0,
              session=None, mcp_tools=None):
    session = session or Session(tmp_path)
    app = KodaTui(Workspace(tmp_path), session, Memory(tmp_path), None, auto_yes=auto_yes,
                  run_task_fn=run_task_fn, approval_timeout=approval_timeout)
    if mcp_tools is not None:
        app.runner.mcp_tools = mcp_tools
    return app


async def _wait(pilot, cond, timeout=10.0):
    """조건 폴링. pilot.pause 대신 asyncio.sleep 사용 — app.exit() 후에도 멈춘 메시지 펌프를 기다리며 영구 블록하지 않는다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return cond()


async def _submit(pilot, text):
    inp = pilot.app.query_one("#prompt")
    await _wait(pilot, lambda: not inp.disabled)
    inp.focus()
    await pilot.pause()
    inp.text = text
    await pilot.press("enter")


def _static_text(widget) -> str:
    r = widget.render()
    return getattr(r, "plain", None) or str(r)


def _turn_done(app):
    return lambda: not app._active.busy and bool(app.conv.query(".turn-footer"))


def _footers(app) -> list[str]:
    return [_static_text(w) for w in app.conv.query(".turn-footer")]


@pytest.fixture(autouse=True)
def _no_memory_llm(monkeypatch):
    # 턴마다 도는 메모리 판정은 LLM 호출이라 기본으로 막는다. 필요한 테스트만 다시 패치한다.
    async def nothing(memory, task, answer, settings=None):
        return []
    monkeypatch.setattr(runner_mod, "remember_from_turn", nothing)


# --- AC 4: 앱이 뜨고 입력을 받을 수 있다 (headless) ---

def test_boot_widgets_exist(tmp_path):
    async def go():
        app = _make_app(tmp_path, run_task_fn=None)
        async with app.run_test():
            assert app.query_one("#prompt") is not None
            assert app.conv is not None
            assert app.query_one("#statusline") is not None
            assert "컨텍스트" in _static_text(app.query_one("#statusline"))
    asyncio.run(go())


# --- AC 5: token stream 이 conversation 영역에 누적된다 ---

def test_token_stream_accumulates(tmp_path):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_phase("read")
        on_token("# 제목\n\n")
        on_token("본문 문단")
        return _result(final="# 제목\n\n본문 문단")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test() as pilot:
            await _submit(pilot, "설명해줘")
            assert await _wait(pilot, _turn_done(app))
            assert app._active.draft == "# 제목\n\n본문 문단"
            # 대화 영역에 user 턴 + Markdown 위젯 + footer 가 순서대로 쌓인다
            conv = app.conv
            texts = [_static_text(w) for w in conv.query(".user-turn")]
            assert any("설명해줘" in t for t in texts)
            footers = [_static_text(w) for w in conv.query(".turn-footer")]
            assert any("완료" in t for t in footers)
            assert not any("route:" in t or "trace:" in t for t in footers)   # 진단용 값은 답변 아래에 남기지 않는다
    asyncio.run(go())


# --- AC 6: 이벤트가 올바른 영역에 표시된다 (+ retry 리셋, think/heartbeat 는 status 전용) ---

def test_events_routed(tmp_path):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_phase("edit")
        on_token("버릴 부분")                     # retry 전 부분 스트림 → 폐기 대상
        on_phase("think:5")
        on_phase("heartbeat:30s")
        on_phase("tool:write_file")
        on_phase("retry:length_truncation")
        on_token("다시 쓴 본문")
        return _result(final="다시 쓴 본문", route="edit", verdict="pass",
                       verify_detail="테스트 근거", trace_url="http://lf/t/1")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test() as pilot:
            await _submit(pilot, "수정해줘")
            assert await _wait(pilot, _turn_done(app))
            # 툴 이벤트는 활동바 롤업 tally 로 누적된다 (workspace_ 접두 제거)
            assert "write_file" in app._active.tools
            assert app._active.draft == "다시 쓴 본문"             # retry 가 1차 부분 스트림 폐기
            # 검증 결과·판정 근거·trace 는 우측 패널 폐지 후 턴 footer 에 인라인된다
            footer = "\n".join(_footers(app))
            assert "verdict: pass" in footer and "테스트 근거" in footer
            assert "trace:" not in footer and "route:" not in footer
    asyncio.run(go())


# --- AC 7: approval modal — approve=True / deny·close=False / timeout=False ---

def _approval_fake(record):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        ok = approver("run_command", {"argv": ["pytest", "-q"]})
        record.append(ok)
        return _result(final=f"approved={ok}")
    return fake


def test_approval_approve(tmp_path):
    record = []

    async def go():
        app = _make_app(tmp_path, _approval_fake(record))
        async with app.run_test() as pilot:
            await _submit(pilot, "테스트 실행해줘")
            assert await _wait(pilot, lambda: isinstance(app.screen, ApprovalModal))
            assert "run_command" in _static_text(app.screen.query_one("#approval_title"))
            assert "$ pytest -q" in _static_text(app.screen.query_one("#approval_args"))
            # 승인 대기는 활동바 라벨로 노출된다(롤업에 가려지지 않게)
            assert await _wait(pilot, lambda: "승인 대기" in _static_text(app.query_one("#activity")))
            await pilot.click("#approve")
            assert await _wait(pilot, _turn_done(app))
            assert record == [True]
    asyncio.run(go())


def test_approval_deny_by_escape(tmp_path):
    record = []

    async def go():
        app = _make_app(tmp_path, _approval_fake(record))
        async with app.run_test() as pilot:
            await _submit(pilot, "테스트 실행해줘")
            assert await _wait(pilot, lambda: isinstance(app.screen, ApprovalModal))
            await pilot.press("escape")   # modal 닫기 = deny
            assert await _wait(pilot, _turn_done(app))
            assert record == [False]
    asyncio.run(go())


def test_approval_timeout_denies(tmp_path):
    record = []

    async def go():
        app = _make_app(tmp_path, _approval_fake(record), approval_timeout=0.2)
        async with app.run_test() as pilot:
            await _submit(pilot, "테스트 실행해줘")
            assert await _wait(pilot, _turn_done(app))   # 무응답 → deny 로 턴이 끝난다
            assert record == [False]
            # 고아 modal 이 남지 않는다 — _end_turn 이 정리한다
            assert await _wait(pilot, lambda: not isinstance(app.screen, ApprovalModal))
            assert not app.query_one("#prompt").disabled
    asyncio.run(go())


# --- AC 8: --yolo 는 auto 승인(.auto=True) — confirm 등급 차단은 middleware 계약이 담당 ---

def test_auto_yes_flag(tmp_path):
    app = _make_app(tmp_path, None, auto_yes=True)
    assert getattr(app.runner.approver, "auto", False) is True
    app2 = _make_app(tmp_path, None, auto_yes=False)
    assert getattr(app2.runner.approver, "auto", False) is False  # 대화형 승인으로 취급


# --- AC 9: empty final 은 세션 히스토리에 저장하지 않는다 ---

def test_empty_final_not_saved(tmp_path):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_token("부분만 나오고")
        return _result(final="", empty_final=True, empty_reason="length_truncation")

    async def go():
        session = Session(tmp_path)
        app = _make_app(tmp_path, fake, session=session)
        async with app.run_test() as pilot:
            await _submit(pilot, "질문")
            assert await _wait(pilot, _turn_done(app))
            assert session.history == []
            assert not session.path.exists()
            footer = "\n".join(_footers(app))
            assert "빈 응답" in footer and "length_truncation" in footer
    asyncio.run(go())


# --- AC 10 + Phase 3 parity: MCP 세션당 1회 · run_id=session.id · 히스토리 누적 · 저장 ---

def test_turn_parity_and_session_save(tmp_path):
    calls = []

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        calls.append({"task": task, "history": list(kw["history"]),
                      "mcp_tools": kw["mcp_tools"], "mcps_dir": kw["mcps_dir"],
                      "run_id": kw["run_id"]})
        on_token(f"답변 {len(calls)}")
        return _result(final=f"답변 {len(calls)}", route="read")

    async def go():
        session = Session(tmp_path)
        mcp_tools = [object()]  # 사전 로드된 도구 목록 (정체성 비교용)
        app = _make_app(tmp_path, fake, session=session, mcp_tools=mcp_tools)
        async with app.run_test() as pilot:
            await _submit(pilot, "첫 질문")
            assert await _wait(pilot, _turn_done(app))
            await _submit(pilot, "둘째 질문")
            assert await _wait(pilot, lambda: not app._active.busy and len(app.conv.query(".turn-footer")) == 2)

        assert [c["task"] for c in calls] == ["첫 질문", "둘째 질문"]
        assert calls[0]["mcp_tools"] is mcp_tools and calls[1]["mcp_tools"] is mcp_tools
        assert calls[0]["mcps_dir"] is None and calls[1]["mcps_dir"] is None  # 재로드 금지
        assert calls[0]["run_id"] == session.id == calls[1]["run_id"]
        assert calls[0]["history"] == []
        assert [m["content"] for m in calls[1]["history"]] == ["첫 질문", "답변 1"]
        assert session.path.exists() and len(session.turns) == 2
        loaded = Session.load(tmp_path, session.id)
        assert [m["content"] for m in loaded.history[-2:]] == ["둘째 질문", "답변 2"]
    asyncio.run(go())


# --- 턴 실패: 예외 → 안내 후 입력 재활성화 (chat '턴 실패' 동등) ---

def test_turn_failed_recovers(tmp_path):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        raise RuntimeError("boom")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test() as pilot:
            await _submit(pilot, "질문")
            assert await _wait(pilot, _turn_done(app))
            footer = "\n".join(_footers(app))
            assert "턴 실패: RuntimeError: boom" in footer
            assert not app.query_one("#prompt").disabled
    asyncio.run(go())


# --- 턴 완료마다 메모리 판정 → 저장 알림. /exit 는 세션 저장만 ---

def test_turn_stores_judged_memory_and_exit_saves_session(tmp_path, monkeypatch):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        return _result(final="답변")

    async def fake_remember(memory, task, answer, settings=None):
        memory.remember("함수명은 snake_case", "procedural")
        return [("procedural", "함수명은 snake_case")]

    monkeypatch.setattr(runner_mod, "remember_from_turn", fake_remember)

    async def go():
        session = Session(tmp_path)
        app = _make_app(tmp_path, fake, session=session)
        async with app.run_test() as pilot:
            await _submit(pilot, "앞으로 함수명은 snake_case 로 써")
            assert await _wait(pilot, _turn_done(app))
            assert Memory(tmp_path).items("procedural") == ["함수명은 snake_case"]   # 턴 직후 저장
            await _submit(pilot, "/remember episodic 오늘 calc.py 를 고쳤다")
            await pilot.pause()
            assert Memory(tmp_path).items("episodic") == ["오늘 calc.py 를 고쳤다"]   # 유형 지정 수동 저장
            await _submit(pilot, "/exit")
            assert await _wait(pilot, lambda: app.return_value is not None)
        assert app.return_value is True and session.path.exists()
    asyncio.run(go())


def test_empty_answer_with_tool_calls_is_recorded(tmp_path):
    """실제로 도구를 돌린 턴은 최종 응답이 비어도 히스토리에 남는다. 안 그러면 그 작업이 통째로 사라져 다음 턴이 맥락을 잃는다."""
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        return _result(final="", empty_final=True, empty_reason="no_content",
                       tool_calls=5, tool_summary="run_command×5 · edit_file")

    async def go():
        session = Session(tmp_path)
        app = _make_app(tmp_path, fake, session=session)
        async with app.run_test() as pilot:
            await _submit(pilot, "다른 포트로 서버 띄워줘")
            assert await _wait(pilot, _turn_done(app))
            assert len(session.turns) == 1                                  # 실행한 턴은 기록된다
            assert session.history[0]["content"] == "다른 포트로 서버 띄워줘"
            assert "run_command×5 · edit_file" in session.history[1]["content"]
            assert session.path.exists()
    asyncio.run(go())


def test_empty_answer_without_tool_calls_is_not_recorded(tmp_path):
    """도구도 안 돌고 응답도 없으면 남길 것이 없다(기존 오염 방지 유지)."""
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        return _result(final="", empty_final=True, empty_reason="length_truncation", tool_calls=0)

    async def go():
        session = Session(tmp_path)
        app = _make_app(tmp_path, fake, session=session)
        async with app.run_test() as pilot:
            await _submit(pilot, "질문")
            assert await _wait(pilot, _turn_done(app))
            assert session.history == [] and not session.path.exists()
    asyncio.run(go())


def test_failed_verdict_is_marked_in_history(tmp_path):
    """검증 실패 턴의 답변은 히스토리에 실패 표시와 함께 남아, 다음 턴이 사실로 오해하지 않는다."""
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_token("완료했습니다. kill 로 전부 종료했습니다.")
        return _result(final="완료했습니다. kill 로 전부 종료했습니다.", route="edit", verdict="fail",
                       verify_detail="실행된 도구가 없다", tool_calls=0)

    async def go():
        session = Session(tmp_path)
        app = _make_app(tmp_path, fake, session=session)
        async with app.run_test() as pilot:
            await _submit(pilot, "프론트 백엔드 다 죽여줘")
            assert await _wait(pilot, _turn_done(app))
            saved = session.history[1]["content"]
            assert saved.startswith("완료했습니다.") and "koda 검증: 실패" in saved
            footer = "\n".join(_footers(app))
            assert "verdict: fail" in footer
    asyncio.run(go())


# --- 승인 args 미리보기 — 길이 제한 + run_command 명령 표시 + secret redaction ---

def test_args_preview():
    assert _args_preview("run_command", {"argv": ["rm", "-rf", "x"]}) == "$ rm -rf x"
    long = _args_preview("write_file", {"content": "x" * 2000})
    assert len(long) <= 500 + len(" …(생략)") and long.endswith(" …(생략)")
    # secret 성 키는 값이 노출되지 않는다(redaction)
    masked = _args_preview("some_mcp_tool", {"api_key": "sk-live-123", "path": "a.py"})
    assert "sk-live-123" not in masked and "[REDACTED]" in masked and "a.py" in masked


# --- 텍스트 선택 활성 + 마크다운 표 클릭 안전 (과거 textual 8.2.8 크래시 회귀 감시) ---

def test_select_enabled_and_table_click_safe(tmp_path):
    """선택을 켠 상태에서 표 셀 클릭이 앱을 죽이지 않아야 한다. 과거 이 경로가 고아 위젯 hit-test 로 죽어 선택을 껐던 적이 있다."""
    assert KodaTui.ALLOW_SELECT is True

    # 뷰포트(100x40) 안에 표 전체가 보이는 크기 — 스크롤 밖 셀은 pilot.click 이 OutOfBounds
    table_md = "| 항목 | 값 |\n| --- | --- |\n" + "\n".join(f"| r{i} | {i} |" for i in range(4))

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_token(table_md)
        return _result(final=table_md)

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test(size=(100, 40)) as pilot:
            await _submit(pilot, "표로 정리해줘")
            assert await _wait(pilot, _turn_done(app))
            cells = app.query(".cell")
            assert cells, "마크다운 표 셀이 렌더링되어야 한다"
            await pilot.click(".cell")   # 선택 활성 상태에서 표 클릭이 안전해야 한다
            await pilot.pause()
            assert not app.query_one("#prompt").disabled  # 앱 생존 확인
    asyncio.run(go())


# --- 슬래시 커맨드 디스패치 ---

def test_slash_commands_do_not_trigger_turn(tmp_path):
    """/help·알 수 없는 명령은 run_task(턴)를 실행하지 않고, 일반 텍스트만 턴을 돈다."""
    calls = {"n": 0}

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        calls["n"] += 1
        on_token("답")
        return _result("완료")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test() as pilot:
            await _submit(pilot, "/help")
            await pilot.pause()
            await _submit(pilot, "/nope")          # 알 수 없는 명령
            await pilot.pause()
            assert calls["n"] == 0                  # 슬래시 커맨드는 턴을 실행하지 않는다
            await _submit(pilot, "안녕")             # 일반 텍스트 → 턴
            assert await _wait(pilot, _turn_done(app))
            assert calls["n"] == 1

    asyncio.run(go())


def test_slash_clear_starts_new_thread(tmp_path):
    """/clear 는 새 스레드로 진입한다: 화면·세션 히스토리 초기화 + 새 세션 id. 이전 세션은 --resume 로 복구 가능."""
    from textual.containers import VerticalScroll

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_phase("read")
        on_token("답변")
        return _result("완료")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test() as pilot:
            await _submit(pilot, "안녕")
            assert await _wait(pilot, _turn_done(app))
            old_id = app.runner.session.id
            assert app.runner.session.history                    # 턴 후 히스토리 존재
            conv = app.conv
            assert len(conv.children) > 0
            await _submit(pilot, "/clear")
            assert await _wait(pilot, lambda: app.conv is not conv)   # 새 세션은 자기 대화 영역을 갖는다
            assert len(app.conv.children) == 0
            assert app.runner.session.id != old_id               # 새 세션(스레드)
            assert app.runner.session.history == []              # 대화 맥락 초기화
            assert Session.load(tmp_path, old_id).history         # 이전 세션은 저장됨(복구 가능)

    asyncio.run(go())


# --- 과정 가시화 (제목 템플릿 · 콜백 핸들러 · 스텝 줄 렌더) ---

def test_tool_title_templates():
    # 별도 LLM 호출 없이 도구+완성 args → 표시용 제목. 빈 args 는 도구 이름으로 폴백.
    assert tool_title("run_command", {"command": "pytest -q"}) == "$ pytest -q"
    assert tool_title("run_readonly_command", {"argv": ["ls", "-al"]}) == "$ ls -al"
    assert tool_title("read_file", {"path": "a.py"}) == "읽기 · a.py"
    assert tool_title("search_text", {"query": "foo"}) == "검색 · foo"
    assert tool_title("edit_file", {"path": "b.py"}) == "수정 · b.py"
    assert tool_title("explore", {}) == "탐색 위임"
    assert tool_title("read_file", {}) == "파일 읽기"      # 빈 args 폴백
    assert tool_title("some_mcp_tool", {}) == "some_mcp_tool"       # MCP/기타


def test_tool_target_classifies_explore_tools():
    # 탐색성 도구만 (종류, 대상) 을 주고, 편집·실행·일반 MCP 는 None 이라 병합되지 않는다.
    assert tool_target("read_file", {"path": "a.py"}) == ("읽음", "a.py")
    assert tool_target("search_text", {"query": "add"}) == ("검색", '"add"')
    assert tool_target("run_readonly_command", {"command": "ls -la"}) == ("조사", "ls -la")
    assert tool_target("query-docs", {}) == ("문서", "query-docs")
    assert tool_target("web_search_exa", {"query": "koda"}) == ("웹", '"koda"')
    assert tool_target("edit_file", {"path": "a.py"}) is None
    assert tool_target("run_command", {"command": "pytest"}) is None
    assert tool_target("some_mcp_tool", {}) is None
    posted: list = []
    ToolActivityHandler(posted.append).on_tool_start({"name": "read_file"}, "", run_id="r", inputs={"path": "x"})
    assert posted[0].target == ("읽음", "x")


def test_tool_step_mounts_in_group_and_finishes(tmp_path):
    # ToolStart → ProcessGroup 안에 StepLine(진행 -run) mount, ToolEnd → 같은 줄 finish(-ok + 요약 ⎿ + 출력).
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test() as pilot:
            app.post_message(ToolStart("r1", "run_command", "$ pytest -q"))
            assert await _wait(pilot, lambda: bool(app.query(StepLine)))
            step = app.query_one(StepLine)
            assert step.parent.parent is app.query_one(ProcessGroup) and not app.query_one(ProcessGroup).collapsed
            assert "⏺ $ pytest -q" in step.title.plain and step.has_class("-run") and step.collapsed
            app.post_message(ToolEnd("r1", "exit_code=0\nstdout:\n3 passed\nstderr:\n", ok=True, name="run_command"))
            assert await _wait(pilot, lambda: app.query_one(StepLine).has_class("-ok"))
            assert "⎿ 3 passed" in step.title.plain and "3 passed" in _static_text(step._body)
            assert first_line("exit_code=0\nstdout:\n..      [100%]\n") == ".. [100%]"   # 공백 정규화
            assert first_line("exit_code=1\nstdout:\n\nstderr:\nboom") == "exit_code=1 · boom"
            assert app._active.tool_steps == {}                  # 매칭 후 dict 에서 pop
    asyncio.run(go())


def test_tool_end_unknown_run_id_ignored(tmp_path):
    # 매칭되는 start 가 없는 ToolEnd 는 예외 없이 무시된다(.pop(id, None) 방어).
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test() as pilot:
            app.post_message(ToolEnd("no-such-id", "출력", ok=True))
            await pilot.pause()
            assert not app.query(StepLine) and not app.query(ProcessGroup)   # 유령 위젯·크래시 없음
            assert not app._close_requested               # 앱 생존
    asyncio.run(go())


def test_orphan_steps_finished_on_turn_end(tmp_path):
    # 턴이 툴 도중 죽어 ToolEnd 가 안 오면(_end_turn) 미완 스텝(단독·탐색 병합 모두)을 '중단됨' 실패로 마감한다.
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test() as pilot:
            app.post_message(ToolStart("r1", "run_command", "$ pytest"))
            app.post_message(ToolStart("r2", "read_file", "읽기 · a.py", ("읽음", "a.py")))
            assert await _wait(pilot, lambda: len(app.query(StepLine)) == 2)
            assert all(st.has_class("-run") for st in app.query(StepLine))
            app._end_turn()                                     # 턴 종료 공통 경로
            await pilot.pause()
            for st in app.query(StepLine):
                assert st.has_class("-fail") and "중단됨" in _static_text(st._body)
            assert app._active.tool_steps == {}
    asyncio.run(go())


def test_tool_output_secret_redaction():
    # read_file 로 .env 를 열거나 셸 출력에 시크릿이 섞여도 값이 스크롤백에 평문으로 안 남는다.
    posted: list = []
    ToolActivityHandler(posted.append).on_tool_end(
        "API_KEY=sk-secret123\nnormal line\nport = 8080", run_id="r")
    out = posted[0].output
    assert "sk-secret123" not in out and "[REDACTED]" in out
    assert "normal line" in out and "8080" in out   # 일반 줄·비밀 아닌 값은 보존


# --- 세그먼트 스트림: 산문↔툴 시간순 인터리브 ---

def _conv_types(app):
    return [type(c).__name__ for c in app.conv.children]


def test_prose_tool_interleave_order(tmp_path):
    # 산문① → 툴 → 산문② 순으로 이벤트가 오면 대화가 [Markdown, ProcessGroup, Markdown] 으로 쌓인다.
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test() as pilot:
            app.post_message(TokenChunk("산문①"))
            assert await _wait(pilot, lambda: bool(app.conv.query("Markdown")))
            app.post_message(ToolStart("r1", "read_file", "읽기 · a.py", ("읽음", "a.py")))
            assert await _wait(pilot, lambda: bool(app.query(StepLine)))
            app.post_message(ToolEnd("r1", "출력", ok=True))
            app.post_message(TokenChunk("산문②"))
            assert await _wait(pilot, lambda: len(app.conv.query("Markdown")) == 2)
            assert _conv_types(app) == ["Markdown", "ProcessGroup", "Markdown"]
            assert app._active.draft == "산문①산문②"
    asyncio.run(go())


def test_retry_discards_segments_and_groups(tmp_path):
    # retry 시 이번 시도의 산문 세그먼트·과정 그룹·_tool_steps 매핑을 전부 폐기한다.
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test() as pilot:
            app.post_message(TokenChunk("버릴 산문"))
            app.post_message(ToolStart("r1", "read_file", "읽기", ("읽음", "a.py")))
            assert await _wait(pilot, lambda: bool(app.query(StepLine)))
            assert app.conv.query("Markdown") and app._active.tool_steps
            app.post_message(PhaseEvent("retry:length_truncation"))
            assert await _wait(pilot, lambda: not app.query(ProcessGroup))
            assert not app.conv.query("Markdown")   # 세그먼트 폐기(unmount)
            assert app._active.tool_steps == {} and app._active.cur_group is None and app._active.cur_explore is None
            assert app._active.draft == ""
    asyncio.run(go())


# --- 질의 입력창: 다중행(Alt+Enter 줄바꿈)·Enter 제출·maximize 차단 (#21·#22) ---

def test_prompt_newline_keys_insert_no_submit(tmp_path):
    # Alt+Enter·Shift+Enter·Ctrl+J 는 제출이 아니라 줄바꿈 — 턴이 시작되지 않고 개행만 들어간다.
    async def go():
        app = _make_app(tmp_path, run_task_fn=None)
        async with app.run_test() as pilot:
            inp = app.query_one("#prompt", PromptArea)
            for key in ("alt+enter", "shift+enter", "ctrl+j"):
                inp.focus()
                inp.text = "x"
                await pilot.pause()
                await pilot.press(key)
                await pilot.pause()
                assert "\n" in inp.text, key       # 줄바꿈 삽입
                assert not app._active.busy               # 제출 안 됨(턴 미시작)
            assert not app.conv.query(".turn-footer")
    asyncio.run(go())


def test_prompt_enter_submits_multiline(tmp_path):
    # Enter 는 개행 포함 다중행 원문을 그대로 제출한다.
    seen = {}

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        seen["task"] = task
        on_token("ok")
        return _result(final="ok")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test() as pilot:
            inp = app.query_one("#prompt", PromptArea)
            inp.focus()
            await pilot.pause()
            inp.text = "첫 줄\n둘째 줄"
            await pilot.press("enter")
            assert await _wait(pilot, _turn_done(app))
            assert seen["task"] == "첫 줄\n둘째 줄"
            assert inp.text == ""            # 제출 후 입력창 비움
    asyncio.run(go())


def test_maximize_command_removed_even_with_tool_block(tmp_path):
    # #21: 어떤 위젯이 포커스돼도 maximize 명령이 없어야 한다. 특히 스텝 줄의 제목(CollapsibleTitle)은
    # focus 가능·maximize 가능이라, 이 위젯이 포커스된 상태에서도 명령이 제거됨을 확인한다(위젯별
    # ALLOW_MAXIMIZE whack-a-mole 이 아니라 앱 레벨 차단임을 가드).
    async def go():
        app = _make_app(tmp_path, run_task_fn=None)
        async with app.run_test() as pilot:
            app.post_message(ToolStart("r1", "read_file", "읽기 · a.py", ("읽음", "a.py")))
            assert await _wait(pilot, lambda: bool(app.query(StepLine)))
            title = app.query_one(StepLine).query_one("CollapsibleTitle")
            title.focus()
            await pilot.pause()
            assert title.allow_maximize is True   # 전제: 이 위젯은 원래 maximize 가능
            maximize = app.screen.action_maximize
            ours = list(app.get_system_commands(app.screen))
            assert all(c.callback != maximize for c in ours)                  # 우리 override: 제거됨
            base = list(super(type(app), app).get_system_commands(app.screen))
            assert any(c.callback == maximize for c in base)                  # 대조: 기본은 제공함
    asyncio.run(go())


def test_prompt_autogrows_capped(tmp_path):
    # 내용에 따라 높이 자동 성장하되 max-height 로 상한(초과분 내부 스크롤).
    async def go():
        app = _make_app(tmp_path, run_task_fn=None)
        async with app.run_test(size=(80, 30)) as pilot:
            inp = app.query_one("#prompt", PromptArea)
            inp.text = "한 줄"
            await pilot.pause()
            h1 = inp.outer_size.height
            inp.text = "\n".join(f"줄 {i}" for i in range(20))
            await pilot.pause()
            h20 = inp.outer_size.height
            assert h20 > h1        # 성장
            assert h20 <= 10       # max-height 상한
    asyncio.run(go())


def test_ctrl_c_once_cancels_turn_twice_exits(tmp_path):
    # Ctrl+C 1회: 진행 중인 턴을 끊고(부분 답변 미저장) 앱은 살아 있다. 2초 안에 2회: 세션 저장 후 종료.
    cancelled = []

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        if task == "빠른 질문":
            on_token("답변")
            return _result(final="답변")
        on_token("긴 답변 시작")
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(task)
            raise
        return _result(final="끝")

    async def go():
        session = Session(tmp_path)
        app = _make_app(tmp_path, fake, session=session)
        async with app.run_test(size=(100, 30)) as pilot:
            await _submit(pilot, "빠른 질문")
            assert await _wait(pilot, _turn_done(app))
            await _submit(pilot, "긴 질문")
            assert await _wait(pilot, lambda: app._active.busy and app.runner._turn_task is not None and "긴 답변" in app._active.draft)
            inp = app.query_one("#prompt", PromptArea)
            inp.text = "hi"
            inp.selection = Selection((0, 0), (0, 2))          # TextArea 내부 선택은 화면 선택이 아니라 copy 로 빠지지 않는다
            await pilot.press("ctrl+c")
            assert await _wait(pilot, lambda: not app._active.busy and cancelled == ["긴 질문"])
            footers = _footers(app)
            assert any("중단됨" in f for f in footers) and not app._exit
            assert len(session.turns) == 1                      # 중단된 턴은 히스토리에 없다
            await asyncio.sleep(2.2)                            # 연타 창이 지난 뒤
            await pilot.press("ctrl+c")                         # 대기 중 1회: 예고만
            await pilot.pause()
            assert not app._exit
            await pilot.press("ctrl+c")                         # 2초 안에 2회: 종료
            assert await _wait(pilot, lambda: app.return_value is not None)
        assert app.return_value is True and session.path.exists()
    asyncio.run(go())


# --- 편집 도구 diff 색상 렌더 + name 전파 + redaction (TUI_DIFF_RENDER) ---

def _body_content(block):
    return block._body._Static__content   # Static 이 update() 로 받은 원본 renderable


def test_tool_diff_render_branch_and_name(tmp_path):
    # 편집 도구 성공 diff → Group(요약 Text + Syntax), 비편집/실패 → 평문. name 이 렌더 분기를 가른다.
    async def go():
        app = _make_app(tmp_path, run_task_fn=None)
        async with app.run_test() as pilot:
            app.post_message(ToolStart("r1", "edit_file", "수정 · a.py"))
            assert await _wait(pilot, lambda: bool(app.query(StepLine)))
            b = app.query(StepLine)[0]
            assert b._tool_name == "edit_file"                 # ToolStart.name → StepLine 전파
            app.post_message(ToolEnd("r1", "수정함: a.py (+1/-1)\n@@ -1 +1 @@\n-x = 1\n+x = 10", ok=True, name="edit_file"))
            assert await _wait(pilot, lambda: type(_body_content(b)).__name__ == "Group")
            assert "⎿ 수정함: a.py (+1/-1)" in b.title.plain

            # 비편집 도구: 내용에 @@ 가 있어도 이름으로 차단 → 평문(false positive 방지)
            app.post_message(ToolStart("r2", "run_command", "$ git diff"))
            assert await _wait(pilot, lambda: len(app.query(StepLine)) == 2)
            b2 = app.query(StepLine)[1]
            app.post_message(ToolEnd("r2", "내용\n@@ 이건 diff 아님", ok=True, name="run_command"))
            await pilot.pause()
            assert isinstance(_body_content(b2), str)

            # 편집 도구지만 실패 메시지(비diff) → 평문
            app.post_message(ToolStart("r3", "edit_file", "수정 · b"))
            assert await _wait(pilot, lambda: len(app.query(StepLine)) == 3)
            b3 = app.query(StepLine)[2]
            app.post_message(ToolEnd("r3", "실패: old 를 찾지 못했다\n비슷한 줄:", ok=True, name="edit_file"))
            await pilot.pause()
            assert isinstance(_body_content(b3), str)
    asyncio.run(go())


def test_redaction_masks_diff_prefixed_secrets():
    # §3.2b: diff 의 +/- 접두(및 문맥) 시크릿 라인도 마스킹된다
    from koda.tui.tool_handler import _redact
    out = _redact("@@ -1 +1 @@\n-API_KEY=old\n+API_KEY=new\n TOKEN=ctx")
    assert "old" not in out and "new" not in out and "ctx" not in out
    assert out.count("[REDACTED]") == 3


def test_edit_tool_gets_larger_trim_limit():
    # 편집 도구는 별도(더 큰) trim 상한 — diff 가 기본 2000자에서 반토막 나지 않는다
    from koda.tui.tool_handler import _OUTPUT_LIMIT, ToolActivityHandler
    posted = []
    h = ToolActivityHandler(posted.append)
    h.on_tool_start({"name": "edit_file"}, "", run_id="r1", inputs={})
    h.on_tool_end("@@\n" + "+ln\n" * 3000, run_id="r1")    # 2000자 훨씬 초과
    end = posted[-1]
    assert end.name == "edit_file"
    assert len(end.output) > _OUTPUT_LIMIT                 # 편집 상한(6000)이 적용돼 기본 초과 보존


# --- /resume: 저장된 세션으로 교체 + 이력 재생 ---

def test_resume_command_replays_history_and_switches_session(tmp_path):
    saved = Session(tmp_path, session_id="old1")
    saved.add_turn("이전 질문", "이전 답변", route="read", verdict=None)
    saved.save()

    async def fake_run_task(task, ws, approver, on_token, on_phase=None, **kw):
        return _result(final="새 답")

    app = _make_app(tmp_path, fake_run_task)

    async def run():
        async with app.run_test() as pilot:
            await _submit(pilot, "/resume old1")
            assert await _wait(pilot, lambda: app.runner.session.id == "old1")
            conv = app.conv
            texts = [_static_text(w) for w in conv.query(".user-turn")]
            assert texts == ["» 이전 질문"]                      # 이전 user 턴이 화면에 재생됨
            assert len(conv.query(".answer")) == 1               # assistant 답변도 재생됨
            assert app.runner.session.id == "old1"
            # 이어서 대화하면 재개한 세션에 턴이 쌓인다
            await _submit(pilot, "이어서")
            assert await _wait(pilot, lambda: len(app.runner.session.turns) == 2)
            assert app.runner.session.history[-1]["content"] == "새 답"

    asyncio.run(run())


def test_resume_unknown_id_keeps_current_session(tmp_path):
    app = _make_app(tmp_path, None)
    cur = app.runner.session.id

    async def run():
        async with app.run_test() as pilot:
            await _submit(pilot, "/resume nope")
            await pilot.pause()
            assert app.runner.session.id == cur and not app._active.busy

    asyncio.run(run())


# --- 드래그 선택 → 클립보드 복사 ---

def test_drag_selection_copies_to_clipboard(tmp_path):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_token("복사대상텍스트")
        return _result("복사대상텍스트")

    copied: list[str] = []

    async def go():
        app = _make_app(tmp_path, fake)
        app.copy_to_clipboard = copied.append          # OSC 52 대신 기록만
        async with app.run_test(size=(100, 30)) as pilot:
            await _submit(pilot, "질문")
            assert await _wait(pilot, _turn_done(app))
            md = app.conv.query(".answer").first()
            r = md.region
            await pilot.mouse_down(offset=(r.x, r.y))
            await pilot.hover(offset=(r.x + 6, r.y))
            await pilot.mouse_up(offset=(r.x + 6, r.y))
            assert await _wait(pilot, lambda: bool(copied))
            # 드래그 구간만큼(한글은 2셀/자) 앞에서부터 잘린 텍스트가 복사된다
            assert copied[0] and "복사대상텍스트".startswith(copied[0])
            # Ctrl+C: 선택이 남아 있으면 복사(종료 아님)
            n = len(copied)
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert len(copied) == n + 1 and not app._close_requested
    asyncio.run(go())


# --- 사고 블록: LLM 호출 완료 시 접힌 블록을 본문 앞에 ---

def test_reasoning_step_lands_in_group_before_open_prose(tmp_path):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_token("본문")
        await asyncio.sleep(0.6)      # 단락이 열린 채로 사고가 도착하는 창을 확보한다
        return _result("본문")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test(size=(100, 30)) as pilot:
            await _submit(pilot, "질문")
            assert await _wait(pilot, lambda: app._active.prose_md is not None)   # 산문 세그먼트가 열린 뒤
            app.post_message(ReasoningEnd("llm1", "이 질문은 단순하다. 바로 답한다."))
            assert await _wait(pilot, lambda: bool(app.query(ReasoningStep)))   # 메시지 처리 완료까지
            assert await _wait(pilot, _turn_done(app))
            kinds = _conv_types(app)
            assert kinds.index("ProcessGroup") < kinds.index("Markdown")        # 사고 그룹이 본문보다 앞
            step = app.query_one(ReasoningStep)
            assert step.collapsed and step.title.plain == "∙ 이 질문은 단순하다."   # 첫 문장만, 원문은 본문
            assert "바로 답한다" in _static_text(step._body)
            group = app.query_one(ProcessGroup)
            assert group.collapsed and group.title.startswith("과정 · 사고 1 · ")   # 완료 후 접힘

    asyncio.run(go())


def test_tool_activity_handler_posts_reasoning_on_llm_end():
    from types import SimpleNamespace
    posted = []
    h = ToolActivityHandler(posted.append)
    msg = SimpleNamespace(additional_kwargs={"reasoning_content": "  생각 중  "})
    h.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=msg)]]), run_id="r1")
    h.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=SimpleNamespace(additional_kwargs={}))]]), run_id="r2")
    assert len(posted) == 1 and isinstance(posted[0], ReasoningEnd) and posted[0].text == "생각 중"


def test_reasoning_step_of_next_turn_stays_in_that_turn(tmp_path):
    # 3턴의 사고 블록이 2턴 단락 앞에 끼어들던 버그: 턴 시작·단락 종료 때 기준점을 비워야 한다.
    calls = {"n": 0}

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            await asyncio.sleep(0.3)   # 2턴: 본문 토큰 전에 사고가 먼저 도착하는 상황을 만든다
        on_token(f"본문{calls['n']}")
        return _result(f"본문{calls['n']}")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test(size=(100, 40)) as pilot:
            await _submit(pilot, "첫 질문")
            assert await _wait(pilot, _turn_done(app))
            await _submit(pilot, "둘째 질문")
            assert await _wait(pilot, lambda: app._active.busy)
            app.post_message(ReasoningEnd("llm2", "둘째 턴의 사고"))      # 아직 본문 토큰 없음
            assert await _wait(pilot, lambda: bool(app.query(ReasoningStep)))
            assert await _wait(pilot, _turn_done(app))
            conv = app.conv
            kinds = _conv_types(app)
            i_q2 = [i for i, w in enumerate(conv.children) if "둘째 질문" in _static_text(w)][0]
            i_group = kinds.index("ProcessGroup")
            assert i_group > i_q2, kinds                      # 둘째 질의 뒤 = 둘째 턴 영역
            assert kinds[i_group:].count("Markdown") == 1     # 그 뒤에 둘째 턴 본문만 온다

    asyncio.run(go())


def test_explore_merge_and_split_rules(tmp_path):
    # 연속 탐색은 한 줄에 누적되고, 편집 도구가 오거나 실패가 섞이면 새 줄이 된다.
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test(size=(120, 40)) as pilot:
            app.post_message(ToolStart("r1", "read_file", "읽기 · a.py", ("읽음", "a.py")))
            app.post_message(ToolStart("r2", "read_file", "읽기 · b.py", ("읽음", "b.py")))
            app.post_message(ToolStart("r3", "search_text", "검색 · add", ("검색", '"add"')))
            app.post_message(ToolStart("r4", "run_readonly_command", "$ ls -la", ("조사", "ls -la")))
            assert await _wait(pilot, lambda: app.query(ExploreStep) and app.query_one(ExploreStep).count == 4)
            ex = app.query_one(ExploreStep)
            assert ex.title.plain == '⏺ 탐색 · a.py, b.py 읽음 · "add" 검색 · $ ls -la'
            for rid in ("r1", "r2", "r3", "r4"):
                app.post_message(ToolEnd(rid, f"{rid} 출력", ok=True))
            assert await _wait(pilot, lambda: ex.has_class("-ok"))
            assert "--- 읽음 b.py\nr2 출력" in _static_text(ex._body)

            app.post_message(ToolStart("r5", "edit_file", "수정 · a.py"))          # 편집은 병합을 끊는다
            app.post_message(ToolStart("r6", "read_file", "읽기 · c.py", ("읽음", "c.py")))
            assert await _wait(pilot, lambda: len(app.query(ExploreStep)) == 2)
            steps = list(app.query(StepLine))
            assert [type(s).__name__ for s in steps] == ["ExploreStep", "StepLine", "ExploreStep"]
            app.post_message(ToolEnd("r5", "수정함", ok=True, name="edit_file"))
            app.post_message(ToolEnd("r6", "오류: 없음", ok=False))                 # 실패 → 다음 탐색은 새 줄
            assert await _wait(pilot, lambda: steps[2].has_class("-fail"))
            app.post_message(ToolStart("r7", "read_file", "읽기 · d.py", ("읽음", "d.py")))
            assert await _wait(pilot, lambda: len(app.query(ExploreStep)) == 3)
            app.post_message(ReasoningEnd("l1", "다음 라운드"))                  # 사고(새 LLM 라운드)도 병합을 끊는다
            app.post_message(ToolStart("r8", "read_file", "읽기 · e.py", ("읽음", "e.py")))
            assert await _wait(pilot, lambda: len(app.query(ExploreStep)) == 4)
            assert [type(s).__name__ for s in app.query(StepLine)][-3:] == ["ExploreStep", "ReasoningStep", "ExploreStep"]
            assert len(app.query(ProcessGroup)) == 1                          # 그룹은 스텝 구간당 하나
    asyncio.run(go())


def test_thinking_toggle_hides_reasoning_steps(tmp_path):
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test() as pilot:
            app.post_message(ReasoningEnd("l1", "첫 사고"))
            assert await _wait(pilot, lambda: bool(app.query(ReasoningStep)))
            await _submit(pilot, "/thinking hide")
            assert await _wait(pilot, lambda: not app.query_one(ReasoningStep).display)
            app.post_message(ReasoningEnd("l2", "둘째 사고"))
            await pilot.pause()
            assert len(app.query(ReasoningStep)) == 1                         # 끈 동안은 만들지 않는다
            await _submit(pilot, "/thinking")                                 # 인자 없으면 토글
            assert await _wait(pilot, lambda: app.query_one(ReasoningStep).display)
            assert "/thinking" in app._commands
    asyncio.run(go())




def test_click_on_detached_paragraph_does_not_crash(tmp_path):
    # 스트리밍 재렌더로 떼어진 문단이 컴포지터 맵에 남은 채 클릭되면 textual 선택 코드가 parent=None 으로 죽는다. koda 화면은 그 클릭을 버린다.
    from textual import events

    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test(size=(100, 30)) as pilot:
            app.post_message(TokenChunk("## 제목\n\n첫 문단입니다.\n\n둘째 문단입니다.\n"))
            assert await _wait(pilot, lambda: len(app.query("MarkdownParagraph")) >= 2)
            await pilot.pause()
            para = app.query("MarkdownParagraph")[-1]
            region = app.screen.find_widget(para).region
            x, y = region.x + 2, region.y
            await para.remove()
            hit, _ = app.screen.get_widget_and_offset_at(x, y)
            assert hit is para and not hit.is_attached                  # 전제: 맵이 아직 옛 문단을 가리킨다
            app.screen._forward_event(events.MouseDown(None, x=x, y=y, delta_x=0, delta_y=0, button=1,
                                                       shift=False, meta=False, ctrl=False, screen_x=x, screen_y=y))
            await pilot.pause()
            assert app.screen._select_state is None and not app._exit    # 크래시 없음
    asyncio.run(go())


def test_mcp_connects_in_background_and_panel_toggles(tmp_path, monkeypatch):
    # 시작 시 stderr 로그 없이 백그라운드로 붙고, 상태줄에 도구 수가 뜨며, /mcp 화면에서 d 로 끄면 다음 턴의 도구 목록에서 빠진다.
    from types import SimpleNamespace

    import koda.tui.app as A
    from koda.mcp import McpServer

    async def fake_connect(dirs, timeout=20.0, cwd=None):
        return [McpServer("ctx", "프로젝트", {"url": "http://c", "transport": "streamable_http"},
                          tools=[SimpleNamespace(name="query-docs", description="문서 조회")]),
                McpServer("bad", "전역", {"url": "http://b", "transport": "streamable_http"}, error="TimeoutError: 20s")]

    monkeypatch.setattr(A, "connect_mcp", fake_connect)

    async def fake_connect_server(server, timeout=20.0):
        server.tools, server.error = [SimpleNamespace(name="query-docs", description="문서 조회")], ""
        return server

    async def fake_disconnect(server):
        server.tools = []
        return server

    monkeypatch.setattr(A, "connect_server", fake_connect_server)
    monkeypatch.setattr(A, "disconnect_server", fake_disconnect)

    async def go():
        app = KodaTui(Workspace(tmp_path), Session(tmp_path), Memory(tmp_path), [tmp_path / "mcps"], auto_yes=True)
        async with app.run_test(size=(110, 35)) as pilot:
            assert await _wait(pilot, lambda: not app._mcp_connecting)
            assert len(app.runner.mcp_tools) == 1
            assert "mcp 1 (실패 1)" in _static_text(app.query_one("#statusline"))
            await _submit(pilot, "/mcp")
            assert await _wait(pilot, lambda: type(app.screen).__name__ == "McpScreen")
            assert "query-docs" in _static_text(app.screen.query_one("#mcp_detail"))   # 강조된 첫 서버의 도구
            await pilot.press("d")                                                        # ctx 끄기
            await pilot.pause()
            assert app.runner.mcp_tools == [] and "mcp 0" in _static_text(app.query_one("#statusline"))
            assert await _wait(pilot, lambda: not app.workers)                            # 해제 워커 완료(목록 다시 그림)
            await pilot.press("d")                                                        # 다시 켜기 → 재연결
            assert await _wait(pilot, lambda: len(app.runner.mcp_tools) == 1)
            assert "mcp 1 (실패 1)" in _static_text(app.query_one("#statusline"))
            await pilot.press("escape")
            assert await _wait(pilot, lambda: type(app.screen).__name__ != "McpScreen")
    asyncio.run(go())


def test_at_path_picker_completes_and_does_not_submit(tmp_path):
    # @ 뒤 입력에 맞는 파일·폴더 후보가 입력창 위에 뜨고, Tab/Enter 는 제출 대신 경로를 채운다. Esc 는 닫는다.
    (tmp_path / "mcps").mkdir()
    (tmp_path / "mcps" / "exa.json").write_text("{}")
    (tmp_path / "README.md").write_text("x")
    submitted = []

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        submitted.append(task)
        return _result("ok")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test(size=(100, 30)) as pilot:
            inp = app.query_one("#prompt", PromptArea)
            picker = app.query_one("#path_picker")
            inp.focus()
            await pilot.press(*"보고서 @mc")
            assert await _wait(pilot, lambda: picker.display)
            assert [str(o.id) for o in picker._options][:2] == ["@mcps/", "@mcps/exa.json "]
            await pilot.press("enter")                                   # 폴더 선택: @mcps/ 로 채우고 목록은 하위로 이어짐
            assert await _wait(pilot, lambda: inp.text == "보고서 @mcps/")
            assert submitted == [] and picker.display
            await pilot.press("tab")                                     # 파일 선택: 공백까지 붙이고 목록 닫힘
            assert await _wait(pilot, lambda: inp.text == "보고서 @mcps/exa.json ")
            assert not picker.display and not inp.picker_open
            await pilot.press(*"@RE")
            assert await _wait(pilot, lambda: picker.display)
            await pilot.press("escape")
            assert await _wait(pilot, lambda: not picker.display)
            await pilot.press("enter")                                   # 목록이 닫힌 뒤 Enter 는 제출
            assert await _wait(pilot, lambda: submitted == ["보고서 @mcps/exa.json @RE"])
    asyncio.run(go())


def test_slash_command_picker_completes_and_full_command_submits(tmp_path):
    # / 로 시작하면 입력 중인 접두에 맞는 명령이 설명과 함께 뜨고 Tab/Enter 로 채운다. 다 친 명령은 후보에 없어 Enter 가 바로 실행된다.
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test(size=(100, 30)) as pilot:
            inp = app.query_one("#prompt", PromptArea)
            picker = app.query_one("#path_picker")
            inp.focus()
            await pilot.press(*"/re")
            assert await _wait(pilot, lambda: picker.display)
            assert [str(o.id) for o in picker._options] == ["/resume ", "/rename ", "/remember "]
            await pilot.press("down", "down", "enter")
            assert await _wait(pilot, lambda: inp.text == "/remember " and not picker.display)
            inp.clear()
            await pilot.press(*"/help")
            await pilot.pause()
            assert not picker.display                                    # 정확히 일치하는 명령은 후보가 아니다
            await pilot.press("enter")                                   # 그대로 실행됨(입력창 비워짐)
            assert await _wait(pilot, lambda: inp.text == "")
    asyncio.run(go())


def test_statusline_context_usage_status_and_model_switch(tmp_path, monkeypatch):
    # 상태줄은 모델·모드·게이지를 보인다. 게이지는 대화 히스토리가 압축 임계에 다가간 정도이고, 프롬프트 토큰은 /status 에 있다.
    import koda.tui.app as A
    from koda.config import ModelSettings
    from koda.session import COMPACT_THRESHOLD_CHARS
    from koda.tui.model import UsageUpdate

    st = ModelSettings(_env_file=None, openai_model="deepseek-v4-flash", deepseek_api_key="dk", context_budget_tokens=10000)
    assert ModelSettings(_env_file=None).context_budget_tokens == 131_072   # 기본 예산은 131k
    monkeypatch.setattr(A, "select_provider", lambda settings: ("openai", ""))

    async def go():
        session = Session(tmp_path)
        app = KodaTui(Workspace(tmp_path), session, Memory(tmp_path), None, auto_yes=True,
                      run_task_fn=None, model_name="deepseek-v4-flash", settings=st)
        async with app.run_test(size=(120, 30)) as pilot:
            line = _static_text(app.query_one("#statusline"))
            assert line.startswith("deepseek-v4-flash · yolo") and "컨텍스트 ░░░░░░░░░░ 0%" in line
            assert "대기 중" not in line and "koda" not in line     # 진행 상태는 활동바가 말한다
            # 대화가 쌓이면 게이지가 오른다 (턴마다 실제로 증가하는 값)
            session.add_turn("질문", "답" * (COMPACT_THRESHOLD_CHARS // 4), route="read", verdict=None)
            app._refresh_statusline()
            await pilot.pause()
            assert "컨텍스트 ███░░░░░░░ 25%" in _static_text(app.query_one("#statusline"))
            app.post_message(UsageUpdate(7000, 500))                # 토큰 사용량은 게이지를 흔들지 않는다
            await pilot.pause()
            assert "컨텍스트 ███░░░░░░░ 25%" in _static_text(app.query_one("#statusline"))
            await _submit(pilot, "/status")
            assert await _wait(pilot, lambda: type(app.screen).__name__ == "InfoScreen")
            body = _static_text(app.screen.query_one("#info_body"))
            assert "deepseek-v4-flash (deepseek)" in body
            assert "압축 임계" in body and "25%" in body
            assert "최대 7.5k / 예산 10k 토큰" in body and "세션 누적 입력 7.0k 출력 0.5k" in body
            await pilot.press("escape")
            assert await _wait(pilot, lambda: type(app.screen).__name__ != "InfoScreen")
            await _submit(pilot, "/model gpt-5.6 key=ok")
            assert await _wait(pilot, lambda: app.runner.settings.openai_model == "gpt-5.6")
            assert app.runner.settings.openai_api_key == "ok" and app.runner.settings.use_responses_api is True
            assert _static_text(app.query_one("#statusline")).startswith("gpt-5.6 · yolo")
    asyncio.run(go())


def test_notifications_with_brackets_do_not_crash(tmp_path):
    # /model 안내와 /help 목록엔 [provider=…] 같은 대괄호가 있다. 알림이 Rich 마크업으로 해석되면 MarkupError 로 앱이 죽는다.
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test(size=(120, 30)) as pilot:
            await _submit(pilot, "/help")                                # /model 단축 문법의 [provider=…] 가 목록에 실린다
            await pilot.pause()
            app.runner.memory.remember("[대괄호] 가 든 메모리 [b]굵게[/b]")
            await _submit(pilot, "/memory")
            await pilot.pause()
            from textual.content import Content
            notes = list(app._notifications)
            texts = [n.message for n in notes]
            assert any("[provider=" in t for t in texts) and any("[대괄호]" in t for t in texts)
            # 헤드리스에선 토스트가 mount 되지 않으므로 토스트와 같은 방식으로 직접 파싱해 본다(markup=True 면 MarkupError).
            assert all(n.markup is False for n in notes)
            for n in notes:
                Content.from_text(n.message, markup=n.markup)
    asyncio.run(go())


# --- 세션 패널: 목록 · 상태 표시 · 재개/삭제 · 제목 시점 ---

def _panel_ids(app) -> list[str]:
    lst = app.query_one("#sp_list")
    return [str(lst.get_option_at_index(i).id) for i in range(lst.option_count)]


def _panel_row(app, i: int) -> str:
    return app.query_one("#sp_list").get_option_at_index(i).prompt.plain


def test_session_panel_lists_current_first_and_toggles(tmp_path):
    old = Session(tmp_path, session_id="old1")
    old.title = "지난 작업"
    old.add_turn("이전 질문", "이전 답변", route="read", verdict=None)
    old.save()

    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test(size=(120, 30)) as pilot:
            panel = app.query_one("#session_panel")
            assert not panel.has_class("-hidden")                       # 100열 이상이면 펼친 채 시작
            title = _static_text(app.query_one("#sp_title"))
            assert title.startswith("📁") and title.endswith(tmp_path.name[-12:])   # 머리글에 워크스페이스 이름(길면 앞을 자름)
            assert _panel_ids(app) == [app.runner.session.id, "old1"]   # 미저장 현재 세션이 맨 앞
            assert "● 새 세션" in _panel_row(app, 0)                     # 대기 중 = ● + 임시 이름
            assert "지난 작업" in _panel_row(app, 1) and "●" not in _panel_row(app, 1)
            await pilot.press("ctrl+b")
            assert await _wait(pilot, lambda: panel.has_class("-hidden"))
            await pilot.press("ctrl+b")
            assert await _wait(pilot, lambda: not panel.has_class("-hidden"))
    asyncio.run(go())


def test_panel_starts_hidden_on_narrow_terminal(tmp_path):
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test(size=(90, 30)):
            assert app.query_one("#session_panel").has_class("-hidden")
    asyncio.run(go())


def test_panel_resume_and_delete_with_confirm(tmp_path):
    for sid in ("old1", "old2"):
        s = Session(tmp_path, session_id=sid)
        s.title = f"작업 {sid}"
        s.add_turn(f"{sid} 질문", f"{sid} 답변", route="read", verdict=None)
        s.save()

    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test(size=(120, 30)) as pilot:
            lst = app.query_one("#sp_list")
            lst.focus()
            await pilot.pause()
            lst.highlighted = _panel_ids(app).index("old1")
            await pilot.press("enter")                                   # 패널에서 재개
            assert await _wait(pilot, lambda: app.runner.session.id == "old1")
            assert any("old1 질문" in _static_text(w) for w in app.query(".user-turn"))
            lst.focus()
            await pilot.pause()
            lst.highlighted = _panel_ids(app).index("old1")
            await pilot.press("d")                                       # 현재 세션 삭제는 거부
            await pilot.pause()
            assert (tmp_path / ".koda" / "sessions" / "old1.json").exists()
            lst.highlighted = _panel_ids(app).index("old2")
            await pilot.press("d")                                       # 다른 세션은 확인 모달 뒤 삭제
            assert await _wait(pilot, lambda: type(app.screen).__name__ == "ConfirmScreen")
            await pilot.click("#confirm_yes")
            assert await _wait(pilot, lambda: not (tmp_path / ".koda" / "sessions" / "old2.json").exists())
            assert "old2" not in _panel_ids(app)
    asyncio.run(go())


def test_title_set_after_first_turn_with_one_retry(tmp_path):
    # run_task 계약의 대역: trace_name 이 비어 있을 때만 제목을 짓는다. 1턴은 빈약(빈 제목), 2턴에 성공.
    titles = iter(["", "진짜 제목"])
    seen = []

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        seen.append(kw["trace_name"])
        on_token("답")
        return _result(final="답", title=(next(titles) if not kw["trace_name"] else ""))

    async def go():
        session = Session(tmp_path)
        app = _make_app(tmp_path, fake, session=session)
        async with app.run_test(size=(120, 30)) as pilot:
            await _submit(pilot, "안녕")
            assert await _wait(pilot, _turn_done(app))
            assert session.title == "" and session.title_tries == 1      # 빈약 → 다음 실질 턴에서 재시도
            assert "안녕" in _panel_row(app, 0)                          # 제목 전엔 질의 앞부분
            await _submit(pilot, "README 요약해줘")
            assert await _wait(pilot, lambda: not app._active.busy and len(app.conv.query(".turn-footer")) == 2)
            assert session.title == "진짜 제목" and session.title_tries == 2
            assert "진짜 제목" in _panel_row(app, 0)
            await _submit(pilot, "셋째 질문")
            assert await _wait(pilot, lambda: not app._active.busy and len(app.conv.query(".turn-footer")) == 3)
            assert seen == ["", "", "진짜 제목"]                          # 확정 후엔 그대로 전달
    asyncio.run(go())


def test_title_gives_up_after_two_tries(tmp_path):
    seen = []

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        seen.append(kw["trace_name"])
        on_token("답")
        return _result(final="답", title="")

    async def go():
        session = Session(tmp_path)
        app = _make_app(tmp_path, fake, session=session)
        async with app.run_test(size=(120, 30)) as pilot:
            for i, q in enumerate(("하나", "둘", "셋째 질문입니다"), start=1):
                await _submit(pilot, q)
                assert await _wait(pilot, lambda: not app._active.busy and len(app.conv.query(".turn-footer")) == i)
            assert session.title == "" and session.title_tries == 2       # 2회로 캡
            assert seen == ["", "", "셋째 질문입니다"]                     # 3턴부터 질의 앞부분을 trace 이름으로
    asyncio.run(go())


def test_rename_sets_manual_title(tmp_path):
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test(size=(120, 30)) as pilot:
            await _submit(pilot, "/rename 패널 작업")
            await pilot.pause()
            s = app.runner.session
            assert s.title == "패널 작업" and s.title_manual is True
            assert "패널 작업" in _panel_row(app, 0)
    asyncio.run(go())


def test_compaction_updates_title_unless_manual(tmp_path, monkeypatch):
    async def fake_sum(old, settings=None):
        return "요약", "압축 제목"

    monkeypatch.setattr(runner_mod, "summarize_history", fake_sum)

    async def go():
        session = Session(tmp_path)
        for i in range(6):
            session.add_turn(f"q{i}", f"a{i}", route="read", verdict=None)
        app = _make_app(tmp_path, None, session=session)
        async with app.run_test(size=(120, 30)):
            await asyncio.to_thread(app.runner.compact_now)
            assert session.title == "압축 제목"                            # 압축이 제목을 공짜로 갱신
            session.title = "내 제목"
            session.title_manual = True
            for i in range(6, 12):
                session.add_turn(f"q{i}", f"a{i}", route="read", verdict=None)
            await asyncio.to_thread(app.runner.compact_now)
            assert session.title == "내 제목"                              # 수동 제목은 덮지 않는다
    asyncio.run(go())


def test_panel_shows_running_state_during_turn(tmp_path):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_token("진행")
        await asyncio.sleep(0.6)
        return _result(final="진행")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test(size=(120, 30)) as pilot:
            await _submit(pilot, "질문")
            assert await _wait(pilot, lambda: "답변 중" in _panel_row(app, 0))
            assert await _wait(pilot, _turn_done(app))
            assert "답변 중" not in _panel_row(app, 0)                     # 턴이 끝나면 대기 표시로 복귀
    asyncio.run(go())


async def _panel_key(pilot, key):
    """세션 패널에 포커스를 두고 키를 누른다(답변 중에는 입력창이 잠기므로 전환은 패널로 한다)."""
    lst = pilot.app.query_one("#sp_list")
    lst.focus()
    await pilot.pause()
    await pilot.press(key)
    await pilot.pause()


async def _panel_open(pilot, session_id):
    lst = pilot.app.query_one("#sp_list")
    lst.focus()
    await pilot.pause()
    lst.highlighted = _panel_ids(pilot.app).index(session_id)
    await pilot.press("enter")
    await pilot.pause()


def test_background_turn_continues_while_other_session_answers(tmp_path):
    """세션 A 가 답변하는 도중 패널로 B 를 열어 질의할 수 있고, 두 턴이 각자의 대화 영역에서 나란히 끝난다."""
    go_on = {"A": False, "B": False}

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        who = task[0]
        on_token(f"{who} 답변 시작")
        while not go_on[who]:          # 스레드마다 루프가 달라 asyncio.Event 대신 플래그를 폴링한다
            await asyncio.sleep(0.02)
        return _result(final=f"{who} 답변 시작", title=f"{who} 세션")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test(size=(120, 30)) as pilot:
            a = app.runner.session.id
            await _submit(pilot, "A 질문")
            assert await _wait(pilot, lambda: app._runs[a].busy and "A 답변" in app._runs[a].draft)
            assert app.query_one("#prompt").disabled              # 답변 중인 세션에는 입력이 잠긴다
            await _panel_key(pilot, "n")                          # 답변 중에도 패널로 새 세션을 연다
            assert await _wait(pilot, lambda: app._active_id != a)
            b = app._active_id
            assert app._runs[a].busy and not app._runs[b].busy    # A 는 배경에서 계속 돈다
            assert not app.query_one("#prompt").disabled          # B 에서는 바로 질의 가능
            await _submit(pilot, "B 질문")
            assert await _wait(pilot, lambda: app._runs[b].busy)
            go_on["B"] = True
            assert await _wait(pilot, lambda: not app._runs[b].busy)
            assert app._runs[a].busy                              # B 가 끝나도 A 는 그대로 진행
            go_on["A"] = True
            assert await _wait(pilot, lambda: not app._runs[a].busy)
            # 각 세션의 답변은 자기 대화 영역에만 쌓인다
            assert app._runs[a].draft == "A 답변 시작" and app._runs[b].draft == "B 답변 시작"
            assert len(app._runs[a].conv.query(".turn-footer")) == 1
            assert len(app._runs[b].conv.query(".turn-footer")) == 1
            assert app._runs[a].conv.display is False and app._runs[b].conv.display is True
            assert len(app._runs[a].session.turns) == 1 and len(app._runs[b].session.turns) == 1
            assert app._runs[a].session.title == "A 세션" and app._runs[b].session.title == "B 세션"
    asyncio.run(go())


def test_switching_back_to_busy_session_shows_its_screen_and_locks_input(tmp_path):
    """답변 중인 세션으로 돌아가면 그 세션의 화면·활동바가 보이고, 그 세션에서는 끝날 때까지 입력이 잠긴다."""
    go_on = {"go": False}

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_token("긴 답변")
        while not go_on["go"]:
            await asyncio.sleep(0.02)
        return _result(final="긴 답변")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test(size=(120, 30)) as pilot:
            a = app.runner.session.id
            await _submit(pilot, "A 질문")
            assert await _wait(pilot, lambda: app._runs[a].busy)
            await _panel_key(pilot, "n")
            assert await _wait(pilot, lambda: app._active_id != a)
            assert not app.query_one("#activity").display          # 새 세션은 조용하다
            await _panel_open(pilot, a)                            # 답변 중인 세션으로 복귀
            assert app._active_id == a and app.conv is app._runs[a].conv
            assert app.query_one("#prompt").disabled
            assert app.query_one("#activity").display              # 활동바가 그 세션 기준으로 돌아온다
            go_on["go"] = True
            assert await _wait(pilot, _turn_done(app))
            assert not app.query_one("#prompt").disabled
    asyncio.run(go())


def test_panel_state_glyphs_and_branch(tmp_path, monkeypatch):
    """상태 글리프 어휘: 실행 중 스피너 · 승인 대기 ◐ · 실패 ✕ · 보고 있는 세션 ● · 그 외 ○. 두 번째 줄엔 브랜치와 시각."""
    import koda.tui.app as A

    monkeypatch.setattr(A, "git_ref", lambda root: ("feat/x", "abc1234"))
    other = Session(tmp_path, session_id="old1")
    other.title = "지난 작업"
    other.branch = "main"
    other.add_turn("q", "a", route="read", verdict=None)
    other.save()

    async def boom(task, ws, approver, on_token, on_phase, **kw):
        raise RuntimeError("boom")

    async def go():
        app = _make_app(tmp_path, boom)
        async with app.run_test(size=(120, 30)) as pilot:
            cur = app.runner.session
            assert cur.branch == "feat/x" and cur.base_commit == "abc1234"   # 세션에 브랜치·기준 커밋이 새겨진다
            assert "● " in _panel_row(app, 0) and "⎇ feat/x" in _panel_row(app, 0)
            row_other = _panel_row(app, _panel_ids(app).index("old1"))
            assert row_other.startswith("○ ") and "⎇ main · " in row_other
            await _submit(pilot, "실패할 질의")
            assert await _wait(pilot, _turn_done(app))
            assert app._active.state == "failed" and "✕ " in _panel_row(app, 0)
            assert "실패 · " in _panel_row(app, 0)
    asyncio.run(go())


def test_panel_approval_glyph_is_yellow_not_error(tmp_path):
    """승인 대기는 ◐(노랑)이고 빨강 ✕ 는 실패 전용이다."""
    record = []

    async def go():
        app = _make_app(tmp_path, _approval_fake(record), approval_timeout=30.0)
        async with app.run_test(size=(120, 30)) as pilot:
            await _submit(pilot, "테스트 실행해줘")
            assert await _wait(pilot, lambda: app._active.state == "approval")
            row = app.query_one("#sp_list").get_option_at_index(0).prompt
            assert row.plain.startswith("◐ ") and "승인 대기" in row.plain
            assert any("yellow" in str(sp.style) for sp in row.spans) and not any("red" in str(sp.style) for sp in row.spans)
            await pilot.click("#approve")
            assert await _wait(pilot, _turn_done(app))
    asyncio.run(go())


def test_panel_time_is_last_answer_and_order_stable_on_switch(tmp_path):
    """패널 시각은 마지막 답변 시각으로 고정되고, 세션을 오가도 순서가 바뀌지 않는다."""
    import time as _t

    old = Session(tmp_path, session_id="old1")
    old.title = "지난 작업"
    old.add_turn("이전 질문", "이전 답변", route="read", verdict=None)
    old.updated_at = _t.time() - 3 * 3600          # 3시간 전 답변
    old.save()

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_token("답")
        return _result(final="답")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test(size=(120, 30)) as pilot:
            new_id = app.runner.session.id
            assert "대화 없음" in _panel_row(app, 0)                 # 아직 답변이 없는 세션
            await _submit(pilot, "질문")
            assert await _wait(pilot, _turn_done(app))
            assert _panel_ids(app) == [new_id, "old1"]              # 방금 답변받은 세션이 위
            assert "방금" in _panel_row(app, 0) and "3시간 전" in _panel_row(app, 1)
            await _panel_open(pilot, "old1")                        # 전환은 시각·순서를 바꾸지 않는다
            assert app._active_id == "old1"
            assert _panel_ids(app) == [new_id, "old1"]
            assert "3시간 전" in _panel_row(app, 1) and "방금" in _panel_row(app, 0)
            await _submit(pilot, "이전 세션에 이어서")               # 답변을 받으면 그때 위로 올라온다
            assert await _wait(pilot, _turn_done(app))
            assert _panel_ids(app) == ["old1", new_id]
            assert "방금" in _panel_row(app, 0)
    asyncio.run(go())


def test_background_approval_waits_until_session_is_opened(tmp_path):
    """배경 세션의 승인 요청은 보고 있는 화면을 가로채지 않고, 그 세션을 열 때 모달로 묻는다."""
    go_on = {"go": False}
    record = []

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_token("시작")
        while not go_on["go"]:
            await asyncio.sleep(0.02)
        ok = approver("run_command", {"argv": ["pytest", "-q"]})
        record.append(ok)
        return _result(final=f"approved={ok}")

    async def go():
        app = _make_app(tmp_path, fake, approval_timeout=30.0)
        async with app.run_test(size=(120, 30)) as pilot:
            a = app.runner.session.id
            await _submit(pilot, "테스트 실행해줘")
            assert await _wait(pilot, lambda: app._runs[a].busy)
            await _panel_key(pilot, "n")
            assert await _wait(pilot, lambda: app._active_id != a)
            go_on["go"] = True                                       # 배경 세션에서 승인 요청 발생
            assert await _wait(pilot, lambda: app._runs[a].approval is not None)
            assert not isinstance(app.screen, ApprovalModal)          # 보고 있던 화면을 가로채지 않는다
            assert app._runs[a].state == "approval"                   # 패널은 승인 대기로 보인다
            await _panel_open(pilot, a)
            assert await _wait(pilot, lambda: isinstance(app.screen, ApprovalModal))
            await pilot.click("#approve")
            assert await _wait(pilot, _turn_done(app))
            assert record == [True] and app._runs[a].approval is None
    asyncio.run(go())


def test_model_wizard_picks_provider_then_fills_values(tmp_path, monkeypatch):
    # /model 만 치면 provider 목록 → 선택한 provider 의 모델명·base_url·키 입력 창. 마지막 칸 Enter 로 적용된다.
    import koda.tui.app as A
    from koda.config import ModelSettings

    st = ModelSettings(_env_file=None, openai_model="deepseek-v4-flash", deepseek_api_key="dk", local_model="qwen3.8-27b")
    monkeypatch.setattr(A, "select_provider", lambda settings: (settings.primary, ""))

    async def go():
        app = KodaTui(Workspace(tmp_path), Session(tmp_path), Memory(tmp_path), None, auto_yes=True, model_name="deepseek-v4-flash", settings=st)
        async with app.run_test(size=(120, 34)) as pilot:
            await _submit(pilot, "/model")
            assert await _wait(pilot, lambda: type(app.screen).__name__ == "ModelScreen")
            assert not app.screen.query_one("#model_form").display
            await pilot.press("down", "enter")                                            # openai 선택
            assert await _wait(pilot, lambda: app.screen.query_one("#model_form").display)
            assert app.screen.query_one("#m_url").value == "https://api.openai.com/v1"    # 프로필 기본값이 미리 채워짐
            assert app.screen.query_one("#m_name").value == ""                            # 다른 provider 라 모델명은 비어 있음
            await pilot.press(*"gpt-5.6", "enter")                                        # 모델명 → 다음 칸
            await pilot.press("enter")                                                    # base_url 그대로 → 다음 칸
            await pilot.press(*"ok", "enter")                                             # 키 → 적용
            assert await _wait(pilot, lambda: app.runner.settings.openai_model == "gpt-5.6")
            assert app.runner.settings.openai_api_key == "ok" and app.runner.settings.primary == "openai"
            assert _static_text(app.query_one("#statusline")).startswith("gpt-5.6 · yolo")
            await _submit(pilot, "/model")
            assert await _wait(pilot, lambda: type(app.screen).__name__ == "ModelScreen")
            await pilot.press("enter")                                                    # local 선택: 현재 로컬 값이 채워짐
            assert await _wait(pilot, lambda: app.screen.query_one("#model_form").display)
            assert app.screen.query_one("#m_name").value == "qwen3.8-27b"
            await pilot.press("escape")                                                   # 뒤로(목록)
            assert await _wait(pilot, lambda: not app.screen.query_one("#model_form").display)
            await pilot.press("escape")                                                   # 닫기
            assert await _wait(pilot, lambda: type(app.screen).__name__ != "ModelScreen")
            assert app.runner.settings.openai_model == "gpt-5.6"                          # 취소는 설정을 바꾸지 않는다
    asyncio.run(go())


def test_edit_diff_is_shown_in_conversation_not_folded(tmp_path):
    """편집 diff 는 접힌 과정 박스 안이 아니라 대화에 직접 보인다. 이후 스텝은 diff 아래 새 그룹으로 간다."""
    diff = "수정함: calc.py (+1/-1)\n@@ -1 +1 @@\n-x = 1\n+x = 10"

    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test(size=(120, 40)) as pilot:
            app.post_message(ToolStart("r1", "edit_file", "수정 · calc.py"))
            assert await _wait(pilot, lambda: bool(app.query(StepLine)))
            app.post_message(ToolEnd("r1", diff, ok=True, name="edit_file"))
            assert await _wait(pilot, lambda: bool(app.query(DiffBlock)))
            block = app.query_one(DiffBlock)
            assert block.parent is app.conv                       # 과정 그룹 안이 아니라 대화 직속
            assert _conv_types(app) == ["ProcessGroup", "DiffBlock"]
            # 다음 도구는 diff 아래 새 그룹으로 (시간순 유지)
            app.post_message(ToolStart("r2", "read_file", "읽기 · calc.py", ("읽음", "calc.py")))
            assert await _wait(pilot, lambda: len(app.query(ProcessGroup)) == 2)
            assert _conv_types(app) == ["ProcessGroup", "DiffBlock", "ProcessGroup"]
            # 턴이 끝나 과정이 접혀도 diff 는 그대로 보인다
            app.post_message(ToolEnd("r2", "내용", ok=True, name="read_file"))
            for w in app._active.turn_widgets:
                if isinstance(w, ProcessGroup):
                    w.summarize()
            await pilot.pause()
            assert all(g.collapsed for g in app.query(ProcessGroup))
            assert block.display and block.is_attached
    asyncio.run(go())


def test_non_diff_edit_output_stays_in_step(tmp_path):
    """diff 가 아닌 편집 결과(실패 안내 등)는 대화에 별도 블록을 만들지 않는다."""
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test(size=(120, 30)) as pilot:
            app.post_message(ToolStart("r1", "edit_file", "수정 · b.py"))
            assert await _wait(pilot, lambda: bool(app.query(StepLine)))
            app.post_message(ToolEnd("r1", "실패: old 를 찾지 못했다\n비슷한 줄:", ok=True, name="edit_file"))
            await pilot.pause()
            assert not app.query(DiffBlock)
    asyncio.run(go())
