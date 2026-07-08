"""Textual TUI 앱: 대화 스트림(좌) + 상태/검증/메모리/세션 패널(우) + 입력(하) (§5.3).

실행 코어(run_task·PermissionMiddleware·Session·Memory)는 계약 그대로 재사용한다 (§10).
worker → UI 는 post_message(thread-safe) 메시지만, UI → worker 는 ApprovalRequest.resolve
(threading.Event) 하나뿐이다 (§5.2 콜백 스레드 안전성).

진행 표시(활동바)는 toad/Claude Code 관례를 따른다: 입력창 바로 위에 스피너 + 현재 단계 +
경과 초 + (reasoning 중) 토큰 카운트. 상태 라벨은 TUI 전용 — CLI 의 _format_phase 는 불변.
"""
from __future__ import annotations

import asyncio
import time
from functools import partial

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Input, Markdown, RichLog, Static, TabbedContent, TabPane

from ..cli import _err
from ..mcp import load_mcp_tools
from ..memory import Memory
from ..session import Session
from ..workspace import Workspace
from .model import (
    ApprovalRequest,
    CompactDone,
    PhaseEvent,
    SessionClosed,
    TokenChunk,
    TurnDone,
    TurnFailed,
)
from .runner import APPROVAL_TIMEOUT_S, TuiTurnRunner
from .widgets import ApprovalModal

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # rich 'dots' 프레임 — toad/Claude Code 계열 표준 스피너

# TUI 전용 단계 라벨 (CLI 의 대괄호 라벨보다 자연스러운 동사형).
# 색상은 theme_variables 키 (self._theme_color 로 라이트/다크 적응 조회).
_PHASE_TUI = {
    "route": ("요청 분류", "accent"),
    "read": ("코드 읽는 중", "accent"),
    "plan": ("작업 계획 수립", "primary"),
    "edit": ("코드 작성", "warning"),
    "verify": ("결과 검증", "success"),
    "review": ("독립 리뷰", "success"),
}


class KodaTui(App):
    TITLE = "koda"
    # textual 8.2.8 결함 회피: 마크다운 표 렌더 직후 셀을 클릭하면 텍스트 선택 로직이
    # 고아 위젯(parent=None)을 hit-test 해 `container.region` AttributeError 로 앱 전체가
    # 죽는다(실사용 크래시). 선택 기능을 끄면 그 코드 경로 자체가 실행되지 않는다.
    # 복사는 터미널 네이티브 선택(Shift+드래그)으로 여전히 가능하다.
    ALLOW_SELECT = False
    BINDINGS = [("ctrl+q", "graceful_quit", "저장 후 종료")]
    CSS = """
    #status_strip { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    #main { height: 1fr; }
    #conversation { width: 2fr; padding: 0 1; }
    #side { width: 1fr; min-width: 28; }
    #activity { height: 1; padding: 0 1; }
    .user-turn { color: $accent; text-style: bold; margin: 1 0 0 0; }
    .turn-footer { color: $text-muted; margin: 0 0 1 0; }
    ApprovalModal { align: center middle; }
    #approval_dialog { width: 80%; max-width: 90; height: auto; border: thick $error;
                       background: $surface; padding: 1 2; }
    #approval_args { color: $text-muted; margin: 1 0; }
    #approval_buttons { height: auto; align-horizontal: center; margin-top: 1; }
    #approval_buttons Button { margin: 0 2; }
    """

    def __init__(self, ws, session, memory, mcp_tools: list, auto_yes: bool,
                 run_task_fn=None, approval_timeout: float = APPROVAL_TIMEOUT_S):
        super().__init__()
        self.runner = TuiTurnRunner(self, ws, session, memory, mcp_tools, auto_yes,
                                    run_task_fn=run_task_fn, approval_timeout=approval_timeout)
        self._busy = False
        # 이름 주의: `_closing` 은 textual MessagePump 내부 플래그다 — 덮어쓰면 이후
        # post_message 가 모든 메시지를 조용히 드롭한다(행 원인). 그 이름을 쓰지 말 것.
        self._close_requested = False
        self._draft = ""         # 현재 턴 스트림 누적 — retry 리셋·finalize 폴백 판단용
        self._stream = None      # textual MarkdownStream
        self._answer_md: Markdown | None = None
        # 활동바 상태 (모두 UI thread 전용)
        self._turn_t0 = 0.0
        self._spin_i = 0
        self._think_tok = 0          # reasoning 진행 (think:N) — 토큰 도착 시 해제
        self._activity_label = ""    # 현재 단계 라벨
        self._activity_detail = ""   # 현재 실행 중인 도구명
        # 슬래시 커맨드 레지스트리 — /help 목록·placeholder 의 단일 출처 (plan/TUI_SLASH_COMMANDS).
        self._commands: dict[str, tuple[str, object]] = {
            "/help": ("사용 가능한 명령 목록", self._cmd_help),
            "/compact": ("대화 이력을 요약으로 압축", self._cmd_compact),
            "/clear": ("대화 화면 비우기(이력은 보존)", self._cmd_clear),
            "/memory": ("메모리 패널 보기", self._cmd_memory),
            "/remember": ("메모리에 저장: /remember <내용>", self._cmd_remember),
            "/exit": ("저장 후 종료", self._cmd_exit),
            "/quit": ("저장 후 종료", self._cmd_exit),
        }

    # --- 레이아웃 ---

    def compose(self) -> ComposeResult:
        yield Static(self._strip_text("대기 중"), id="status_strip", markup=False)
        with Horizontal(id="main"):
            yield VerticalScroll(id="conversation")
            with TabbedContent(initial="tab_status", id="side"):
                with TabPane("상태", id="tab_status"):
                    yield RichLog(id="event_log", wrap=False, markup=False)
                with TabPane("검증", id="tab_verify"):
                    yield Static("(검증 결과 없음)", id="verify_panel", markup=False)
                with TabPane("메모리", id="tab_memory"):
                    yield Static(self.runner.memory.read() or "(비어 있음)", id="memory_panel", markup=False)
                with TabPane("세션", id="tab_session"):
                    yield Static(self._session_text(len(self.runner.session.turns)), id="session_panel", markup=False)
        yield Static("", id="activity", markup=False)
        yield Input(placeholder="요청 입력…  (/help 로 명령 목록 · Ctrl+Q 종료)", id="prompt")

    def on_mount(self) -> None:
        self.query_one("#activity", Static).display = False
        self._activity_timer = self.set_interval(0.125, self._tick_activity, pause=True)
        self.query_one("#prompt", Input).focus()

    def _strip_text(self, status: str) -> str:
        return f"koda · 세션 {self.runner.session.id} · {status}"

    def _session_text(self, turns: int) -> str:
        s = self.runner.session
        return (f"세션 id: {s.id}\n턴 수: {turns}\n히스토리 메시지: {len(s.history)}\n\n"
                f"재개: koda tui --resume {s.id}")

    def _theme_color(self, name: str) -> str:
        """테마 색상 hex 조회. 라이트/다크 자동 적응."""
        return self.theme_variables.get(name, name)

    def _set_status(self, status: str) -> None:
        self.query_one("#status_strip", Static).update(self._strip_text(status))

    # --- 활동바: 스피너 + 단계 + 경과 초 (+ reasoning 토큰) ---

    def _activity_on(self, label: str) -> None:
        self._turn_t0 = time.monotonic()
        self._spin_i = 0
        self._think_tok = 0
        self._activity_label = label
        self._activity_detail = ""
        self.query_one("#activity", Static).display = True
        self._tick_activity()
        self._activity_timer.resume()

    def _activity_off(self) -> None:
        self._activity_timer.pause()
        self.query_one("#activity", Static).display = False

    def _tick_activity(self) -> None:
        self._spin_i = (self._spin_i + 1) % len(_SPINNER)
        elapsed = int(time.monotonic() - self._turn_t0)
        # 경과시간 3단계 색상 — 로컬 LLM 추론이 원래 느릴 수 있으므로,
        # 이는 실패 신호가 아니라 '오래 걸리고 있다'는 표시다.
        if elapsed < 10:
            time_color = self._theme_color("success")
        elif elapsed <= 30:
            time_color = self._theme_color("warning")
        else:
            time_color = self._theme_color("error")
        t = Text()
        accent = self._theme_color("accent")
        t.append(_SPINNER[self._spin_i] + " ", style=f"bold {accent}")
        t.append(self._activity_label or "작업 중", style="bold")
        if self._activity_detail:
            t.append(f" · {self._activity_detail}", style="dim")
        t.append(f" · {elapsed}s", style=f"bold {time_color}")
        if self._think_tok:
            t.append(f" · 생각 중 ~{self._think_tok} tok", style="dim italic")
        self.query_one("#activity", Static).update(t)

    def on_unmount(self) -> None:
        self._activity_timer.stop()

    # --- 이벤트 로그 (우측 '상태' 패널) ---

    def _log(self, content: Text | str, elapsed: bool = True) -> None:
        line = Text()
        if elapsed and (self._busy or self._close_requested):
            line.append(f"{int(time.monotonic() - self._turn_t0):>3}s ", style="dim")
        if isinstance(content, str):
            line.append(content)
        else:
            line.append_text(content)
        self.query_one("#event_log", RichLog).write(line)

    # --- 입력/명령 ---

    @on(Input.Submitted, "#prompt")
    async def _on_submit(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        if not line or self._busy or self._close_requested:
            return
        event.input.clear()
        if line.startswith("/"):          # 슬래시 커맨드 → 레지스트리 디스패치
            name, _, arg = line.partition(" ")
            entry = self._commands.get(name)
            if entry is None:
                self._log(f"알 수 없는 명령: {name} — /help 로 목록 확인", elapsed=False)
            else:
                await entry[1](arg.strip())
            return
        await self._start_turn(line)

    # --- 슬래시 커맨드 핸들러 (레지스트리 self._commands 에서 디스패치) ---

    async def _cmd_help(self, arg: str) -> None:
        seen, lines = set(), ["사용 가능한 명령:"]
        for name, (summary, fn) in self._commands.items():
            if fn in seen:               # /exit·/quit 처럼 같은 핸들러는 한 번만
                continue
            seen.add(fn)
            lines.append(f"  {name}  — {summary}")
        self._log("\n".join(lines), elapsed=False)

    async def _cmd_clear(self, arg: str) -> None:
        await self.query_one("#conversation", VerticalScroll).remove_children()
        self._log("대화 화면을 지웠습니다 (이력은 보존됨).", elapsed=False)

    async def _cmd_memory(self, arg: str) -> None:
        self.query_one("#memory_panel", Static).update(self.runner.memory.read() or "(비어 있음)")
        self.query_one(TabbedContent).active = "tab_memory"

    async def _cmd_remember(self, arg: str) -> None:
        saved = self.runner.memory.remember(arg)
        self.query_one("#memory_panel", Static).update(self.runner.memory.read() or "(비어 있음)")
        self._log("기억함." if saved else "이미 기억하고 있거나 내용이 비어 있음.", elapsed=False)

    async def _cmd_exit(self, arg: str) -> None:
        self._begin_close()

    async def _cmd_compact(self, arg: str) -> None:
        # 압축은 LLM 요약 호출 → 턴과 동일하게 worker thread 로. busy 가드는 _on_submit 이 이미 함.
        self._busy = True
        self.query_one("#prompt", Input).disabled = True
        self._set_status("압축 중")
        self._activity_on("문맥 압축")
        self.run_worker(self.runner.compact_now, thread=True, exclusive=True, group="turn")

    async def _start_turn(self, task: str) -> None:
        self._busy = True
        self.query_one("#prompt", Input).disabled = True
        self._draft = ""
        conv = self.query_one("#conversation", VerticalScroll)
        self._answer_md = Markdown("", classes="answer")
        await conv.mount(Static(f"» {task}", classes="user-turn", markup=False), self._answer_md)
        conv.anchor()
        self._stream = Markdown.get_stream(self._answer_md)
        self._set_status("작업 중")
        self._activity_on("시작")
        log = self.query_one("#event_log", RichLog)
        log.write(Text(f"── {task[:32]}{'…' if len(task) > 32 else ''} ", style="dim"))
        self.run_worker(partial(self.runner.run_turn, task), thread=True,
                        exclusive=True, group="turn")

    def _end_turn(self, status: str = "대기 중") -> None:
        self._busy = False
        self._answer_md = None
        self._activity_off()
        self._set_status(status)
        if isinstance(self.screen, ApprovalModal):
            # approver timeout(=deny) 으로 응답 없이 턴이 끝난 고아 modal 정리 —
            # dismiss(False) 의 늦은 resolve 는 이미 반환된 worker 에 no-op 이다
            self.screen.dismiss(False)
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = False
        prompt.focus()

    # --- worker 메시지 핸들러 ---

    @on(TokenChunk)
    async def _on_token(self, msg: TokenChunk) -> None:
        self._draft += msg.text
        self._think_tok = 0  # 본문 생성 시작 → reasoning 카운터 해제
        if self._stream is not None:
            await self._stream.write(msg.text)

    async def _stop_stream(self) -> None:
        if self._stream is None:
            return
        try:
            await self._stream.stop()
        except asyncio.CancelledError:
            pass  # 스트림 태스크가 기동 전에 취소된 극단 케이스 방어 — 핸들러 전파 시 앱이 죽는다
        self._stream = None

    @on(PhaseEvent)
    async def _on_phase(self, msg: PhaseEvent) -> None:
        phase = msg.phase
        if phase.startswith("think:"):
            n = phase[6:]
            self._think_tok = int(n) if n.isdigit() else 0  # 활동바 전용 (§5.4)
            return
        if phase.startswith("heartbeat:"):
            return  # 활동바 경과 초가 대체 — 로그·표시 불필요
        if phase.startswith("retry:"):
            await self._reset_draft()  # 1차 시도 부분 스트림 폐기 (renderer 회귀 불변식과 동일)
            self._log(Text(f"↻ 빈 응답 — 변형 재시도 ({phase[6:]})", style=self._theme_color("warning")))
            self._activity_label = "재시도"
            return
        if phase in _PHASE_TUI:
            label, theme_key = _PHASE_TUI[phase]
            color = self._theme_color(theme_key)
            self._activity_label = label
            self._activity_detail = ""
            self._log(Text.assemble(("▸ ", color), (label, f"bold {color}")))
            return
        if phase.startswith("tool:"):
            name = phase[5:].removeprefix("workspace_")
            self._activity_detail = name
            self._log(Text(f"  ⚙ {name}", style="dim"))
            return
        if phase.startswith("mcp:"):
            self._log(Text(f"· {phase[4:]}", style="dim"), elapsed=False)
            return
        # 자유 형식 (문맥 압축 중…, 메모리 갱신 중… 등)
        self._activity_label = phase
        self._log(Text(f"· {phase}", style="dim"))

    async def _reset_draft(self) -> None:
        self._draft = ""
        await self._stop_stream()
        if self._answer_md is not None:
            await self._answer_md.update("")
            self._stream = Markdown.get_stream(self._answer_md)

    @on(TurnDone)
    async def _on_turn_done(self, msg: TurnDone) -> None:
        result = msg.result
        final = (result.get("final") or "").strip()
        await self._stop_stream()
        if self._answer_md is not None and final and final != self._draft.strip():
            await self._answer_md.update(final)  # 스트림 유실·재시도 폴백: 정본으로 교체

        verdict = result.get("verdict")
        elapsed = int(time.monotonic() - self._turn_t0)
        footer = Text()
        footer.append(f"✓ 완료 · {elapsed}초", style=f"bold {self._theme_color('success')}")
        footer.append(f"\nroute: {result.get('route')}", style="dim")
        if verdict:
            footer.append("  ·  ", style="dim")
            verdict_color = self._theme_color("success") if verdict == "pass" else self._theme_color("error")
            footer.append(f"verdict: {verdict}",
                          style=f"bold {verdict_color}")
        if result.get("trace_url"):
            footer.append(f"\ntrace: {result['trace_url']}", style="dim")
        if result.get("empty_final"):
            footer.append(f"\n빈 응답 — 재시도 권장 (사유: {result.get('empty_reason') or 'unknown'})"
                          " · 히스토리에 저장하지 않음", style=self._theme_color("warning"))
        conv = self.query_one("#conversation", VerticalScroll)
        await conv.mount(Static(footer, classes="turn-footer"))
        conv.anchor()

        vtext = Text()
        v_style = f"bold {self._theme_color('success')}" if verdict == "pass" else (f"bold {self._theme_color('error')}" if verdict else "")
        vtext.append(f"verdict: {verdict or '(없음)'}\n\n", style=v_style)
        vtext.append(result.get("verify_detail") or "(판정 근거 없음)")
        self.query_one("#verify_panel", Static).update(vtext)
        self.query_one("#session_panel", Static).update(self._session_text(msg.turns))
        self._end_turn()

    @on(TurnFailed)
    async def _on_turn_failed(self, msg: TurnFailed) -> None:
        await self._stop_stream()
        conv = self.query_one("#conversation", VerticalScroll)
        err_color = f"bold {self._theme_color('error')}"
        await conv.mount(Static(Text(f"턴 실패: {msg.error}", style=err_color), classes="turn-footer"))
        conv.anchor()
        self._log(Text(f"✗ 턴 실패: {msg.error}", style=err_color))
        self._end_turn()

    @on(CompactDone)
    async def _on_compact_done(self, msg: CompactDone) -> None:
        # /compact worker 완료 — busy 해제·입력 재활성(턴과 동일 경로) 후 결과 안내
        self._end_turn()
        self._log(msg.note, elapsed=False)

    @on(ApprovalRequest)
    def _on_approval(self, msg: ApprovalRequest) -> None:
        self._log(Text(f"⚠ 승인 대기 · {msg.tool.removeprefix('workspace_')}",
                        style=f"bold {self._theme_color('error')}"))
        self._activity_detail = "승인 대기"
        # modal 닫힘(ESC)·거부·승인 결과가 resolve 로 worker 의 threading.Event 를 깨운다
        self.push_screen(ApprovalModal(msg.tool, msg.args), lambda ok: msg.resolve(bool(ok)))

    # --- 종료 ---

    def action_graceful_quit(self) -> None:
        self._begin_close()

    def _begin_close(self) -> None:
        if self._close_requested:
            return
        if self._busy:
            self.notify("턴 실행 중 — 완료 후 종료할 수 있습니다.", severity="warning")
            return
        self._close_requested = True
        self.query_one("#prompt", Input).disabled = True
        self._set_status("세션 저장 중")
        self._activity_on("세션 저장·메모리 갱신")
        self.run_worker(self.runner.close_session, thread=True, exclusive=True, group="turn")

    @on(SessionClosed)
    def _on_session_closed(self, msg: SessionClosed) -> None:
        self.exit(msg.saved)


def run_tui(args) -> int:
    """`koda tui` 진입점. 세션/메모리/MCP 준비는 chat(_cmd_chat)과 동일 흐름."""
    ws = Workspace(args.workspace)
    memory = Memory(ws.root)

    if args.resume:
        try:
            session = Session.load(ws.root, args.resume)
            _err(f"세션 재개: {session.id} (턴 {len(session.turns)}개 로드)")
        except FileNotFoundError:
            _err(f"세션 없음: {args.resume} (목록: {', '.join(Session.list_ids(ws.root)) or '없음'})")
            return 2
    else:
        session = Session(ws.root)

    # MCP 는 세션당 1회 로드 (앱 시작 전 — 툴 목록 안정 = 프롬프트 캐시 prefix 안정, AC 10)
    mcp_tools: list = []
    if not args.no_mcp:
        try:
            mcp_tools = asyncio.run(load_mcp_tools(args.mcps_dir, on_log=lambda m: _err(f"  · {m}")))
        except Exception as e:
            _err(f"  · MCP 로드 실패({type(e).__name__}) — 로컬 도구만 사용")

    app = KodaTui(ws, session, memory, mcp_tools, auto_yes=args.yes)
    app.run()
    if session.history:
        _err(f"세션 저장됨: {session.id}  (재개: koda tui --resume {session.id})")
    return 0
