"""runner: run_task 를 worker thread 에서 실행하고 콜백을 Textual 메시지로 변환 (§5.2).

- Textual 앱은 자체 이벤트 루프를 가지므로 턴 실행은 CLI 와 동일하게 별도 thread 에서
  `asyncio.run(run_task(...))` 으로 돈다 — 실행 코어 계약 무변경 (§10).
- worker → UI 는 post_message(thread-safe)만 사용한다. 위젯 직접 접근 금지.
- approver 는 PermissionMiddleware 의 동기 bool 계약 그대로: worker 를 threading.Event
  로 재우고 UI modal 응답(resolve)으로 깨운다. timeout 은 deny (§Phase 4.5).
- Session/Memory 는 runner(worker 쪽)가 소유한다 — UI 는 메시지로 받은 사본만 표시.
"""
from __future__ import annotations

import asyncio
import threading

from ..agents import compact_memory, extract_memories, run_task, summarize_history
from ..cli import _auto_approver, _run_with_heartbeat
from .model import ApprovalRequest, PhaseEvent, SessionClosed, TokenChunk, TurnDone, TurnFailed

APPROVAL_TIMEOUT_S = 600.0  # modal 무응답은 deny — 위험 도구가 무기한 대기하지 않는다


class ApprovalBridge:
    """동기 approver 구현체. `.auto` 속성이 없으므로 denygate confirm 등급을 통과할 수 있는
    대화형 승인으로 취급된다 (AC 8)."""

    def __init__(self, post, timeout: float = APPROVAL_TIMEOUT_S):
        self._post = post  # app.post_message (thread-safe)
        self._timeout = timeout

    def __call__(self, name, args) -> bool:
        done = threading.Event()
        verdict = {"ok": False}

        def resolve(ok: bool) -> None:
            verdict["ok"] = bool(ok)
            done.set()

        self._post(ApprovalRequest(name, args, resolve))
        if not done.wait(self._timeout):
            return False
        return verdict["ok"]


class TuiTurnRunner:
    """턴 파이프라인(실행 → 히스토리 반영 → 압축 → 저장)을 chat(_cmd_chat)과 동등하게 수행."""

    def __init__(self, app, ws, session, memory, mcp_tools: list, auto_yes: bool,
                 run_task_fn=None, approval_timeout: float = APPROVAL_TIMEOUT_S):
        self._app = app
        self._ws = ws
        self.session = session
        self.memory = memory
        self.mcp_tools = mcp_tools
        self._run_task = run_task_fn or run_task
        self.approver = _auto_approver if auto_yes else ApprovalBridge(app.post_message, approval_timeout)

    # --- 아래 메서드들은 worker thread 진입점: run_worker(..., thread=True) 로만 호출 ---

    def run_turn(self, task: str) -> None:
        post = self._app.post_message
        try:
            result = asyncio.run(_run_with_heartbeat(
                self._run_task(
                    task, self._ws, self.approver,
                    lambda t: post(TokenChunk(t)),
                    lambda p: post(PhaseEvent(p)),
                    history=self.session.history, mcp_tools=self.mcp_tools,
                    mcps_dir=None,  # MCP 는 세션당 1회 사전 로드 (AC 10)
                    run_id=self.session.id,  # artifact 를 세션 단위로 이어 쓴다
                ),
                lambda p: post(PhaseEvent(p)),
            ))
        except Exception as e:
            post(TurnFailed(f"{type(e).__name__}: {e}"))
            return
        try:
            if not result.get("empty_final"):
                # 빈 final 은 히스토리·세션에 저장하지 않는다 — chat 과 동일한 오염 방지 (AC 9)
                self.session.add_turn(task, result["final"], result["route"], result.get("verdict"))
                old = self.session.old_messages() if self.session.needs_compaction else []
                if old:
                    post(PhaseEvent("문맥 압축 중…"))
                    summary = asyncio.run(summarize_history(old))
                    self.session.apply_compaction(summary)
                self.session.save()
        except Exception as e:
            # 예외가 thread worker 를 뚫으면 exit_on_error 로 앱이 통째로 죽는다 — 턴 실패로 강등
            post(TurnFailed(f"세션 반영 실패 {type(e).__name__}: {e}"))
            return
        post(TurnDone(result, turns=len(self.session.turns)))

    def close_session(self) -> None:
        """/exit·Ctrl+Q: 세션 저장 + 영구 메모리 추출 — chat 종료 흐름과 동등."""
        post = self._app.post_message
        saved = bool(self.session.history)
        if saved:
            try:
                self.session.save()
            except Exception as e:
                # 저장 실패로 SessionClosed 가 누락되면 앱이 종료 못 한다 — 항상 post 로 이어간다
                post(PhaseEvent(f"세션 저장 실패({type(e).__name__})"))
                saved = False
            try:
                post(PhaseEvent("메모리 갱신 중…"))
                for note in asyncio.run(extract_memories(self.session.history)):
                    note = note.removeprefix("- ").strip()
                    if len(note) > 200:  # 형식 위반(장문)은 영구 저장하지 않는다
                        continue
                    if self.memory.remember(note):
                        post(PhaseEvent(f"+ 기억함: {note[:60]}"))
                if self.memory.needs_compaction:
                    post(PhaseEvent("메모리 압축 중…"))
                    compacted = asyncio.run(compact_memory(self.memory.read()))
                    if compacted:
                        self.memory.replace(compacted)
            except Exception as e:
                post(PhaseEvent(f"메모리 추출 생략({type(e).__name__})"))
        post(SessionClosed(self.session.id, saved))
