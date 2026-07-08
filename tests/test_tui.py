"""TUI 어댑터 결정론 검증 (plan/TEXTUAL_TUI_PLAN.md §6 Phase 5, §7 AC 4~10).

- textual 미설치 환경(기본 sync)에서는 전체 스킵 — 기본 스위트는 TUI extra 없이 통과 (AC 1~3 보호).
- LLM 없이 fake run_task 로 스트림·이벤트 매핑·승인 브리지·세션 parity 를 검증한다.
- pytest-asyncio 없이 asyncio.run 으로 감싼다 (의존성 최소).
"""
from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("textual")

import koda.tui.runner as runner_mod
from koda.cli import main
from koda.memory import Memory
from koda.session import Session
from koda.tui.app import KodaTui
from koda.tui.model import ToolEnd, ToolStart
from koda.tui.tool_handler import ToolActivityHandler, tool_title
from koda.tui.widgets import ApprovalModal, ToolBlock, _args_preview
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
    return KodaTui(Workspace(tmp_path), session, Memory(tmp_path),
                           mcp_tools if mcp_tools is not None else [], auto_yes=auto_yes,
                           run_task_fn=run_task_fn, approval_timeout=approval_timeout)


async def _wait(pilot, cond, timeout=10.0):
    """조건 폴링. pilot.pause 대신 asyncio.sleep 사용 — app.exit() 이후에도 pause 가
    멈춘 메시지 펌프를 기다리며 영구 블록하지 않도록 (앱 태스크는 같은 루프에서 돈다)."""
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
    inp.value = text
    await pilot.press("enter")


def _static_text(widget) -> str:
    r = widget.render()
    return getattr(r, "plain", None) or str(r)


def _notifs(app) -> str:
    return "\n".join(n.message for n in app._notifications)


def _turn_done(app):
    return lambda: not app._busy and bool(app.query(".turn-footer"))


# --- AC 4: 앱이 뜨고 입력을 받을 수 있다 (headless) ---

def test_boot_widgets_exist(tmp_path):
    async def go():
        app = _make_app(tmp_path, run_task_fn=None)
        async with app.run_test():
            assert app.query_one("#prompt") is not None
            assert app.query_one("#conversation") is not None
            assert app.query_one("#statusline") is not None
            assert "koda" in _static_text(app.query_one("#statusline"))
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
            assert app._draft == "# 제목\n\n본문 문단"
            # 대화 영역에 user 턴 + Markdown 위젯 + footer 가 순서대로 쌓인다
            conv = app.query_one("#conversation")
            texts = [_static_text(w) for w in conv.query(".user-turn")]
            assert any("설명해줘" in t for t in texts)
            footers = [_static_text(w) for w in conv.query(".turn-footer")]
            assert any("route: read" in t for t in footers)
    asyncio.run(go())


# --- AC 6: 이벤트가 올바른 영역에 표시된다 (+ retry 리셋, think/heartbeat 는 status 전용) ---

def test_events_routed(tmp_path):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_phase("edit")
        on_token("버릴 부분")                     # retry 전 부분 스트림 → 폐기 대상
        on_phase("think:5")
        on_phase("heartbeat:30s")
        on_phase("tool:workspace_write_file")
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
            assert "write_file" in app._turn_tools
            assert app._draft == "다시 쓴 본문"             # retry 가 1차 부분 스트림 폐기
            # 검증 결과·판정 근거·trace 는 우측 패널 폐지 후 턴 footer 에 인라인된다
            footer = "\n".join(_static_text(w) for w in app.query(".turn-footer"))
            assert "verdict: pass" in footer and "테스트 근거" in footer
            assert "trace: http://lf/t/1" in footer
    asyncio.run(go())


# --- 활동바: 턴 중 스피너·단계 라벨·경과초·생각 토큰 표시, 종료 시 숨김 ---

def test_activity_indicator_during_turn(tmp_path):
    import threading
    release = threading.Event()

    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_phase("read")
        on_phase("think:42")
        while not release.is_set():
            await asyncio.sleep(0.01)
        on_token("답")
        return _result(final="답")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test() as pilot:
            act = app.query_one("#activity")

            def act_text():
                r = act.render()
                return getattr(r, "plain", None) or str(r)

            assert act.display is False                    # 대기 중엔 숨김
            await _submit(pilot, "질문")
            assert await _wait(pilot, lambda: act.display)
            assert await _wait(pilot, lambda: "코드 읽는 중" in act_text())   # 단계 라벨
            assert await _wait(pilot, lambda: "~42 tok" in act_text())      # reasoning 카운트
            assert "s" in act_text()                       # 경과 초
            release.set()
            assert await _wait(pilot, _turn_done(app))
            assert act.display is False                    # 종료 후 숨김
    asyncio.run(go())


# --- AC 7: approval modal — approve=True / deny·close=False / timeout=False ---

def _approval_fake(record):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        ok = approver("workspace_run_command", {"argv": ["pytest", "-q"]})
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
            assert "workspace_run_command" in _static_text(app.screen.query_one("#approval_title"))
            assert "$ pytest -q" in _static_text(app.screen.query_one("#approval_args"))
            # 승인 대기는 활동바 라벨로 노출된다(롤업에 가려지지 않게 — agy 리뷰 중대)
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
            # 고아 modal 이 남지 않는다 — _end_turn 이 정리 (리뷰 Medium)
            assert await _wait(pilot, lambda: not isinstance(app.screen, ApprovalModal))
            assert not app.query_one("#prompt").disabled
    asyncio.run(go())


# --- AC 8: --yes 는 auto 승인(.auto=True) — confirm 등급 차단은 middleware 계약이 담당 ---

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
            footer = "\n".join(_static_text(w) for w in app.query(".turn-footer"))
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
            assert await _wait(pilot, lambda: not app._busy and len(app.query(".turn-footer")) == 2)

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


# --- Phase 3: /remember·/memory 명령 ---

def test_remember_and_memory_commands(tmp_path):
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test() as pilot:
            await _submit(pilot, "/remember 파이썬 3.14 를 쓴다")
            await pilot.pause()
            assert "파이썬 3.14 를 쓴다" in Memory(tmp_path).read()
            assert "기억함." in _notifs(app)
            await _submit(pilot, "/memory")
            await pilot.pause()
            assert "파이썬 3.14" in _notifs(app)   # /memory 는 토스트로 내용 표시(패널 폐지)
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
            footer = "\n".join(_static_text(w) for w in app.query(".turn-footer"))
            assert "턴 실패: RuntimeError: boom" in footer
            assert not app.query_one("#prompt").disabled
    asyncio.run(go())


# --- Phase 3: /exit → 세션 저장 + 메모리 추출 (chat 종료 흐름 동등) ---

def test_close_session_extracts_memory(tmp_path, monkeypatch):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        return _result(final="답변")

    async def fake_extract(history):
        return ["- 로컬 모델은 Qwen 이다"]

    monkeypatch.setattr(runner_mod, "extract_memories", fake_extract)

    async def go():
        session = Session(tmp_path)
        app = _make_app(tmp_path, fake, session=session)
        async with app.run_test() as pilot:
            await _submit(pilot, "질문")
            assert await _wait(pilot, _turn_done(app))
            await _submit(pilot, "/exit")
            assert await _wait(pilot, lambda: app.return_value is not None)
        assert app.return_value is True  # saved
        assert "로컬 모델은 Qwen 이다" in Memory(tmp_path).read()
        assert session.path.exists()
    asyncio.run(go())


# --- cli 배선: tui 서브커맨드 → lazy import 된 run_tui 로 args 전달 ---

def test_cli_tui_wiring(tmp_path, monkeypatch):
    import koda.tui.app as app_mod
    seen = {}

    def fake_run_tui(args):
        seen.update(vars(args))
        return 7

    monkeypatch.setattr(app_mod, "run_tui", fake_run_tui)
    rc = main(["tui", "--workspace", str(tmp_path), "--no-mcp", "--yes"])
    assert rc == 7
    assert seen["workspace"] == str(tmp_path) and seen["no_mcp"] and seen["yes"]
    assert seen["mcps_dir"] == "mcps" and seen["resume"] is None


# --- §9: 승인 args 미리보기 — 길이 제한 + run_command 명령 표시 + secret redaction ---

def test_args_preview():
    assert _args_preview("workspace_run_command", {"argv": ["rm", "-rf", "x"]}) == "$ rm -rf x"
    long = _args_preview("workspace_write_file", {"content": "x" * 2000})
    assert len(long) <= 500 + len(" …(생략)") and long.endswith(" …(생략)")
    # secret 성 키는 값이 노출되지 않는다 (§9 redaction)
    masked = _args_preview("some_mcp_tool", {"api_key": "sk-live-123", "path": "a.py"})
    assert "sk-live-123" not in masked and "[REDACTED]" in masked and "a.py" in masked


# --- run_tui 진입점: resume 실패 rc=2, 정상 경로 rc=0 (App.run 은 스텁) ---

def test_run_tui_entry_paths(tmp_path, monkeypatch):
    import argparse

    import koda.tui.app as app_mod

    monkeypatch.setattr(app_mod.KodaTui, "run", lambda self: None)

    def ns(**kw):
        base = {"workspace": str(tmp_path), "yes": False, "resume": None,
                "mcps_dir": "mcps", "no_mcp": True}
        base.update(kw)
        return argparse.Namespace(**base)

    assert app_mod.run_tui(ns(resume="없는세션")) == 2   # 세션 없음 → chat 과 동일 rc
    assert app_mod.run_tui(ns()) == 0                    # 새 세션 정상 경로

    s = Session(tmp_path)
    s.add_turn("질문", "답", "read", None)
    s.save()
    assert app_mod.run_tui(ns(resume=s.id)) == 0         # 재개 경로


# --- 활동바 경과시간 3단계 색상 전환 ---

def _hex_to_rgb_str(hex_color: str) -> str:
    """ '#4EBF71' → 'rgb(78,191,113)' — Textual Static.render() 의 Text.spans
    style 문자열은 rgb(...) 형식을 쓰므로 hex 값을 변환해 비교한다."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgb({r},{g},{b})"


def test_activity_elapsed_time_colors(tmp_path):
    """_tick_activity() 가 경과시간에 따라 활동바 스타일을 3단계로 전환한다:
    <10s → success, 10~30s → warning, >30s → error."""
    async def go():
        app = _make_app(tmp_path, run_task_fn=None)
        async with app.run_test() as pilot:
            app._activity_on("테스트")
            await pilot.pause()

            success_rgb = _hex_to_rgb_str(app.theme_variables["success"])
            warning_rgb = _hex_to_rgb_str(app.theme_variables["warning"])
            error_rgb = _hex_to_rgb_str(app.theme_variables["error"])

            # 1단계: 10초 미만 → success
            app._turn_t0 = time.monotonic() - 5
            app._tick_activity()
            r = app.query_one("#activity").render()
            spans = getattr(r, "spans", [])
            assert any(success_rgb in str(s.style) for s in spans), (
                f"5초 경과 시 success({success_rgb}) 색상 span 이 있어야 함"
            )

            # 2단계: 10~30초 → warning
            app._turn_t0 = time.monotonic() - 15
            app._tick_activity()
            r = app.query_one("#activity").render()
            spans = getattr(r, "spans", [])
            assert any(warning_rgb in str(s.style) for s in spans), (
                f"15초 경과 시 warning({warning_rgb}) 색상 span 이 있어야 함"
            )

            # 3단계: 30초 초과 → error
            app._turn_t0 = time.monotonic() - 35
            app._tick_activity()
            r = app.query_one("#activity").render()
            spans = getattr(r, "spans", [])
            assert any(error_rgb in str(s.style) for s in spans), (
                f"35초 경과 시 error({error_rgb}) 색상 span 이 있어야 함"
            )
    asyncio.run(go())


# --- 완료 footer 문구: '✓ 완료 · ' 마운트 확인 ---

def test_completion_footer_text(tmp_path):
    """턴이 정상 완료되면 대화 영역에 '✓ 완료 · ' 로 시작하는 footer 가 마운트된다."""
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_token("답변")
        return _result(final="답변", route="read")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test() as pilot:
            await _submit(pilot, "질문")
            assert await _wait(pilot, _turn_done(app))
            footers = [_static_text(w) for w in app.query(".turn-footer")]
            assert any(t.startswith("✓ 완료 · ") for t in footers), (
                f"완료 footer 가 '✓ 완료 · ' 로 시작해야 함: {footers}"
            )
    asyncio.run(go())


# --- 마크다운 표 클릭 크래시 회피 (textual 8.2.8 결함) ---

def test_allow_select_disabled_and_table_click_safe(tmp_path):
    """textual 8.2.8 은 마크다운 표 재렌더 직후 셀 클릭 시 텍스트 선택 로직이 고아 위젯을
    hit-test 해 앱 전체가 죽는다. ALLOW_SELECT=False 로 그 경로를 차단한다 — 이 플래그가
    되살아나면 실사용 크래시가 재발하므로 불변식으로 고정한다."""
    assert KodaTui.ALLOW_SELECT is False

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
            await pilot.click(".cell")   # 선택 비활성 상태에서 표 클릭이 안전해야 한다
            await pilot.pause()
            assert not app.query_one("#prompt").disabled  # 앱 생존 확인
    asyncio.run(go())


# --- 타이머 정리: unmount 시 _activity_timer.stop() 호출 ---

def test_activity_timer_stopped_on_unmount(tmp_path):
    """앱이 unmount 될 때 _activity_timer.stop() 이 호출된다."""
    async def go():
        app = _make_app(tmp_path, run_task_fn=None)
        stop_calls = []
        async with app.run_test():
            app._activity_timer.stop = lambda: stop_calls.append(True)
        assert len(stop_calls) == 1, (
            f"on_unmount 시 _activity_timer.stop() 이 정확히 1회 호출되어야 함 "
            f"(실제: {len(stop_calls)}회)"
        )
    asyncio.run(go())


# --- 슬래시 커맨드 디스패치 (plan/TUI_SLASH_COMMANDS.md) ---

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
    """/clear 는 새 스레드로 진입한다(claude code /clear): 화면·세션 히스토리
    초기화 + 새 세션 id. 이전 세션은 저장되어 --resume 로 복구 가능."""
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
            conv = app.query_one("#conversation", VerticalScroll)
            assert len(conv.children) > 0
            await _submit(pilot, "/clear")
            assert await _wait(pilot, lambda: len(conv.children) == 0)
            assert app.runner.session.id != old_id               # 새 세션(스레드)
            assert app.runner.session.history == []              # 대화 맥락 초기화
            assert Session.load(tmp_path, old_id).history         # 이전 세션은 저장됨(복구 가능)

    asyncio.run(go())


# --- 활동바 툴 롤업 + 예외 엄격화 (우측 패널 폐지 → 활동바/footer/토스트) ---

def test_activity_tool_rollup(tmp_path):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_phase("read")
        on_phase("edit")
        on_phase("tool:workspace_read_file")
        on_phase("tool:workspace_read_file")
        on_phase("tool:workspace_edit_file")
        on_token("완료")
        return _result(final="완료", route="edit", verdict="pass")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test() as pilot:
            await _submit(pilot, "리팩토링")
            assert await _wait(pilot, _turn_done(app))
            # 툴 호출이 활동바 롤업 tally 로 집계된다 (read_file×2, edit_file×1)
            assert app._turn_tools["read_file"] == 2
            assert app._turn_tools["edit_file"] == 1
            assert "read_file×2" in app._rollup(app._turn_tools)   # frequency tally
            footer = "\n".join(_static_text(w) for w in app.query(".turn-footer"))
            assert "verdict: pass" in footer

    asyncio.run(go())


def test_empty_final_marks_failure_and_notifies(tmp_path):
    # 예외 엄격화: 빈 응답(length_truncation) → 턴 footer 에 사유 명시 + notify(error)
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_phase("edit")
        return _result(final="", route="edit", empty_reason="length_truncation")

    async def go():
        app = _make_app(tmp_path, fake)
        async with app.run_test() as pilot:
            await _submit(pilot, "긴 작업")
            assert await _wait(pilot, _turn_done(app))
            footer = "\n".join(_static_text(w) for w in app.query(".turn-footer"))
            assert "length_truncation" in footer                  # 사유 명시
            assert "length_truncation" in _notifs(app)            # 엄격 예외 알림

    asyncio.run(go())


def test_activity_on_resets_tool_rollup(tmp_path):
    # agy 리뷰(중대): _activity_on 이 _turn_tools 를 리셋 안 하면 새 턴 시작·/compact 에
    # 이전 턴 툴 롤업이 활동바에 잔존한다. _activity_on 이 롤업을 초기화해야 한다.
    from collections import Counter

    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test():
            app._turn_tools = Counter({"read_file": 3})
            app._activity_on("문맥 압축")
            assert app._turn_tools == Counter()                    # 롤업 초기화
            assert "read_file" not in _static_text(app.query_one("#activity"))  # 잔존 없음
    asyncio.run(go())


# --- 하단 statusline + 테마 (toad 참조: 우측 패널 폐지 후 세션 정보/테마) ---

def test_statusline_reflects_session_and_model(tmp_path):
    async def fake(task, ws, approver, on_token, on_phase, **kw):
        on_token("답")
        return _result("완료")

    async def go():
        session = Session(tmp_path)
        app = KodaTui(Workspace(tmp_path), session, Memory(tmp_path), [], auto_yes=True,
                      run_task_fn=fake, model_name="qwen-test")
        async with app.run_test() as pilot:
            line = _static_text(app.query_one("#statusline"))
            assert session.id in line and "qwen-test" in line and "0턴" in line
            assert "auto" in line                       # 승인 모드 뱃지
            await _submit(pilot, "안녕")
            assert await _wait(pilot, _turn_done(app))
            assert "1턴" in _static_text(app.query_one("#statusline"))  # 턴 후 갱신
    asyncio.run(go())


def test_theme_command_switches_and_defaults_koda_dark(tmp_path):
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test() as pilot:
            assert app.theme == "koda-dark"             # 기본 테마 = koda-dark
            assert "koda-dark" in app.available_themes
            await _submit(pilot, "/theme bogus-xyz")    # 알 수 없는 테마 → 경고, 미변경
            await pilot.pause()
            assert "알 수 없는 테마" in _notifs(app)
            assert app.theme == "koda-dark"
            await _submit(pilot, "/theme textual-dark") # 내장 테마로 전환
            await pilot.pause()
            assert app.theme == "textual-dark"
    asyncio.run(go())


# --- Phase 2: 툴 가시화 (제목 템플릿 · 콜백 핸들러 · ToolBlock 렌더) ---

def test_tool_title_templates():
    # 별도 LLM 호출 없이 도구+완성 args → 표시용 제목. 빈 args 는 도구 이름으로 폴백.
    assert tool_title("workspace_run_command", {"argv": ["pytest", "-q"]}) == "$ pytest -q"
    assert tool_title("workspace_read_file", {"path": "a.py"}) == "읽기 · a.py"
    assert tool_title("workspace_list_files", {}) == "목록 · ."
    assert tool_title("workspace_search_text", {"query": "foo"}) == "검색 · foo"
    assert tool_title("workspace_edit_file", {"path": "b.py"}) == "수정 · b.py"
    assert tool_title("explore", {}) == "탐색 위임"
    assert tool_title("workspace_read_file", {}) == "파일 읽기"      # 빈 args 폴백
    assert tool_title("some_mcp_tool", {}) == "some_mcp_tool"       # MCP/기타


def test_tool_activity_handler_posts_messages():
    # 콜백(langchain on_tool_*) → ToolStart/ToolEnd 메시지. run_id 로 매칭.
    posted: list = []
    h = ToolActivityHandler(posted.append)
    h.on_tool_start({"name": "workspace_read_file"}, "a.py", run_id="r1", inputs={"path": "a.py"})
    h.on_tool_end("파일 내용", run_id="r1")
    assert isinstance(posted[0], ToolStart) and posted[0].run_id == "r1"
    assert posted[0].title == "읽기 · a.py"
    assert isinstance(posted[1], ToolEnd) and posted[1].run_id == "r1"
    assert posted[1].output == "파일 내용" and posted[1].ok
    h.on_tool_error(RuntimeError("boom"), run_id="r2")
    assert posted[2].run_id == "r2" and not posted[2].ok and "boom" in posted[2].output


def test_tool_handler_output_trimmed():
    from koda.tui.tool_handler import _OUTPUT_LIMIT
    posted: list = []
    ToolActivityHandler(posted.append).on_tool_end("x" * (_OUTPUT_LIMIT + 500), run_id="r")
    assert len(posted[0].output) <= _OUTPUT_LIMIT + 12
    assert posted[0].output.endswith("생략)")


def test_tui_tool_blocks_mount_and_finish(tmp_path):
    # ToolStart → 대화에 ToolBlock(진행 중 ○) mount, ToolEnd → 같은 블록 finish(✓ + output).
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test() as pilot:
            app.post_message(ToolStart("r1", "workspace_read_file", "읽기 · calc.py"))
            assert await _wait(pilot, lambda: bool(app.query(ToolBlock)))
            block = app.query_one(ToolBlock)
            assert "읽기 · calc.py" in str(block.title) and "○" in str(block.title)
            app.post_message(ToolEnd("r1", "파일 12줄", ok=True))
            assert await _wait(pilot, lambda: "✓" in str(app.query_one(ToolBlock).title))
            assert app._tool_blocks == {}                 # 매칭 후 dict 에서 pop
    asyncio.run(go())


def test_tool_end_unknown_run_id_ignored(tmp_path):
    # 매칭되는 start 가 없는 ToolEnd 는 예외 없이 무시된다(.pop(id, None) 방어).
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test() as pilot:
            app.post_message(ToolEnd("no-such-id", "출력", ok=True))
            await pilot.pause()
            assert not app.query(ToolBlock)               # 유령 블록·크래시 없음
            assert not app._close_requested               # 앱 생존
    asyncio.run(go())


def test_orphan_tool_block_finished_on_turn_end(tmp_path):
    # 턴이 툴 도중 죽어 ToolEnd 가 안 오면(_end_turn) 미완 블록을 '중단됨'으로 마감한다.
    async def go():
        app = _make_app(tmp_path, None)
        async with app.run_test() as pilot:
            app.post_message(ToolStart("r1", "workspace_run_command", "$ pytest"))
            assert await _wait(pilot, lambda: bool(app.query(ToolBlock)))
            assert "○" in str(app.query_one(ToolBlock).title)   # 진행 중
            app._end_turn()                                     # 턴 종료 공통 경로
            await pilot.pause()
            block = app.query_one(ToolBlock)
            assert "✗" in str(block.title) and "중단됨" in _static_text(block._body)
            assert app._tool_blocks == {}
    asyncio.run(go())


def test_tool_output_secret_redaction():
    # read_file 로 .env 를 열거나 셸 출력에 시크릿이 섞여도 값이 스크롤백에 평문으로 안 남는다.
    posted: list = []
    ToolActivityHandler(posted.append).on_tool_end(
        "API_KEY=sk-secret123\nnormal line\nport = 8080", run_id="r")
    out = posted[0].output
    assert "sk-secret123" not in out and "[REDACTED]" in out
    assert "normal line" in out and "8080" in out   # 일반 줄·비밀 아닌 값은 보존
