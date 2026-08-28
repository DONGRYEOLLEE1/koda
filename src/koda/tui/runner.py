from __future__ import annotations

import asyncio
import threading
import time

from ..agents import remember_from_turn, run_task, summarize_history
from ..cli import _auto_approver
from ..session import Session
from .model import (
    ApprovalRequest,
    CompactDone,
    MemorySaved,
    PhaseEvent,
    SessionClosed,
    TokenChunk,
    TurnCancelled,
    TurnDone,
    TurnFailed,
)
from .tool_handler import ToolActivityHandler

APPROVAL_TIMEOUT_S = 600.0  # modal 무응답은 deny — 위험 도구가 무기한 대기하지 않는다
HEARTBEAT_S = 30.0


async def _heartbeat(task: asyncio.Task, on_phase, interval: float = HEARTBEAT_S):
    """장시간 무출력 생성 중에도 살아있음을 표시한다. task 가 취소되면 CancelledError 가 그대로 올라온다."""
    start = time.monotonic()
    while True:
        done, _ = await asyncio.wait({task}, timeout=interval)
        if done:
            return task.result()
        on_phase(f"heartbeat:{int(time.monotonic() - start)}s")


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
                 run_task_fn=None, approval_timeout: float = APPROVAL_TIMEOUT_S, settings=None):
        self._app = app
        self._ws = ws
        self.session = session
        self.memory = memory
        self.mcp_tools = mcp_tools
        self.settings = settings   # None 이면 run_task 가 .env 에서 읽는다. /model 이 이 객체를 교체한다
        self._turn_loop: asyncio.AbstractEventLoop | None = None   # 진행 중인 턴의 루프·태스크. cancel_turn 이 밖에서 끊는다
        self._turn_task: asyncio.Task | None = None
        self._run_task = run_task_fn or run_task
        self.approver = _auto_approver if auto_yes else ApprovalBridge(app.post_message, approval_timeout)

    # --- 아래 메서드들은 worker thread 진입점: run_worker(..., thread=True) 로만 호출 ---

    def cancel_turn(self) -> bool:
        """진행 중인 턴을 취소한다(UI 스레드에서 호출). 턴이 없으면 False."""
        loop, task = self._turn_loop, self._turn_task
        if loop is None or task is None:
            return False
        loop.call_soon_threadsafe(task.cancel)
        return True

    def run_turn(self, task: str) -> None:
        post = self._app.post_message
        try:
            with asyncio.Runner() as runner:
                self._turn_loop = runner.get_loop()
                self._turn_task = self._turn_loop.create_task(self._run_task(
                    task, self._ws, self.approver,
                    lambda t: post(TokenChunk(t)),
                    lambda p: post(PhaseEvent(p)),
                    history=self.session.history, mcp_tools=self.mcp_tools,
                    mcps_dir=None,  # MCP 는 세션당 1회 사전 로드 (AC 10)
                    run_id=self.session.id,  # artifact 를 세션 단위로 이어 쓴다
                    # 툴 가시화: langfuse 와 동일한 config.callbacks 채널로만 관측(실행 로직 무변경).
                    extra_callbacks=[ToolActivityHandler(post)],
                    trace_name=self.session.title,  # 첫 턴은 비어 있어 run_task 가 제목을 짓는다
                    settings=self.settings,
                ))
                result = runner.run(_heartbeat(self._turn_task, lambda p: post(PhaseEvent(p))))
        except asyncio.CancelledError:
            post(TurnCancelled())
            return
        except Exception as e:
            post(TurnFailed(f"{type(e).__name__}: {e}"))
            return
        finally:
            self._turn_loop = self._turn_task = None
        try:
            self.session.title = self.session.title or result.get("title", "")
            if not result.get("empty_final"):
                # 빈 final 은 히스토리·세션에 저장하지 않는다 — chat 과 동일한 오염 방지.
                self.session.add_turn(task, result["final"], result["route"], result.get("verdict"))
                try:  # 메모리는 턴마다 판정해 저장한다. 판정 실패가 턴을 실패시키지는 않는다.
                    for kind, note in asyncio.run(remember_from_turn(self.memory, task, result["final"], self.settings)):
                        post(MemorySaved(kind, note))
                except Exception as e:
                    post(PhaseEvent(f"메모리 판정 생략({type(e).__name__})"))
                old = self.session.old_messages() if self.session.needs_compaction else []
                if old:
                    post(PhaseEvent("문맥 압축 중…"))
                    summary = asyncio.run(summarize_history(old, self.settings))
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

    def resume(self, session_id: str) -> Session:
        """/resume: 현재 세션을 저장하고 저장된 세션으로 교체한다. 없으면 FileNotFoundError."""
        loaded = Session.load(self._ws.root, session_id)
        if self.session.history and self.session.id != session_id:
            try:
                self.session.save()
            except Exception as e:
                self._app.notify(f"이전 세션 저장 실패({type(e).__name__})", severity="warning", timeout=5)
        self.session = loaded
        return loaded

    def compact_now(self) -> None:
        """/compact: 현재 세션 이력을 요약 압축한다(최근 턴 유지). 임계값과 무관하게 수동으로 트리거한다."""
        post = self._app.post_message
        old = self.session.old_messages()
        if not old:
            post(CompactDone("압축할 오래된 이력이 없습니다 (최근 대화만 있음)."))
            return
        try:
            before = len(self.session.history)
            summary = asyncio.run(summarize_history(old, self.settings))
            self.session.apply_compaction(summary)
            self.session.save()
            after = len(self.session.history)
            post(CompactDone(f"문맥 압축 완료 — 이전 메시지 {len(old)}개를 요약으로 치환 "
                             f"(히스토리 {before}→{after})."))
        except Exception as e:
            post(CompactDone(f"압축 실패({type(e).__name__}): {e}"))

    def close_session(self) -> None:
        """/exit·Ctrl+Q: 세션 저장. 메모리는 턴마다 이미 반영됐으므로 여기서는 하지 않는다."""
        post = self._app.post_message
        saved = bool(self.session.history)
        if saved:
            try:
                self.session.save()
            except Exception as e:
                # 저장 실패로 SessionClosed 가 누락되면 앱이 종료 못 한다 — 항상 post 로 이어간다
                post(PhaseEvent(f"세션 저장 실패({type(e).__name__})"))
                saved = False
        post(SessionClosed(self.session.id, saved))
