from __future__ import annotations

import asyncio
import threading

from ..agents import compact_memory, extract_memories, run_task, summarize_history
from ..cli import _auto_approver, _run_with_heartbeat
from ..session import Session
from .model import (
    ApprovalRequest,
    CompactDone,
    PhaseEvent,
    SessionClosed,
    TokenChunk,
    TurnDone,
    TurnFailed,
)
from .tool_handler import ToolActivityHandler

APPROVAL_TIMEOUT_S = 600.0  # modal 무응답은 deny — 위험 도구가 무기한 대기하지 않는다


class ApprovalBridge:
    """동기 approver 구현체. .auto 속성이 없어 denygate confirm 등급을 통과 가능한 대화형 승인으로 취급된다."""

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
                    # 툴 가시화: langfuse 와 동일한 config.callbacks 채널로만 관측(실행 로직 무변경).
                    extra_callbacks=[ToolActivityHandler(post)],
                ),
                lambda p: post(PhaseEvent(p)),
            ))
        except Exception as e:
            post(TurnFailed(f"{type(e).__name__}: {e}"))
            return
        try:
            if not result.get("empty_final"):
                # 빈 final 은 히스토리·세션에 저장하지 않는다 — chat 과 동일한 오염 방지.
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

    def new_thread(self) -> str:
        """/clear: 현재 세션을 저장하고 새 세션으로 컨텍스트를 초기화한다. 이전 세션은 --resume 로 복구 가능, 영구 메모리는 유지된다."""
        if self.session.history:
            try:
                self.session.save()
            except Exception as e:
                # 저장 실패해도 새 스레드 진입은 막지 않되 조용히 삼키지 않고 알린다(close_session 과 일관).
                self._app.notify(f"이전 세션 저장 실패({type(e).__name__})", severity="warning", timeout=5)
        self.session = Session(self._ws.root)
        return self.session.id

    def compact_now(self) -> None:
        """/compact: 현재 세션 이력을 요약 압축한다(최근 턴 유지). 임계값과 무관하게 수동으로 트리거한다."""
        post = self._app.post_message
        old = self.session.old_messages()
        if not old:
            post(CompactDone("압축할 오래된 이력이 없습니다 (최근 대화만 있음)."))
            return
        try:
            before = len(self.session.history)
            summary = asyncio.run(summarize_history(old))
            self.session.apply_compaction(summary)
            self.session.save()
            after = len(self.session.history)
            post(CompactDone(f"문맥 압축 완료 — 이전 메시지 {len(old)}개를 요약으로 치환 "
                             f"(히스토리 {before}→{after})."))
        except Exception as e:
            post(CompactDone(f"압축 실패({type(e).__name__}): {e}"))

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
