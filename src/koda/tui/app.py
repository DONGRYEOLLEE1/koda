from __future__ import annotations

import asyncio
import time
from collections import Counter
from functools import partial

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.theme import Theme
from textual.widgets import Footer, Markdown, Static

from ..cli import _err
from ..mcp import load_mcp_tools, mcp_dirs
from ..memory import Memory, parse_remember
from ..session import Session
from ..workspace import Workspace
from .model import (
    ApprovalRequest,
    CompactDone,
    MemorySaved,
    PhaseEvent,
    ReasoningEnd,
    SessionClosed,
    TokenChunk,
    ToolEnd,
    ToolStart,
    TurnDone,
    TurnFailed,
)
from .runner import APPROVAL_TIMEOUT_S, TuiTurnRunner
from .widgets import ApprovalModal, ExploreStep, ProcessGroup, PromptArea, ReasoningStep, SessionPicker, StepLine

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # rich 'dots' 프레임 — toad/Claude Code 계열 표준 스피너

# TUI 전용 단계 라벨 (CLI 의 대괄호 라벨보다 자연스러운 동사형).
# 색상은 theme_variables 키 (self._theme_color 로 라이트/다크 적응 조회).
_PHASE_TUI = {
    "route": ("요청 분류", "accent"),
    "answer": ("답변 작성", "accent"),
    "read": ("코드 읽는 중", "accent"),
    "plan": ("작업 계획 수립", "primary"),
    "edit": ("코드 작성", "warning"),
    "verify": ("결과 검증", "success"),
    "review": ("독립 리뷰", "success"),
}

# koda 브랜드 다크 테마 (toad 참조: 다크 네이비 배경 · 보라 accent · 민트 그린 성공 · 골드 · 로즈).
# 색은 Textual 시맨틱 토큰으로만 참조한다(_theme_color) — /theme 로 내장 테마 교체 가능.
KODA_DARK = Theme(
    name="koda-dark",
    primary="#7c74f4",     # 보라 — 브랜딩·강조
    secondary="#bb9af7",   # 밝은 보라
    accent="#bb9af7",      # accent 는 아껴 쓴다 (toad 원칙)
    success="#7ee787",     # 민트 그린 — 성공·셸
    warning="#e0af68",     # 골드
    error="#f7768e",       # 로즈 — 경고/실패
    foreground="#c0caf5",
    background="#191a24",   # 다크 네이비
    surface="#22242f",
    panel="#2a2d3e",
    dark=True,
)


class KodaTui(App):
    TITLE = "koda"
    # 텍스트 선택은 켜 둔다. 드래그로 고른 뒤 놓으면 클립보드(OSC 52)로 복사되고, Ctrl+C 도 선택이 있으면 복사다.
    # 과거 표 셀 클릭 크래시(textual 8.2.8) 회피용으로 꺼 두었으나 스트리밍 재렌더 중 클릭·드래그 반복으로도 재현되지 않아 되살렸다.
    BINDINGS = [
        ("ctrl+q", "graceful_quit", "종료"),
        # Ctrl+C 도 정상 종료. 입력창(TextArea) focus 시 기본은 copy 라 priority 로 그것을 이겨 종료로 만든다(IDE 가 Ctrl+Q 를 가로채는 환경 대비).
        Binding("ctrl+c", "graceful_quit", "종료", priority=True, show=False),
    ]
    CSS = """
    #conversation { width: 1fr; height: 1fr; padding: 0 1; }
    #prompt { height: auto; max-height: 10; }
    #activity { height: 1; padding: 0 1; }
    #statusline { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    .user-turn { color: $accent; text-style: bold; margin: 1 0 0 0; }
    .turn-footer { color: $text-muted; margin: 0 0 1 0; }
    ApprovalModal { align: center middle; }
    #approval_dialog { width: 80%; max-width: 90; height: auto; border: thick $error;
                       background: $surface; padding: 1 2; }
    #approval_args { color: $text-muted; margin: 1 0; }
    #approval_buttons { height: auto; align-horizontal: center; margin-top: 1; }
    #approval_buttons Button { margin: 0 2; }
    SessionPicker { align: center middle; }
    #session_dialog { width: 90%; max-width: 110; height: auto; max-height: 80%; border: thick $primary;
                      background: $surface; padding: 1 2; }
    #session_title { margin-bottom: 1; }
    ProcessGroup, StepLine { border: none; background: transparent; padding: 0; margin: 0; }
    ProcessGroup CollapsibleTitle, StepLine CollapsibleTitle { color: $text-muted; padding: 0; }
    ProcessGroup > Contents { padding: 0 0 0 2; }
    StepLine > Contents { padding: 0 0 0 2; }
    StepLine Static { color: $text-muted; }
    StepLine.-run CollapsibleTitle { color: $text-disabled; }
    StepLine.-fail CollapsibleTitle { color: $error; }
    ReasoningStep CollapsibleTitle { text-style: italic; }
    """

    def __init__(self, ws, session, memory, mcp_tools: list, auto_yes: bool,
                 run_task_fn=None, approval_timeout: float = APPROVAL_TIMEOUT_S,
                 model_name: str = ""):
        super().__init__()
        self.runner = TuiTurnRunner(self, ws, session, memory, mcp_tools, auto_yes,
                                    run_task_fn=run_task_fn, approval_timeout=approval_timeout)
        self._model_name = model_name
        self._mode = "auto" if auto_yes else "manual"  # 승인 모드 뱃지(statusline)
        self._busy = False
        # 이름 주의: _closing 은 textual MessagePump 내부 플래그라 덮어쓰면 post_message 가
        # 조용히 드롭된다 — 쓰지 말 것.
        self._close_requested = False
        self._draft = ""         # 현재 턴 스트림 누적 — retry 리셋·finalize 폴백 판단용
        # 세그먼트 스트림: 산문을 툴 블록과 시간순 교차되도록 여러 Markdown 세그먼트로 쪼갠다.
        self._cur_prose_stream = None
        self._cur_prose_md = None        # 현재 산문 세그먼트 위젯. 열려 있는 동안 도착한 사고는 그 앞 그룹으로 간다
        self._turn_widgets: list = []   # 이번 턴 mount 위젯(질의 Static 제외) — retry 폐기용
        # 과정 그룹: 연속 스텝(사고·툴) 구간 하나가 ProcessGroup 하나. 본문 토큰이 오면 구간이 끊긴다.
        self._cur_group: ProcessGroup | None = None
        self._prose_group: ProcessGroup | None = None   # 현재 산문 세그먼트 바로 앞의 그룹
        self._cur_explore: ExploreStep | None = None    # 살아 있는 탐색 병합 줄
        self._thinking_visible = True
        # 활동바 상태 (모두 UI thread 전용)
        self._turn_t0 = 0.0
        self._spin_i = 0
        self._think_tok = 0          # reasoning 진행 (think:N) — 토큰 도착 시 해제
        self._activity_label = ""    # 현재 단계 라벨
        self._activity_detail = ""   # 현재 실행 중인 도구명
        self._turn_tools: Counter = Counter()    # 턴 전체 툴 tally(활동바 롤업)
        self._tool_steps: dict = {}  # run_id → StepLine (ToolStart↔ToolEnd 매칭)
        # 슬래시 커맨드 레지스트리 — /help 목록·placeholder 의 단일 출처.
        self._commands: dict[str, tuple[str, object]] = {
            "/help": ("사용 가능한 명령 목록", self._cmd_help),
            "/compact": ("대화 이력을 요약으로 압축", self._cmd_compact),
            "/clear": ("새 스레드 시작(대화 맥락 초기화)", self._cmd_clear),
            "/resume": ("저장된 세션 재개: /resume [session-id] (인자 없으면 목록에서 선택)", self._cmd_resume),
            "/memory": ("메모리 내용 보기", self._cmd_memory),
            "/remember": ("메모리에 저장: /remember [semantic|episodic|procedural] <내용>", self._cmd_remember),
            "/theme": ("테마 변경: /theme <이름> (인자 없으면 목록)", self._cmd_theme),
            "/thinking": ("사고 줄 표시 전환: /thinking [show|hide]", self._cmd_thinking),
            "/exit": ("저장 후 종료", self._cmd_exit),
            "/quit": ("저장 후 종료", self._cmd_exit),
        }

    # --- 레이아웃 ---

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="conversation")
        yield Static("", id="activity", markup=False)
        yield Static(self._statusline_text(), id="statusline", markup=False)
        yield PromptArea(id="prompt", tab_behavior="focus",
                         placeholder="요청 입력…  (Alt+Enter·Ctrl+J 줄바꿈 · /help 명령 · Ctrl+Q 종료)")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(KODA_DARK)
        self.theme = "koda-dark"
        self.query_one("#activity", Static).display = False
        self._activity_timer = self.set_interval(0.125, self._tick_activity, pause=True)
        self.query_one("#prompt", PromptArea).focus()

    def get_system_commands(self, screen):
        # maximize 는 한 위젯으로 화면을 덮어 대화·입력·상태줄을 가린다 — koda 는 이 넷이 한 화면에
        # 공존하는 단일 뷰라, 어떤 위젯(입력창·툴 블록 등)을 최대화해도 답변이 안 보이는 깨진 상태가
        # 된다. 그래서 커맨드 팔레트에서 maximize 명령을 통째로 뺀다(#21). maximize 의 유일한 트리거다.
        for command in super().get_system_commands(screen):
            if command.callback == screen.action_maximize:
                continue
            yield command

    # --- statusline: 세션 · 모델 · 턴 · 모드 · 상태 (toad 하단 statusline) ---

    def _statusline_text(self, status: str = "대기 중") -> str:
        s = self.runner.session
        parts = [f"koda · {s.id}"]
        if self._model_name:
            parts.append(self._model_name)
        parts.append(f"{len(s.turns)}턴")
        parts.append(self._mode)
        parts.append(status)
        return " · ".join(parts)

    def _set_status(self, status: str) -> None:
        self.query_one("#statusline", Static).update(self._statusline_text(status))

    def _theme_color(self, name: str) -> str:
        """테마 색상 hex 조회. 라이트/다크 자동 적응."""
        return self.theme_variables.get(name, name)

    # --- 활동바: 스피너 + 단계 + 경과 초 (+ reasoning 토큰) ---

    def _activity_on(self, label: str) -> None:
        self._turn_t0 = time.monotonic()
        self._spin_i = 0
        self._think_tok = 0
        self._activity_label = label
        self._activity_detail = ""
        self._turn_tools = Counter()   # 롤업 초기화 — 새 턴 시작 깜빡임·/compact 잔존 방지
        self.query_one("#activity", Static).display = True
        self._tick_activity()
        self._activity_timer.resume()

    def _activity_off(self) -> None:
        self._activity_timer.pause()
        self.query_one("#activity", Static).display = False

    @staticmethod
    def _fmt_elapsed(secs: int) -> str:
        """Codex CLI 스타일 경과시간: 47s / 4m 07s / 1h 02m 09s."""
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60:02d}s"
        return f"{secs // 3600}h {secs % 3600 // 60:02d}m {secs % 60:02d}s"

    def _tick_activity(self) -> None:
        self._spin_i = (self._spin_i + 1) % len(_SPINNER)
        elapsed = int(time.monotonic() - self._turn_t0)
        # 경과시간 3단계 색상 — 로컬 LLM 추론이 원래 느릴 수 있으므로 실패 신호가 아니라 '오래 걸리고 있다'는 표시다.
        if elapsed < 10:
            time_color = self._theme_color("success")
        elif elapsed <= 30:
            time_color = self._theme_color("warning")
        else:
            time_color = self._theme_color("error")
        t = Text()
        accent = self._theme_color("accent")
        t.append(self._spin() + " ", style=f"bold {accent}")
        t.append(self._activity_label or "작업 중", style="bold")
        rollup = self._rollup(self._turn_tools).lstrip(" ·").strip()
        if rollup:                                   # 턴 툴 롤업 우선 (read×3 edit×2)
            t.append(f" · {rollup}", style="dim")
        elif self._activity_detail:
            t.append(f" · {self._activity_detail}", style="dim")
        t.append(f" · {self._fmt_elapsed(elapsed)}", style=f"bold {time_color}")
        if self._think_tok:
            t.append(f" · 생각 중 ~{self._think_tok} tok", style="dim italic")
        self.query_one("#activity", Static).update(t)

    def on_unmount(self) -> None:
        self._activity_timer.stop()

    def _rollup(self, tools: Counter) -> str:
        """툴 호출 tally 를 'read×3 edit×2' 로 압축 (claude-statusbar frequency tally)."""
        if not tools:
            return ""
        return " · " + " ".join(f"{n}×{c}" if c > 1 else n for n, c in tools.items())

    def _spin(self) -> str:
        return _SPINNER[self._spin_i]

    # --- 입력/명령 ---

    @on(PromptArea.Submitted)
    async def _on_submit(self, event: PromptArea.Submitted) -> None:
        line = event.value.strip()
        if not line or self._busy or self._close_requested:
            return
        self.query_one("#prompt", PromptArea).clear()
        if line.startswith("/"):          # 슬래시 커맨드 → 레지스트리 디스패치
            name, _, arg = line.partition(" ")
            entry = self._commands.get(name)
            if entry is None:
                self.notify(f"알 수 없는 명령: {name} — /help 로 목록 확인", severity="warning", timeout=4)
            else:
                await entry[1](arg.strip())
            return
        await self._start_turn(line)

    # --- 슬래시 커맨드 핸들러 (레지스트리 self._commands 에서 디스패치) ---

    async def _cmd_help(self, arg: str) -> None:
        seen, lines = set(), []
        for name, (summary, fn) in self._commands.items():
            if fn in seen:               # /exit·/quit 처럼 같은 핸들러는 한 번만
                continue
            seen.add(fn)
            lines.append(f"{name} — {summary}")
        self.notify("\n".join(lines), title="사용 가능한 명령", timeout=12)

    async def _cmd_clear(self, arg: str) -> None:
        # /clear: 화면+대화 맥락을 비우고 새 스레드로 진입한다(이전 세션은 --resume 로 복구 가능,
        # 메모리는 유지). 세션 교체 중 새 턴이 끼어들지 않도록 _busy 로 막는다.
        self._busy = True
        try:
            new_id = self.runner.new_thread()
            await self.query_one("#conversation", VerticalScroll).remove_children()
            self._activity_off()
            self._set_status("대기 중")
            self.notify(f"새 스레드 시작 · 세션 {new_id}", timeout=4)
        finally:
            self._busy = False

    async def _cmd_resume(self, arg: str) -> None:
        # 인자가 있으면 바로, 없으면 목록 modal 에서 고른다. 선택 결과는 콜백으로 받아 세션을 교체하고 이력을 재생한다.
        if arg:
            await self._resume_session(arg)
            return
        sessions = [s for s in Session.summaries(self.runner._ws.root) if s["id"] != self.runner.session.id]
        if not sessions:
            self.notify("재개할 저장된 세션이 없습니다.", timeout=4)
            return

        def picked(session_id) -> None:
            if session_id:
                self.call_later(self._resume_session, session_id)

        self.push_screen(SessionPicker(sessions), picked)

    async def _resume_session(self, session_id: str) -> None:
        self._busy = True
        try:
            try:
                session = self.runner.resume(session_id)
            except FileNotFoundError:
                ids = ", ".join(Session.list_ids(self.runner._ws.root)) or "없음"
                self.notify(f"세션 없음: {session_id} (목록: {ids})", severity="warning", timeout=6)
                return
            conv = self.query_one("#conversation", VerticalScroll)
            await conv.remove_children()
            await self._replay_history(session.history)
            self._activity_off()
            self._set_status("대기 중")
            self.notify(f"세션 재개 · {session.id} ({len(session.turns)}턴)", timeout=4)
        finally:
            self._busy = False

    async def _replay_history(self, history: list) -> None:
        """저장된 user/assistant 이력을 대화 스트림에 다시 그린다 — 이어서 대화할 맥락을 화면에도 보여준다."""
        conv = self.query_one("#conversation", VerticalScroll)
        for m in history:
            content = m.get("content") or ""
            if m.get("role") == "user":
                await conv.mount(Static(f"» {content}", classes="user-turn", markup=False))
            else:
                await conv.mount(Markdown(content, classes="answer"))
        conv.anchor()

    async def _cmd_memory(self, arg: str) -> None:
        self.notify(self.runner.memory.read() or "(비어 있음)", title="메모리", timeout=10)

    async def _cmd_remember(self, arg: str) -> None:
        kind, note = parse_remember(arg)
        saved = self.runner.memory.remember(note, kind)
        self.notify(f"기억함({kind})." if saved else "이미 기억하고 있거나 내용이 비어 있음.", timeout=3)

    @on(MemorySaved)
    def _on_memory_saved(self, msg: MemorySaved) -> None:
        self.notify(f"기억({msg.kind}): {msg.note[:60]}", title="메모리", timeout=4)

    async def _cmd_theme(self, arg: str) -> None:
        # 내장 테마 + koda-dark 중 선택 (opencode/codex /theme 관례).
        if not arg:
            self.notify(f"현재: {self.theme}\n사용 가능: {', '.join(sorted(self.available_themes))}",
                        title="테마", timeout=8)
            return
        if arg not in self.available_themes:
            self.notify(f"알 수 없는 테마: {arg} — /theme 로 목록 확인", severity="warning", timeout=4)
            return
        self.theme = arg
        self.notify(f"테마: {arg}", timeout=3)

    async def _cmd_thinking(self, arg: str) -> None:
        # 세션 내(프로세스) 설정. 이미 그려진 사고 줄도 같이 숨기거나 되살린다.
        if arg not in ("", "show", "hide"):
            self.notify("사용법: /thinking [show|hide]", severity="warning", timeout=4)
            return
        self._thinking_visible = (arg == "show") if arg else not self._thinking_visible
        for step in self.query(ReasoningStep):
            step.display = self._thinking_visible
        self.notify(f"사고 표시: {'켬' if self._thinking_visible else '끔'}", timeout=3)

    async def _cmd_exit(self, arg: str) -> None:
        self._begin_close()

    async def _cmd_compact(self, arg: str) -> None:
        # 압축은 LLM 요약 호출 → 턴과 동일하게 worker thread 로. busy 가드는 _on_submit 이 이미 함.
        self._busy = True
        self.query_one("#prompt", PromptArea).disabled = True
        self._set_status("압축 중")
        self._activity_on("문맥 압축")
        self.run_worker(self.runner.compact_now, thread=True, exclusive=True, group="turn")

    async def _start_turn(self, task: str) -> None:
        self._busy = True
        self.query_one("#prompt", PromptArea).disabled = True
        self._draft = ""
        self._cur_prose_stream = None
        self._cur_prose_md = None       # 이전 턴의 단락이 기준점으로 남으면 새 턴의 사고가 지난 턴에 끼어든다
        self._cur_group = self._prose_group = self._cur_explore = None
        self._turn_widgets = []
        conv = self.query_one("#conversation", VerticalScroll)
        # 질의 Static 은 _turn_widgets 에 안 넣는다 — retry 시 산문/툴만 폐기하고 질의는 보존.
        # 산문 세그먼트는 lazy mount(툴 직전 빈 세그먼트 방지).
        await conv.mount(Static(f"» {task}", classes="user-turn", markup=False))
        conv.anchor()
        self._set_status("작업 중")
        self._activity_on("시작")
        self.run_worker(partial(self.runner.run_turn, task), thread=True,
                        exclusive=True, group="turn")

    def _end_turn(self, status: str = "대기 중") -> None:
        self._busy = False
        # 산문 세그먼트 스트림은 _on_turn_done/_on_turn_failed 가 _close_prose 로 이미 닫았다(여긴 방어).
        self._cur_prose_stream = None
        # 턴 종료 시 미완 스텝 정리. worker 가 툴 도중 죽으면(ToolEnd 미도착) 진행 상태가 영구히
        # 남는다. 성공 턴은 이미 비어 no-op.
        for run_id, step in self._tool_steps.items():
            self._finish_step(step, run_id, "(중단됨)", ok=False)
        self._tool_steps = {}
        self._activity_off()
        self._set_status(status)
        if isinstance(self.screen, ApprovalModal):
            # approver timeout(=deny) 으로 응답 없이 턴이 끝난 고아 modal 정리 — 늦은 resolve 는
            # 이미 반환된 worker 에 no-op.
            self.screen.dismiss(False)
        prompt = self.query_one("#prompt", PromptArea)
        prompt.disabled = False
        prompt.focus()

    # --- worker 메시지 핸들러 ---

    @on(TokenChunk)
    async def _on_token(self, msg: TokenChunk) -> None:
        self._draft += msg.text
        self._think_tok = 0  # 본문 생성 시작 → reasoning 카운터 해제
        if self._cur_prose_stream is None:
            await self._open_prose()   # lazy — 턴 시작·툴 직후 첫 토큰에 새 산문 세그먼트
        await self._cur_prose_stream.write(msg.text)

    async def _open_prose(self) -> None:
        """새 산문 세그먼트(Markdown)를 mount 하고 스트림을 연다. 첫 토큰에 lazy 호출."""
        conv = self.query_one("#conversation", VerticalScroll)
        md = Markdown("", classes="answer")
        await conv.mount(md)
        self._turn_widgets.append(md)
        self._cur_prose_md = md
        self._cur_prose_stream = Markdown.get_stream(md)
        # 본문이 시작되면 스텝 구간이 끊긴다. 이 단락 도중 도착하는 사고는 단락 앞 그룹(_prose_group)에 들어간다.
        self._prose_group = self._cur_group
        self._cur_group = self._cur_explore = None
        conv.anchor()

    async def _close_prose(self) -> None:
        """현재 산문 세그먼트 스트림을 닫는다(툴 블록 진입·턴 종료·retry 시)."""
        if self._cur_prose_stream is None:
            return
        try:
            await self._cur_prose_stream.stop()
        except asyncio.CancelledError:
            pass  # 스트림 태스크가 기동 전에 취소된 극단 케이스 방어 — 핸들러 전파 시 앱이 죽는다
        self._cur_prose_stream = None
        self._cur_prose_md = self._prose_group = None   # 닫힌 단락은 기준점이 아니다. 다음 사고는 그 뒤의 새 그룹에 온다

    @on(PhaseEvent)
    async def _on_phase(self, msg: PhaseEvent) -> None:
        phase = msg.phase
        if phase.startswith("think:"):
            n = phase[6:]
            self._think_tok = int(n) if n.isdigit() else 0  # 활동바 전용
            return
        if phase.startswith("heartbeat:"):
            return  # 활동바 경과 초가 대체 — 로그·표시 불필요
        if phase.startswith("retry:"):
            await self._reset_turn()  # 1차 시도의 산문 세그먼트·툴 블록·매핑 전부 폐기
            reason = phase[6:]
            self._activity_label = "재시도"
            self.notify(f"빈 응답 — 변형 재시도 ({reason})", severity="warning", timeout=4)
            return
        if phase in _PHASE_TUI:
            label, _theme = _PHASE_TUI[phase]
            self._activity_label = label
            self._activity_detail = ""
            return
        if phase.startswith("tool:"):
            name = phase[5:]
            self._activity_detail = name
            self._turn_tools[name] += 1  # 활동바 롤업 tally
            return
        if phase.startswith("mcp:"):
            self.notify(phase[4:], timeout=3)
            return
        # 자유 형식 (문맥 압축 중…, 메모리 갱신 중… 등)
        self._activity_label = phase

    async def _reset_turn(self) -> None:
        """retry: 이번 시도의 산문 세그먼트·과정 그룹·매핑을 전부 폐기하고 재시작한다(질의 Static 보존). _tool_steps 를 안 비우면 뒤늦은 ToolEnd 가 stale run_id 로 제거된 위젯에 매칭된다."""
        await self._close_prose()
        for w in self._turn_widgets:
            try:
                await w.remove()
            except Exception:
                pass  # 이미 제거됐거나 mount 전인 위젯 — 폐기 목적이므로 무시
        self._turn_widgets = []
        self._tool_steps = {}
        self._cur_group = self._prose_group = self._cur_explore = None
        self._draft = ""

    @on(TurnDone)
    async def _on_turn_done(self, msg: TurnDone) -> None:
        result = msg.result
        final = (result.get("final") or "").strip()
        await self._close_prose()
        for w in self._turn_widgets:
            if isinstance(w, ProcessGroup):
                w.summarize()
        # 스트림 전면 유실일 때만 정본을 산문 세그먼트로 폴백 mount — 스트림된 세그먼트가 있으면
        # 통째 교체 시 멀티툴 턴의 중간 산문이 소실되므로 그대로 둔다.
        if final and not self._draft.strip():
            conv = self.query_one("#conversation", VerticalScroll)
            md = Markdown(final, classes="answer")
            await conv.mount(md)
            self._turn_widgets.append(md)
            conv.anchor()

        verdict = result.get("verdict")
        elapsed = int(time.monotonic() - self._turn_t0)
        footer = Text()
        footer.append(f"✓ 완료 · {elapsed}초", style=f"bold {self._theme_color('success')}")
        footer.append(f"\nroute: {result.get('route')}", style="dim")
        if verdict:
            footer.append("  ·  ", style="dim")
            verdict_color = self._theme_color("success") if verdict == "pass" else self._theme_color("error")
            footer.append(f"verdict: {verdict}", style=f"bold {verdict_color}")
        if result.get("verify_detail"):   # 검증 상세는 우측 패널 대신 턴 footer 인라인 (패널 폐지)
            footer.append(f"\n판정: {result['verify_detail']}", style="dim")
        if result.get("trace_url"):
            footer.append(f"\ntrace: {result['trace_url']}", style="dim")
        if result.get("empty_final"):
            reason = result.get("empty_reason") or "unknown"
            hint = "출력 예산 소진(재시도 권장)" if reason == "length_truncation" else "재시도 권장"
            footer.append(f"\n빈 응답 — {hint} (사유: {reason}) · 히스토리에 저장하지 않음",
                          style=self._theme_color("warning"))
            self.notify(f"빈 응답: {reason} · {hint} · 히스토리 미저장", severity="error", timeout=6)
        conv = self.query_one("#conversation", VerticalScroll)
        if not self._turn_widgets:   # 산문·툴 세그먼트가 하나도 없는 빈 턴 — 시각적 공백 방지
            await conv.mount(Static("(응답 없음)", classes="turn-footer", markup=False))
        await conv.mount(Static(footer, classes="turn-footer"))
        conv.anchor()
        # 세션 턴 수 갱신은 _end_turn 의 _set_status 가 statusline 을 재구성하며 반영한다.
        self._end_turn()

    @on(TurnFailed)
    async def _on_turn_failed(self, msg: TurnFailed) -> None:
        await self._close_prose()
        conv = self.query_one("#conversation", VerticalScroll)
        err_color = f"bold {self._theme_color('error')}"
        await conv.mount(Static(Text(f"턴 실패: {msg.error}", style=err_color), classes="turn-footer"))
        conv.anchor()
        self.notify(f"턴 실패: {msg.error}", severity="error", timeout=8)
        self._end_turn()

    @on(CompactDone)
    async def _on_compact_done(self, msg: CompactDone) -> None:
        # /compact worker 완료 — busy 해제·입력 재활성(턴과 동일 경로) 후 결과 안내
        self._end_turn()
        self.notify(msg.note, title="문맥 압축", timeout=5)

    # --- 과정 가시화 (ToolActivityHandler 콜백 → 그룹 안의 스텝 줄) ---

    async def _group(self) -> ProcessGroup:
        """현재 스텝 구간의 그룹. 없으면 산문을 닫고 대화 끝에 새로 만든다."""
        if self._cur_group is None or not self._cur_group.is_attached:
            await self._close_prose()
            self._cur_group = await self._mount_group()
        return self._cur_group

    async def _mount_group(self, before=None) -> ProcessGroup:
        conv = self.query_one("#conversation", VerticalScroll)
        group = ProcessGroup()
        await conv.mount(group, before=before)
        self._turn_widgets.append(group)   # retry 폐기 대상(스텝은 그룹과 함께 제거된다)
        conv.anchor()
        return group

    @on(ToolStart)
    async def _on_tool_start(self, msg: ToolStart) -> None:
        # 툴 진입 → 현재 산문 세그먼트를 닫아 다음 토큰이 새 세그먼트를 열게 한다(시간순 인터리브).
        group = await self._group()
        if msg.target is None:
            self._cur_explore = None      # 편집·실행 도구는 탐색 병합을 끊는다
            step = StepLine(msg.title, msg.name)
            await group.add(step)
        else:
            if self._cur_explore is None or not self._cur_explore.is_attached or self._cur_explore.full:
                self._cur_explore = ExploreStep()
                await group.add(self._cur_explore)
            self._cur_explore.add(msg.run_id, *msg.target)
            step = self._cur_explore
        self._tool_steps[msg.run_id] = step
        self.query_one("#conversation", VerticalScroll).anchor()

    @on(ReasoningEnd)
    async def _on_reasoning_end(self, msg: ReasoningEnd) -> None:
        # 사고는 그 호출의 본문보다 먼저 일어났으므로, 산문 세그먼트가 흐르는 중이면 그 앞 그룹에 넣는다.
        self._cur_explore = None      # 새 LLM 라운드. 다음 탐색이 이전 라운드의 줄에 섞이면 순서가 뒤틀린다
        if not self._thinking_visible:
            return
        if self._cur_prose_md is not None and self._cur_prose_md.is_attached:
            if self._prose_group is None or not self._prose_group.is_attached:
                self._prose_group = await self._mount_group(before=self._cur_prose_md)
            group = self._prose_group
        else:
            group = await self._group()
        await group.add(ReasoningStep(msg.text))
        self.query_one("#conversation", VerticalScroll).anchor()

    @on(ToolEnd)
    async def _on_tool_end(self, msg: ToolEnd) -> None:
        step = self._tool_steps.pop(msg.run_id, None)
        if step is None:
            return
        self._finish_step(step, msg.run_id, msg.output, msg.ok)
        if not msg.ok:
            self._cur_explore = None      # 실패가 섞이면 다음 탐색은 새 줄

    @staticmethod
    def _finish_step(step, run_id: str, output: str, ok: bool) -> None:
        if isinstance(step, ExploreStep):
            step.finish_one(run_id, output, ok)
        else:
            step.finish(output, ok)

    @on(ApprovalRequest)
    def _on_approval(self, msg: ApprovalRequest) -> None:
        # 라벨은 툴 롤업과 무관하게 항상 노출되므로 승인 대기를 라벨로 띄운다(detail 은 롤업에
        # 가려 안 보임). 모달 종료 시 직전 라벨로 복원한다.
        prev_label = self._activity_label
        self._activity_label = "승인 대기"

        def resolve(ok) -> None:
            self._activity_label = prev_label
            msg.resolve(bool(ok))

        # modal 닫힘(ESC)·거부·승인 결과가 resolve 로 worker 의 threading.Event 를 깨운다
        self.push_screen(ApprovalModal(msg.tool, msg.args), resolve)

    # --- 텍스트 선택 → 복사 ---

    def on_mouse_up(self, event) -> None:
        # 선택 구간은 mouse move 로 갱신되고 up 에서 확정된다. 렌더 뒤에 읽어야 마지막 구간까지 들어온다.
        self.call_after_refresh(self._copy_selection)

    def _copy_selection(self) -> bool:
        """화면 선택 텍스트가 있으면 클립보드로 보내고 True. 없으면 False (호출자가 원래 동작을 이어간다)."""
        text = self.screen.get_selected_text()
        if not text:
            return False
        self.copy_to_clipboard(text)
        self.notify(f"복사됨 · {len(text)}자", timeout=2)
        return True

    # --- 종료 ---

    def action_graceful_quit(self) -> None:
        # Ctrl+C 관례: 선택이 있으면 복사, 없으면 종료.
        if self._copy_selection():
            self.screen.clear_selection()
            return
        self._begin_close()

    def _begin_close(self) -> None:
        if self._close_requested:
            return
        if self._busy:
            self.notify("턴 실행 중 — 완료 후 종료할 수 있습니다.", severity="warning")
            return
        self._close_requested = True
        self.query_one("#prompt", PromptArea).disabled = True
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
            mcp_tools = asyncio.run(load_mcp_tools(mcp_dirs(ws.root, args.mcps_dir), on_log=lambda m: _err(f"  · {m}")))
        except Exception as e:
            _err(f"  · MCP 로드 실패({type(e).__name__}) — 로컬 도구만 사용")

    # statusline 에 표시할 활성 모델명 — PRIMARY 가 아니라 실제로 선택된 provider 기준. primary 가 죽어
    # 폴백으로 뜬 경우 PRIMARY 기준 표시는 호출되지 않는 모델을 보여 준다.
    model_name = ""
    try:
        from ..config import load_model_settings
        from ..llm import select_provider
        st = load_model_settings()
        provider, _ = select_provider(st)
        model_name = st.local_model if provider == "local" else st.openai_model
    except Exception:
        pass

    app = KodaTui(ws, session, memory, mcp_tools, auto_yes=args.yolo, model_name=model_name)
    app.run()
    final = app.runner.session   # /clear 로 세션이 교체됐을 수 있으므로 최종 세션 기준
    if final.history:
        _err(f"세션 저장됨: {final.id}  (재개: koda 실행 후 /resume {final.id})")
    return 0
