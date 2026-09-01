from __future__ import annotations

import asyncio
import time
from collections import Counter
from functools import partial

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import MouseDown
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import Footer, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ..cli import _err
from ..config import DEFAULT_CONTEXT_BUDGET, switch_model
from ..gitinfo import git_ref
from ..llm import select_provider
from ..mcp import config_path, connect_mcp, connect_server, disconnect_server, find_config, mcp_dirs, remove_config, write_config
from ..memory import Memory, parse_remember
from ..session import COMPACT_THRESHOLD_CHARS, Session
from ..workspace import Workspace
from .paths import index_paths, match_paths
from .model import (
    DIFF_TOOLS,
    ApprovalRequest,
    UsageUpdate,
    CompactDone,
    McpLoaded,
    MemorySaved,
    PhaseEvent,
    ReasoningEnd,
    SessionClosed,
    TokenChunk,
    TurnCancelled,
    ToolEnd,
    ToolStart,
    TurnDone,
    TurnFailed,
)
from .runner import APPROVAL_TIMEOUT_S, TuiTurnRunner
from .widgets import (
    ApprovalModal,
    ConfirmScreen,
    DiffBlock,
    ExploreStep,
    InfoScreen,
    McpAddScreen,
    McpScreen,
    ModelScreen,
    ProcessGroup,
    PromptArea,
    ReasoningStep,
    SessionPanel,
    SessionPicker,
    StepLine,
)

_CTRL_C_WINDOW_S = 2.0  # 이 안에 Ctrl+C 를 한 번 더 누르면 종료
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # rich 'dots' 프레임 — toad/Claude Code 계열 표준 스피너
_GAUGE_CELLS = 10  # 상태줄 컨텍스트 게이지 칸 수

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


class KodaScreen(Screen):
    """기본 화면. 스트리밍 재렌더로 이미 떼어진 문단을 클릭하면 textual 8.2 의 선택 시작 코드가 parent=None 으로 죽으므로(컴포지터 맵이 한 프레임 늦다) 그 클릭만 버린다."""

    def _forward_event(self, event) -> None:
        if isinstance(event, MouseDown) and not self.app.mouse_captured:
            widget, _ = self.get_widget_and_offset_at(event.x, event.y)
            if widget is not None and not widget.is_attached:
                self._select_state = None
                return
        super()._forward_event(event)


class SessionRun:
    """세션 하나의 실행·표시 상태. 세션들이 동시에 돌아도 대화 영역·스트림·과정 그룹·토큰이 섞이지 않도록 한곳에 모은다."""

    def __init__(self, app, ws, session, memory, auto_yes: bool, run_task_fn, approval_timeout: float):
        self.runner = TuiTurnRunner(app, ws, session, memory, auto_yes,
                                    run_task_fn=run_task_fn, approval_timeout=approval_timeout)
        self.conv = VerticalScroll(id=f"conv_{session.id}", classes="conversation")
        self.conv.display = False        # 활성 세션만 보인다. 배경 세션은 자기 컨테이너에 계속 그린다
        if not session.branch:   # 브랜치·기준 커밋을 세션에 새긴다(저장소가 아니면 빈 값)
            session.branch, base = git_ref(ws.root)
            session.base_commit = session.base_commit or base
        self.busy = False
        self.failed = False       # 마지막 턴이 실패했는지 (패널의 ✕ 표시)
        self.approval: ApprovalRequest | None = None   # 대기 중인 승인 요청 — 이 세션으로 전환할 때 모달을 띄운다
        self.draft = ""                  # 이번 턴 스트림 누적 — retry 리셋·finalize 폴백 판단용
        self.prose_stream = None
        self.prose_md = None             # 현재 산문 세그먼트. 열려 있는 동안 도착한 사고는 그 앞 그룹으로 간다
        self.turn_widgets: list = []     # 이번 턴 mount 위젯(질의 Static 제외) — retry 폐기용
        self.cur_group: ProcessGroup | None = None
        self.prose_group: ProcessGroup | None = None
        self.cur_explore: ExploreStep | None = None
        self.tool_steps: dict = {}       # run_id → StepLine (ToolStart↔ToolEnd 매칭)
        self.turn_t0 = 0.0
        self.think_tok = 0               # reasoning 진행 (think:N) — 토큰 도착 시 해제
        self.label = ""                  # 활동바 단계 라벨
        self.detail = ""                 # 현재 실행 중인 도구명
        self.tools: Counter = Counter()  # 턴 전체 툴 tally(활동바 롤업)
        self.ctx_used = 0                # 이 세션에서 가장 컸던 LLM 호출의 프롬프트+출력 토큰 = 컨텍스트 점유 근사값
        self.tokens = [0, 0]             # 세션 누적 입력/출력 토큰 (/status)

    @property
    def session(self):
        return self.runner.session

    @property
    def state(self) -> str:
        if self.approval is not None:
            return "approval"
        if self.busy:
            return "running"
        return "failed" if self.failed else "idle"


class KodaTui(App):
    TITLE = "koda"
    # 텍스트 선택은 켜 둔다. 드래그로 고른 뒤 놓으면 클립보드(OSC 52)로 복사되고, Ctrl+C 도 선택이 있으면 복사다.
    # 과거 표 셀 클릭 크래시(textual 8.2.8) 회피용으로 꺼 두었으나 스트리밍 재렌더 중 클릭·드래그 반복으로도 재현되지 않아 되살렸다.
    BINDINGS = [
        ("ctrl+q", "graceful_quit", "종료"),
        ("ctrl+b", "toggle_panel", "세션 패널"),
        # Ctrl+C 는 단계형: 답변 중이면 중단, 2초 안에 한 번 더 누르면 종료. 입력창(TextArea) focus 시 기본은 copy 라 priority 로 이긴다.
        Binding("ctrl+c", "ctrl_c", "중단/종료", priority=True, show=False),
    ]
    CSS = """
    #body { height: 1fr; }
    #main { width: 1fr; height: 1fr; }
    #conv_stack { width: 1fr; height: 1fr; }
    #session_panel { width: 30; height: 1fr; background: $panel; padding: 0 1; }
    #session_panel.-hidden { display: none; }
    #sp_title { height: 1; text-style: bold; color: $accent; margin-bottom: 1; }
    #sp_list { height: 1fr; border: none; background: transparent; padding: 0; }
    #sp_list > .option-list--option { padding: 0 0 1 0; }
    #sp_hint { height: auto; color: $text-muted; }
    ConfirmScreen { align: center middle; }
    #confirm_dialog { width: 60%; max-width: 80; height: auto; border: thick $error; background: $surface; padding: 1 2; }
    #confirm_body { color: $text-muted; margin: 1 0; }
    #confirm_buttons { height: auto; align-horizontal: center; margin-top: 1; }
    #confirm_buttons Button { margin: 0 2; }
    .conversation { width: 1fr; height: 1fr; padding: 0 1; }
    #prompt { height: auto; max-height: 10; }
    #path_picker { height: auto; max-height: 12; border: none; background: $panel; padding: 0 1; }
    #activity { height: 1; padding: 0 1; }
    #statusline { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    .user-turn { color: $accent; text-style: bold; margin: 1 0 0 0; }
    .diff-block { margin: 0 0 1 0; padding: 0 0 0 2; }
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
    McpScreen { align: center middle; }
    #mcp_dialog { width: 90%; max-width: 110; height: auto; max-height: 80%; border: thick $primary;
                  background: $surface; padding: 1 2; }
    #mcp_title { margin-bottom: 1; }
    #mcp_list { height: auto; max-height: 10; }
    #mcp_detail { color: $text-muted; margin-top: 1; height: auto; max-height: 14; }
    #mcp_dirs { color: $text-muted; margin-top: 1; }
    McpAddScreen { align: center middle; }
    #mcp_add_dialog { width: 90%; max-width: 110; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    #mcp_add_title { text-style: bold; margin-bottom: 1; }
    #mcp_add_hint { color: $text-muted; margin-bottom: 1; }
    InfoScreen { align: center middle; }
    ModelScreen { align: center middle; }
    #model_dialog { width: 80%; max-width: 100; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    #model_title, #model_form_title { margin-bottom: 1; text-style: bold; }
    #model_providers { height: auto; }
    #model_form { height: auto; }
    #model_form Input { margin-bottom: 1; }
    #model_hint { color: $text-muted; }
    #info_dialog { width: 80%; max-width: 100; height: auto; max-height: 80%; border: thick $primary;
                   background: $surface; padding: 1 2; }
    #info_title { margin-bottom: 1; text-style: bold; }
    #info_body { color: $text-muted; }
    ProcessGroup, StepLine { border: none; background: transparent; padding: 0; margin: 0; }
    ProcessGroup CollapsibleTitle, StepLine CollapsibleTitle { color: $text-muted; padding: 0; }
    ProcessGroup > Contents { padding: 0 0 0 2; }
    StepLine > Contents { padding: 0 0 0 2; }
    StepLine Static { color: $text-muted; }
    StepLine.-run CollapsibleTitle { color: $text-disabled; }
    StepLine.-fail CollapsibleTitle { color: $error; }
    ReasoningStep CollapsibleTitle { text-style: italic; }
    """

    def __init__(self, ws, session, memory, mcp_dirs: list | None, auto_yes: bool,
                 run_task_fn=None, approval_timeout: float = APPROVAL_TIMEOUT_S,
                 model_name: str = "", settings=None):
        super().__init__()
        self._ws = ws
        self._memory = memory
        self._settings = settings      # /model 이 교체한다. 모든 세션이 공유(runner.settings 로 접근)
        self._mcp_tools: list = []     # 연결된 MCP 도구 — 모든 세션이 공유(runner.mcp_tools 로 접근)
        self._auto_yes = auto_yes
        self._run_task_fn = run_task_fn
        self._approval_timeout = approval_timeout
        # 세션 하나 = SessionRun 하나. 여러 세션이 동시에 턴을 돌 수 있고, 화면 전환은 활성 세션 교체다.
        self._runs: dict[str, SessionRun] = {}
        self._active_id = session.id
        self._new_run(session)
        self._model_name = model_name
        self._mode = "yolo" if auto_yes else "manual"   # 승인 모드 뱃지(statusline) — --yolo 자동 승인
        self._ctrl_c_at = 0.0                          # 마지막 Ctrl+C 시각(연타 판정)
        # MCP 는 화면이 뜬 뒤 백그라운드로 붙는다(None 이면 --no-mcp). 결과는 /mcp 화면과 상태줄에 보인다.
        self._mcp_dirs = mcp_dirs
        self._mcp: list = []
        self._mcp_connecting = bool(mcp_dirs)
        self._path_index: list[str] = []      # @경로 후보용 워크스페이스 색인 (3초마다 갱신)
        self._path_index_at = 0.0
        self._picker_dismissed: str | None = None  # Esc 로 닫은 토큰(@경로·/명령). 같은 토큰이면 다시 띄우지 않는다
        self._picker_start = (0, 0)               # 선택 시 바꿀 구간의 시작
        # 이름 주의: _closing 은 textual MessagePump 내부 플래그라 덮어쓰면 post_message 가
        # 조용히 드롭된다 — 쓰지 말 것.
        self._close_requested = False
        self._thinking_visible = True
        self._spin_i = 0
        # 슬래시 커맨드 레지스트리 — /help 목록·placeholder 의 단일 출처.
        self._commands: dict[str, tuple[str, object]] = {
            "/help": ("사용 가능한 명령 목록", self._cmd_help),
            "/compact": ("대화 이력을 요약으로 압축", self._cmd_compact),
            "/clear": ("새 스레드 시작(대화 맥락 초기화)", self._cmd_clear),
            "/resume": ("저장된 세션 열기: /resume [session-id] (인자 없으면 목록에서 선택)", self._cmd_resume),
            "/rename": ("세션 이름 바꾸기: /rename <이름> (인자 없으면 현재 이름 표시)", self._cmd_rename),
            "/mcp": ("MCP 서버 상태·도구 보기, 재연결·끄기", self._cmd_mcp),
            "/status": ("세션·모델·컨텍스트·MCP 상태 보기", self._cmd_status),
            "/model": ("모델 전환 (대화형). 단축: /model <모델명> [provider=…] [base_url=…] [key=…]", self._cmd_model),
            "/memory": ("메모리 내용 보기", self._cmd_memory),
            "/remember": ("메모리에 저장: /remember [semantic|episodic|procedural] <내용>", self._cmd_remember),
            "/theme": ("테마 변경: /theme <이름> (인자 없으면 목록)", self._cmd_theme),
            "/thinking": ("사고 줄 표시 전환: /thinking [show|hide]", self._cmd_thinking),
            "/exit": ("저장 후 종료", self._cmd_exit),
            "/quit": ("저장 후 종료", self._cmd_exit),
        }

    # --- 세션 실행 단위 ---

    @property
    def _active(self) -> SessionRun:
        return self._runs[self._active_id]

    @property
    def runner(self) -> TuiTurnRunner:
        """활성 세션의 runner. 세션마다 하나씩 있고 설정·MCP 도구는 앱이 공유한다."""
        return self._active.runner

    @property
    def conv(self) -> VerticalScroll:
        """활성 세션의 대화 컨테이너."""
        return self._active.conv

    def _new_run(self, session) -> SessionRun:
        run = SessionRun(self, self._ws, session, self._memory, self._auto_yes,
                         self._run_task_fn, self._approval_timeout)
        self._runs[session.id] = run
        return run

    def _run_of(self, msg) -> SessionRun:
        """메시지를 낸 세션의 실행 단위. 표시가 없거나(직접 post) 이미 닫힌 세션이면 활성 세션으로 본다."""
        return self._runs.get(getattr(msg, "session_id", "") or "", self._active)

    # --- 레이아웃 ---

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield SessionPanel(self._ws.root.name, id="session_panel")
            with Vertical(id="main"):
                with Vertical(id="conv_stack"):
                    yield self._active.conv
                yield Static("", id="activity", markup=False)
                yield Static(self._statusline_text(), id="statusline", markup=False)
                yield OptionList(id="path_picker")
                yield PromptArea(id="prompt", tab_behavior="focus",
                                 placeholder="요청 입력…  (Alt+Enter·Ctrl+J 줄바꿈 · /help 명령 · Ctrl+Q 종료)")
        yield Footer()

    def get_default_screen(self) -> Screen:
        return KodaScreen(id="_default")

    def notify(self, message: str, *, title: str = "", severity="information", timeout=None, markup: bool = False) -> None:
        # 알림 본문엔 명령 안내의 [provider=…] 나 메모리 내용처럼 대괄호가 흔하다. Rich 마크업으로 해석하면 MarkupError 로 앱이 죽는다.
        super().notify(message, title=title, severity=severity, timeout=timeout, markup=markup)

    def on_mount(self) -> None:
        self.register_theme(KODA_DARK)
        self.theme = "koda-dark"
        self._active.conv.display = True
        self.query_one("#activity", Static).display = False
        self.query_one("#path_picker", OptionList).display = False
        self._activity_timer = self.set_interval(0.125, self._tick_activity, pause=True)
        if self.size.width < 100:
            self.query_one(SessionPanel).add_class("-hidden")   # 좁은 화면은 접힌 채 시작 (Ctrl+B 로 펼침)
        self.refresh_panel()
        self.query_one("#prompt", PromptArea).focus()
        if self._mcp_dirs:
            self.run_worker(partial(self._connect_all_mcp, self._mcp_dirs), thread=True, group="mcp")

    def get_system_commands(self, screen):
        # maximize 는 한 위젯으로 화면을 덮어 대화·입력·상태줄을 가린다. koda 는 이 넷이 한 화면에
        # 공존하는 단일 뷰라, 어떤 위젯(입력창·툴 블록 등)을 최대화해도 답변이 안 보이는 깨진 상태가
        # 된다. 그래서 커맨드 팔레트에서 maximize 명령을 통째로 뺀다(#21). maximize 의 유일한 트리거다.
        for command in super().get_system_commands(screen):
            if command.callback == screen.action_maximize:
                continue
            yield command

    # --- statusline: 모델 · 승인 모드 · MCP · 컨텍스트 잔량 · 상태 ---

    @property
    def _ctx_budget(self) -> int:
        return int(getattr(self._settings, "context_budget_tokens", DEFAULT_CONTEXT_BUDGET) or DEFAULT_CONTEXT_BUDGET)

    @staticmethod
    def _k(n: int) -> str:
        return f"{n / 1000:.1f}k" if n < 10_000 else f"{n // 1000}k"

    @staticmethod
    def _history_chars(session) -> int:
        return sum(len(m.get("content") or "") for m in session.history)

    def _statusline_text(self) -> Text:
        """모델 · 승인 모드 · 브랜치 · MCP · 컨텍스트 게이지. 진행 상태는 바로 위 활동바가 말하므로 문구로 중복하지 않는다."""
        run = self._active
        t = Text()
        t.append(self._model_name or "모델 미정", style="bold")
        t.append(f" · {self._mode}", style="dim")
        if branch := run.session.branch:
            t.append(f" · ⎇ {branch}", style="dim")
        if self._mcp_dirs:
            t.append(f" · {self._mcp_text()}", style="dim")
        # 게이지는 대화 히스토리가 압축 임계에 얼마나 다가갔는지를 보여 준다. 턴마다 실제로 차오르고,
        # 곧 일어날 일(문맥 압축)을 예고한다. 프롬프트 토큰 최대치는 /status 에 둔다.
        used, budget = self._history_chars(run.session), COMPACT_THRESHOLD_CHARS
        ratio = min(1.0, used / budget) if budget else 0.0
        color = self._theme_color("success") if ratio < 0.5 else self._theme_color("warning") if ratio < 0.8 else self._theme_color("error")
        filled = round(ratio * _GAUGE_CELLS)
        t.append("  컨텍스트 ", style="dim")
        t.append("█" * filled + "░" * (_GAUGE_CELLS - filled), style=color)
        t.append(f" {ratio:.0%}", style=f"bold {color}")
        return t

    def _mcp_text(self) -> str:
        if self._mcp_connecting:
            return "mcp 연결 중"
        failed = sum(1 for s in self._mcp if s.error and s.enabled)
        return f"mcp {len(self._mcp_tools)}" + (f" (실패 {failed})" if failed else "")

    def _refresh_statusline(self) -> None:
        self.query_one("#statusline", Static).update(self._statusline_text())

    @on(UsageUpdate)
    def _on_usage(self, msg: UsageUpdate) -> None:
        run = self._run_of(msg)
        # 한 턴 안에도 라우터·검증처럼 작은 호출이 섞이므로 마지막 호출은 대화 크기가 아니다. 최대값이 실제 점유에 가깝다.
        run.ctx_used = max(run.ctx_used, msg.input_tokens + msg.output_tokens)
        run.tokens[0] += msg.input_tokens
        run.tokens[1] += msg.output_tokens

    # --- /status · /model ---

    def _active_model(self) -> tuple[str, str, str]:
        """(provider, 모델명, base_url). 설정이 없으면 표시용 이름만."""
        st = self._settings
        if st is None:
            return "", self._model_name, ""
        if st.primary == "local":
            return "local", st.local_model, st.local_base_url
        return st.provider_name, st.openai_model, st.openai_base_url

    async def _cmd_status(self, arg: str) -> None:
        st, s = self._settings, self.runner.session
        provider, model, base_url = self._active_model()
        mcp = ", ".join(f"{x.name}({'꺼짐' if not x.enabled else '실패' if x.error else len(x.tools)})" for x in self._mcp) or ("연결 중" if self._mcp_connecting else "없음")
        used_in, used_out = self._active.tokens
        open_ids = ", ".join(f"{r.session.id}{'(답변 중)' if r.busy else ''}" for r in self._runs.values())
        lines = [
            f"세션        {s.id} · {len(s.turns)}턴" + (f" · {s.title}" if s.title else ""),
            f"브랜치      {s.branch or '(git 저장소 아님)'}" + (f" · 기준 {s.base_commit}" if s.base_commit else ""),
            f"열린 세션   {len(self._runs)}개 · {open_ids}",
            f"모델        {model} ({provider or '?'}) · {base_url}",
            f"추론        style={getattr(st, 'reasoning_style', '?')} effort={getattr(st, 'reasoning_effort', '?')} · 구조화={getattr(st, 'structured_method', '?')}"
            + (" · Responses API" if getattr(st, "use_responses_api", False) else ""),
            f"컨텍스트    대화 {self._k(self._history_chars(s))}/{self._k(COMPACT_THRESHOLD_CHARS)}자 ({self._history_chars(s) / COMPACT_THRESHOLD_CHARS:.0%}, 압축 임계)",
            f"프롬프트    최대 {self._k(self._active.ctx_used)} / 예산 {self._k(self._ctx_budget)} 토큰 · 세션 누적 입력 {self._k(used_in)} 출력 {self._k(used_out)}",
            f"승인        {self._mode}",
            f"MCP         {mcp}",
            f"워크스페이스 {self._ws.root}",
            f"메모리      {sum(len(self._memory.items(k)) for k in ('semantic', 'episodic', 'procedural'))}개 항목",
        ]
        self.push_screen(InfoScreen("상태  (ESC 닫기)", "\n".join(lines)))

    async def _cmd_model(self, arg: str) -> None:
        # 인자 없음: provider 선택 → 모델명·base_url·키 입력 창. 인자 있음: 한 줄 단축 문법.
        usage = "사용법: /model (대화형) 또는 /model <모델명> [provider=local|openai|deepseek] [base_url=…] [key=…]"
        if self._settings is None:
            self.notify("설정을 읽지 못해 전환할 수 없습니다(.env 확인).", severity="warning", timeout=5)
            return
        if not arg:
            self.push_screen(ModelScreen(self._settings), self._apply_model_form)
            return
        words = arg.split()
        opts = dict(w.split("=", 1) for w in words if "=" in w)
        names = [w for w in words if "=" not in w]
        if len(names) != 1:
            self.notify(usage, severity="warning", timeout=5)
            return
        self._switch_model(names[0], opts.get("provider", ""), opts.get("base_url", ""), opts.get("key", ""))

    def _apply_model_form(self, result) -> None:
        if result:
            self._switch_model(result["model"], result["provider"], result["base_url"], result["api_key"])

    def _switch_model(self, model: str, provider: str, base_url: str, api_key: str) -> None:
        try:
            new = switch_model(self._settings, model, provider=provider, base_url=base_url, api_key=api_key)
        except Exception as e:
            self.notify(f"전환 실패: {e}", severity="error", timeout=6)
            return
        self._settings = new
        for run in self._runs.values():   # 모델이 바뀌면 어느 세션의 점유량도 더 이상 유효하지 않다
            run.ctx_used = 0
        self._model_name = new.local_model if new.primary == "local" else new.openai_model
        self._refresh_statusline()
        self.run_worker(partial(self._check_model, new), thread=True, group="model")

    def _check_model(self, settings) -> None:
        provider, reason = select_provider(settings)
        name = settings.local_model if provider == "local" else settings.openai_model
        base = settings.local_base_url if provider == "local" else settings.openai_base_url
        if provider != settings.primary:
            self.call_from_thread(self.notify, f"전환한 서버가 응답하지 않아 폴백 사용: {name} · {base} ({reason})", severity="warning", timeout=8)
        else:
            self.call_from_thread(self.notify, f"모델 전환: {name} · {base}", timeout=5)

    # --- MCP: 백그라운드 연결 · /mcp 화면 ---

    def _connect_all_mcp(self, dirs) -> None:
        try:
            servers = asyncio.run(connect_mcp(dirs, cwd=self._ws.root))
        except Exception as e:  # 어댑터 미설치 등. 로컬 도구만으로 계속한다
            self.call_from_thread(self.notify, f"MCP 사용 불가: {e}", severity="warning", timeout=6)
            servers = []
        self.post_message(McpLoaded(servers))

    def _reconnect_mcp(self, server) -> None:
        asyncio.run(connect_server(server))
        self.post_message(McpLoaded(self._mcp))

    def _disconnect_mcp(self, server) -> None:
        asyncio.run(disconnect_server(server))
        self.post_message(McpLoaded(self._mcp))

    @on(McpLoaded)
    def _on_mcp_loaded(self, msg: McpLoaded) -> None:
        self._mcp = msg.servers
        self._mcp_connecting = False
        self._apply_mcp()
        failed = [s.name for s in self._mcp if s.error and s.enabled]
        if failed:
            self.notify(f"MCP 연결 실패: {', '.join(failed)} (/mcp 로 확인)", severity="warning", timeout=6)
        if isinstance(self.screen, McpScreen):
            self.screen.refresh_servers(connecting=False, servers=self._mcp)

    def _apply_mcp(self) -> None:
        """켜져 있고 연결된 서버의 도구만 다음 턴부터 쓴다(모든 세션 공통)."""
        self._mcp_tools = [t for s in self._mcp for t in s.active_tools]
        self._refresh_statusline()

    async def _cmd_mcp(self, arg: str) -> None:
        def reconnect(server) -> None:
            server.enabled = True
            self.notify(f"MCP '{server.name}' 재연결 중…", timeout=3)
            self.run_worker(partial(self._reconnect_mcp, server), thread=True, group="mcp")

        def toggle(server) -> None:
            # 끄면 세션(stdio 면 서버 프로세스)까지 닫고, 켜면 다시 연다. 도구 목록은 즉시 반영된다.
            if server.enabled:
                server.enabled = False
                self._apply_mcp()
                self.run_worker(partial(self._disconnect_mcp, server), thread=True, group="mcp")
            else:
                reconnect(server)

        def add() -> None:
            def added(text) -> None:
                if text:
                    self.run_worker(partial(self._add_mcp, text), thread=True, group="mcp")

            self.push_screen(McpAddScreen(), added)

        def delete(server) -> None:
            def done(ok) -> None:
                if ok:
                    self.run_worker(partial(self._delete_mcp, server), thread=True, group="mcp")

            self.push_screen(ConfirmScreen("MCP 서버 삭제", f"'{server.name}' 설정 파일에서 이 서버를 지웁니다.", yes="삭제"), done)

        def persist(server) -> None:
            self.run_worker(partial(self._persist_mcp, server), thread=True, group="mcp")

        self.push_screen(McpScreen(self._mcp, list(self._mcp_dirs or []), self._mcp_connecting,
                                   toggle, reconnect, add, delete, persist))

    # --- /mcp 화면의 추가·삭제·영구 토글 (worker thread: 파일 쓰기 + 재연결) ---

    def _reload_mcp(self, note: str) -> None:
        self.call_from_thread(self.notify, note, timeout=4)
        if self._mcp_dirs:
            self._connect_all_mcp(self._mcp_dirs)

    def _add_mcp(self, text: str) -> None:
        from ..mcpcli import spec_from_text

        parsed = spec_from_text(text)
        if parsed is None:
            self.call_from_thread(self.notify, "설정을 읽지 못했습니다. JSON 스니펫·URL·실행 명령 중 하나여야 합니다.",
                                  severity="warning", timeout=6)
            return
        name, spec = parsed
        try:
            path = write_config(config_path(name, "user", self._ws.root), name, spec)
        except OSError as e:
            self.call_from_thread(self.notify, f"저장 실패: {e}", severity="error", timeout=6)
            return
        self._reload_mcp(f"MCP '{name}' 추가됨 · {path}")

    def _delete_mcp(self, server) -> None:
        try:
            path = remove_config(server.name, self._ws.root)
        except OSError as e:
            self.call_from_thread(self.notify, f"삭제 실패: {e}", severity="error", timeout=6)
            return
        if path is None:
            self.call_from_thread(self.notify, f"설정 파일을 찾지 못했습니다: {server.name}", severity="warning", timeout=5)
            return
        self._reload_mcp(f"MCP '{server.name}' 삭제됨")

    def _persist_mcp(self, server) -> None:
        """d(세션 토글)와 달리 설정 파일에 켜짐/꺼짐을 남긴다. 다음 실행에도 유지된다."""
        path = find_config(server.name, self._ws.root)
        if path is None:
            self.call_from_thread(self.notify, f"설정 파일을 찾지 못했습니다: {server.name}", severity="warning", timeout=5)
            return
        import json as _json

        data = _json.loads(path.read_text(encoding="utf-8"))
        spec = dict(data["mcpServers"][server.name])
        spec["enabled"] = not server.enabled
        write_config(path, server.name, spec)
        self._reload_mcp(f"MCP '{server.name}' {'켬' if spec['enabled'] else '끔'} (설정에 기록)")

    def _theme_color(self, name: str) -> str:
        """테마 색상 hex 조회. 라이트/다크 자동 적응."""
        return self.theme_variables.get(name, name)

    # --- 세션 패널: 목록 · 세션별 상태 표시 · 전환/새로/삭제 ---

    def _states(self) -> dict[str, str]:
        return {sid: run.state for sid, run in self._runs.items()}

    def _open_rows(self) -> list[dict]:
        """열려 있는 세션의 패널 행. 디스크를 읽지 않아 스피너 틱마다 써도 된다."""
        rows = []
        for sid, run in self._runs.items():
            s = run.session
            rows.append({"id": sid, "title": s.title, "turns": len(s.turns),
                         "first_task": s.turns[0]["task"] if s.turns else "", "branch": s.branch,
                         "answered": s.updated_at, "updated": s.updated_at or s.created_at})
        return rows

    def _panel_rows(self) -> list[dict]:
        """저장된 세션 목록에 열려 있는(아직 저장 전일 수 있는) 세션을 얹는다. 정렬은 마지막 답변 시각순이라 세션을 오가도 순서가 흔들리지 않는다."""
        rows = {r["id"]: r for r in Session.summaries(self._ws.root)}
        for row in self._open_rows():
            rows[row["id"]] = row
        return sorted(rows.values(), key=lambda r: r["updated"], reverse=True)

    def refresh_panel(self) -> None:
        self.query_one(SessionPanel).refresh_sessions(
            self._panel_rows(), self._active_id, self._states(), self._spin())

    def action_toggle_panel(self) -> None:
        panel = self.query_one(SessionPanel)
        panel.toggle_class("-hidden")
        if panel.has_class("-hidden"):
            self.query_one("#prompt", PromptArea).focus()

    @on(SessionPanel.Resume)
    async def _on_panel_resume(self, msg: SessionPanel.Resume) -> None:
        await self._switch_to(msg.session_id)

    @on(SessionPanel.New)
    async def _on_panel_new(self, msg: SessionPanel.New) -> None:
        await self._cmd_clear("")

    @on(SessionPanel.Delete)
    def _on_panel_delete(self, msg: SessionPanel.Delete) -> None:
        run = self._runs.get(msg.session_id)
        if msg.session_id == self._active_id:
            self.notify("현재 보고 있는 세션은 삭제할 수 없습니다 (다른 세션으로 옮긴 뒤 지우세요).", severity="warning", timeout=5)
            return
        if run is not None and run.busy:
            self.notify("답변 중인 세션은 삭제할 수 없습니다.", severity="warning", timeout=4)
            return

        def done(ok) -> None:
            if not ok:
                return
            if run is not None:   # 열려 있던 세션이면 대화 영역까지 닫는다
                self._runs.pop(msg.session_id, None)
                run.conv.remove()
            if Session.delete(self._ws.root, msg.session_id):
                self.notify(f"세션 삭제됨: {msg.session_id}", timeout=3)
            self.refresh_panel()

        self.push_screen(ConfirmScreen("세션 삭제", f"세션 {msg.session_id} 의 저장 파일을 삭제합니다. 되돌릴 수 없습니다.", yes="삭제"), done)

    async def _switch_to(self, session_id: str) -> None:
        """세션 전환. 이미 열려 있으면 표시만 바꾸고(진행 중인 답변은 그대로 계속된다), 아니면 불러와 이력을 재생한다."""
        if session_id == self._active_id:
            self.query_one("#prompt", PromptArea).focus()
            return
        run = self._runs.get(session_id)
        if run is None:
            try:
                session = Session.load(self._ws.root, session_id)
            except FileNotFoundError:
                ids = ", ".join(Session.list_ids(self._ws.root)) or "없음"
                self.notify(f"세션 없음: {session_id} (목록: {ids})", severity="warning", timeout=6)
                return
            run = await self._mount_run(session)
        await self._activate(run)
        s = run.session
        self.notify(f"세션 전환 · {s.id} ({len(s.turns)}턴)" + (" · 답변 중" if run.busy else ""), timeout=3)

    async def _mount_run(self, session) -> SessionRun:
        run = self._new_run(session)
        await self.query_one("#conv_stack", Vertical).mount(run.conv)
        if session.history:
            await self._replay_history(run, session.history)
        return run

    async def _activate(self, run: SessionRun) -> None:
        self._active.conv.display = False
        self._active_id = run.session.id
        run.conv.display = True
        run.conv.anchor()
        prompt = self.query_one("#prompt", PromptArea)
        prompt.disabled = run.busy          # 답변 중인 세션에는 새 질의를 넣지 않는다(다른 세션에서는 가능)
        self._refresh_statusline()
        self._sync_activity()
        self.refresh_panel()
        if run.approval is not None:
            self._show_approval(run)        # 배경에서 밀린 승인 요청은 그 세션을 열 때 묻는다
        elif not run.busy:
            prompt.focus()

    # --- 활동바: 스피너 + 단계 + 경과 초 (+ reasoning 토큰) ---

    def _activity_on(self, run: SessionRun, label: str) -> None:
        run.turn_t0 = time.monotonic()
        run.think_tok = 0
        run.label = label
        run.detail = ""
        run.tools = Counter()   # 롤업 초기화 — 새 턴 시작 깜빡임·/compact 잔존 방지
        self._sync_activity()
        self._tick_activity()

    def _sync_activity(self) -> None:
        """활동바 표시와 타이머를 활성 세션·전체 실행 상태에 맞춘다. 배경 세션이 돌면 타이머는 계속 돈다(패널 스피너)."""
        self.query_one("#activity", Static).display = self._active.busy
        if any(r.busy for r in self._runs.values()):
            self._activity_timer.resume()
        else:
            self._activity_timer.pause()

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
        run = self._active
        if run.busy:
            elapsed = int(time.monotonic() - run.turn_t0)
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
            t.append(run.label or "작업 중", style="bold")
            rollup = self._rollup(run.tools).lstrip(" ·").strip()
            if rollup:                                   # 턴 툴 롤업 우선 (read×3 edit×2)
                t.append(f" · {rollup}", style="dim")
            elif run.detail:
                t.append(f" · {run.detail}", style="dim")
            t.append(f" · {self._fmt_elapsed(elapsed)}", style=f"bold {time_color}")
            if run.think_tok:
                t.append(f" · 생각 중 ~{run.think_tok} tok", style="dim italic")
            self.query_one("#activity", Static).update(t)
        # 패널의 세션별 상태(스피너·승인 대기)도 같은 주기로 갱신한다.
        self.query_one(SessionPanel).set_states(self._open_rows(), self._active_id, self._states(), self._spin())

    def on_unmount(self) -> None:
        self._activity_timer.stop()

    def _rollup(self, tools: Counter) -> str:
        """툴 호출 tally 를 'read×3 edit×2' 로 압축 (claude-statusbar frequency tally)."""
        if not tools:
            return ""
        return " · " + " ".join(f"{n}×{c}" if c > 1 else n for n, c in tools.items())

    def _spin(self) -> str:
        return _SPINNER[self._spin_i]

    # --- 자동완성: 커서 앞 @토큰(파일·폴더)이나 첫 단어 /명령에 맞는 후보를 입력창 위에 띄운다 ---

    def _paths(self) -> list[str]:
        if time.monotonic() - self._path_index_at > 3:
            self._path_index = index_paths(self._ws)
            self._path_index_at = time.monotonic()
        return self._path_index

    def _candidates(self, prompt: PromptArea) -> tuple[str, list[Option], tuple[int, int]]:
        """(토큰, 후보, 시작 위치). 후보의 id 는 선택 시 입력창에 들어갈 완성 텍스트다."""
        if token := prompt.at_token():
            opts = [Option(p, id="@" + p + ("" if p.endswith("/") else " ")) for p in match_paths(token[0], self._paths())]
            return token[0], opts, token[1]
        if token := prompt.slash_token():
            opts = [Option(Text.assemble((name, "bold"), "  ", (summary, "dim")), id=name + " ")
                    for name, (summary, _fn) in self._commands.items() if name.startswith(token[0]) and name != token[0]]
            return token[0], opts, token[1]
        return "", [], (0, 0)

    @on(TextArea.Changed, "#prompt")
    @on(TextArea.SelectionChanged, "#prompt")
    def _refresh_picker(self, event=None) -> None:
        prompt = self.query_one("#prompt", PromptArea)
        picker = self.query_one("#path_picker", OptionList)
        token, options, start = self._candidates(prompt)
        if not options or token == self._picker_dismissed:
            picker.display = prompt.picker_open = False
            return
        self._picker_dismissed = None
        self._picker_start = start
        picker.clear_options()
        picker.add_options(options)
        picker.highlighted = 0
        picker.display = prompt.picker_open = True

    @on(PromptArea.PickerNav)
    def _on_picker_nav(self, msg: PromptArea.PickerNav) -> None:
        prompt = self.query_one("#prompt", PromptArea)
        picker = self.query_one("#path_picker", OptionList)
        if msg.key == "up":
            picker.action_cursor_up()
        elif msg.key == "down":
            picker.action_cursor_down()
        elif msg.key == "escape":
            self._picker_dismissed = self._candidates(prompt)[0]
            picker.display = prompt.picker_open = False
        elif picker.highlighted is not None:
            prompt.complete(self._picker_start, str(picker.get_option_at_index(picker.highlighted).id))

    # --- 입력/명령 ---

    @on(PromptArea.Submitted)
    async def _on_submit(self, event: PromptArea.Submitted) -> None:
        line = event.value.strip()
        if not line or self._active.busy or self._close_requested:
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
        # /clear: 새 세션을 열고 그쪽으로 전환한다. 이전 세션은 패널에 그대로 남아 오갈 수 있다.
        try:
            self._active.runner.save_session()
        except Exception as e:
            self.notify(f"이전 세션 저장 실패({type(e).__name__})", severity="warning", timeout=5)
        run = await self._mount_run(Session(self._ws.root))
        await self._activate(run)
        self.notify(f"새 스레드 시작 · 세션 {run.session.id}", timeout=4)

    async def _cmd_resume(self, arg: str) -> None:
        # 인자가 있으면 바로, 없으면 목록 modal 에서 고른다. 선택 결과는 콜백으로 받아 그 세션으로 전환한다.
        if arg:
            await self._switch_to(arg)
            return
        sessions = [s for s in self._panel_rows() if s["id"] != self._active_id]
        if not sessions:
            self.notify("열 수 있는 다른 세션이 없습니다.", timeout=4)
            return

        def picked(session_id) -> None:
            if session_id:
                self.call_later(self._switch_to, session_id)

        self.push_screen(SessionPicker(sessions), picked)

    async def _replay_history(self, run: SessionRun, history: list) -> None:
        """저장된 user/assistant 이력을 그 세션의 대화 영역에 다시 그린다 — 이어서 대화할 맥락을 화면에도 보여준다."""
        for m in history:
            content = m.get("content") or ""
            if m.get("role") == "user":
                await run.conv.mount(Static(f"» {content}", classes="user-turn", markup=False))
            else:
                await run.conv.mount(Markdown(content, classes="answer"))
        run.conv.anchor()

    async def _cmd_rename(self, arg: str) -> None:
        s = self.runner.session
        if not arg:
            self.notify(f"현재 세션 이름: {s.title or '(없음)'} — /rename <이름> 으로 바꿉니다.", timeout=5)
            return
        s.title = " ".join(arg.split())[:60]
        s.title_manual = True   # 수동 제목은 압축 갱신이 덮지 않는다
        if s.history:
            s.save()
        self.refresh_panel()
        self.notify(f"세션 이름: {s.title}", timeout=3)

    async def _cmd_memory(self, arg: str) -> None:
        self.notify(self._memory.read() or "(비어 있음)", title="메모리", timeout=10)

    async def _cmd_remember(self, arg: str) -> None:
        kind, note = parse_remember(arg)
        saved = self._memory.remember(note, kind)
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
        run = self._active
        run.busy = True
        self.query_one("#prompt", PromptArea).disabled = True
        self._refresh_statusline()
        self._activity_on(run, "문맥 압축")
        self.run_worker(run.runner.compact_now, thread=True, exclusive=True, group=f"turn-{run.session.id}")

    async def _start_turn(self, task: str) -> None:
        run = self._active
        run.busy = True
        run.failed = False
        self.query_one("#prompt", PromptArea).disabled = True
        run.draft = ""
        run.prose_stream = None
        run.prose_md = None       # 이전 턴의 단락이 기준점으로 남으면 새 턴의 사고가 지난 턴에 끼어든다
        run.cur_group = run.prose_group = run.cur_explore = None
        run.turn_widgets = []
        # 질의 Static 은 turn_widgets 에 안 넣는다 — retry 시 산문/툴만 폐기하고 질의는 보존.
        # 산문 세그먼트는 lazy mount(툴 직전 빈 세그먼트 방지).
        await run.conv.mount(Static(f"» {task}", classes="user-turn", markup=False))
        run.conv.anchor()
        self._refresh_statusline()
        self._activity_on(run, "시작")
        # 세션별 group — 다른 세션의 턴을 취소하지 않고 나란히 돈다.
        self.run_worker(partial(run.runner.run_turn, task), thread=True,
                        exclusive=True, group=f"turn-{run.session.id}")
        self.refresh_panel()

    def _end_turn(self, run: SessionRun | None = None) -> None:
        run = run or self._active
        run.busy = False
        # 산문 세그먼트 스트림은 _on_turn_done/_on_turn_failed 가 _close_prose 로 이미 닫았다(여긴 방어).
        run.prose_stream = None
        # 턴 종료 시 미완 스텝 정리. worker 가 툴 도중 죽으면(ToolEnd 미도착) 진행 상태가 영구히
        # 남는다. 성공 턴은 이미 비어 no-op.
        for run_id, step in run.tool_steps.items():
            self._finish_step(step, run_id, "(중단됨)", ok=False)
        run.tool_steps = {}
        run.approval = None
        self._refresh_statusline()
        if run is self._active:
            if isinstance(self.screen, ApprovalModal):
                # approver timeout(=deny) 으로 응답 없이 턴이 끝난 고아 modal 정리 — 늦은 resolve 는
                # 이미 반환된 worker 에 no-op.
                self.screen.dismiss(False)
            prompt = self.query_one("#prompt", PromptArea)
            prompt.disabled = False
            prompt.focus()
        self._sync_activity()
        self.refresh_panel()   # 턴 끝의 제목·마지막 답변 시각·상태를 패널에 반영

    # --- worker 메시지 핸들러 ---

    @on(TokenChunk)
    async def _on_token(self, msg: TokenChunk) -> None:
        run = self._run_of(msg)
        run.draft += msg.text
        run.think_tok = 0  # 본문 생성 시작 → reasoning 카운터 해제
        if run.prose_stream is None:
            await self._open_prose(run)   # lazy — 턴 시작·툴 직후 첫 토큰에 새 산문 세그먼트
        await run.prose_stream.write(msg.text)

    async def _open_prose(self, run: SessionRun) -> None:
        """새 산문 세그먼트(Markdown)를 mount 하고 스트림을 연다. 첫 토큰에 lazy 호출."""
        md = Markdown("", classes="answer")
        await run.conv.mount(md)
        run.turn_widgets.append(md)
        run.prose_md = md
        run.prose_stream = Markdown.get_stream(md)
        # 본문이 시작되면 스텝 구간이 끊긴다. 이 단락 도중 도착하는 사고는 단락 앞 그룹(prose_group)에 들어간다.
        run.prose_group = run.cur_group
        run.cur_group = run.cur_explore = None
        run.conv.anchor()

    async def _close_prose(self, run: SessionRun) -> None:
        """현재 산문 세그먼트 스트림을 닫는다(툴 블록 진입·턴 종료·retry 시)."""
        if run.prose_stream is None:
            return
        try:
            await run.prose_stream.stop()
        except asyncio.CancelledError:
            pass  # 스트림 태스크가 기동 전에 취소된 극단 케이스 방어 — 핸들러 전파 시 앱이 죽는다
        run.prose_stream = None
        run.prose_md = run.prose_group = None   # 닫힌 단락은 기준점이 아니다. 다음 사고는 그 뒤의 새 그룹에 온다

    @on(PhaseEvent)
    async def _on_phase(self, msg: PhaseEvent) -> None:
        run = self._run_of(msg)
        phase = msg.phase
        if phase.startswith("think:"):
            n = phase[6:]
            run.think_tok = int(n) if n.isdigit() else 0  # 활동바 전용
            return
        if phase.startswith("heartbeat:"):
            return  # 활동바 경과 초가 대체 — 로그·표시 불필요
        if phase.startswith("retry:"):
            await self._reset_turn(run)  # 1차 시도의 산문 세그먼트·툴 블록·매핑 전부 폐기
            reason = phase[6:]
            run.label = "재시도"
            self.notify(f"빈 응답 — 변형 재시도 ({reason})", severity="warning", timeout=4)
            return
        if phase in _PHASE_TUI:
            label, _theme = _PHASE_TUI[phase]
            run.label = label
            run.detail = ""
            return
        if phase.startswith("tool:"):
            name = phase[5:]
            run.detail = name
            run.tools[name] += 1  # 활동바 롤업 tally
            return
        if phase.startswith("mcp:"):
            self.notify(phase[4:], timeout=3)
            return
        # 자유 형식 (문맥 압축 중…, 메모리 갱신 중… 등)
        run.label = phase

    async def _reset_turn(self, run: SessionRun) -> None:
        """retry: 이번 시도의 산문 세그먼트·과정 그룹·매핑을 전부 폐기하고 재시작한다(질의 Static 보존). tool_steps 를 안 비우면 뒤늦은 ToolEnd 가 stale run_id 로 제거된 위젯에 매칭된다."""
        await self._close_prose(run)
        for w in run.turn_widgets:
            try:
                await w.remove()
            except Exception:
                pass  # 이미 제거됐거나 mount 전인 위젯 — 폐기 목적이므로 무시
        run.turn_widgets = []
        run.tool_steps = {}
        run.cur_group = run.prose_group = run.cur_explore = None
        run.draft = ""

    @on(TurnDone)
    async def _on_turn_done(self, msg: TurnDone) -> None:
        run = self._run_of(msg)
        result = msg.result
        final = (result.get("final") or "").strip()
        await self._close_prose(run)
        for w in run.turn_widgets:
            if isinstance(w, ProcessGroup):
                w.summarize()
        # 스트림 전면 유실일 때만 정본을 산문 세그먼트로 폴백 mount — 스트림된 세그먼트가 있으면
        # 통째 교체 시 멀티툴 턴의 중간 산문이 소실되므로 그대로 둔다.
        if final and not run.draft.strip():
            md = Markdown(final, classes="answer")
            await run.conv.mount(md)
            run.turn_widgets.append(md)
            run.conv.anchor()

        verdict = result.get("verdict")
        elapsed = int(time.monotonic() - run.turn_t0)
        # 경로(route)와 trace 주소는 진단용이라 답변 아래에 남기지 않는다. 필요하면 /status 와 langfuse 에서 본다.
        footer = Text()
        footer.append(f"✓ 완료 · {elapsed}초", style=f"bold {self._theme_color('success')}")
        if verdict:
            footer.append("  ·  ", style="dim")
            verdict_color = self._theme_color("success") if verdict == "pass" else self._theme_color("error")
            footer.append(f"verdict: {verdict}", style=f"bold {verdict_color}")
        if result.get("verify_detail"):   # 검증 상세는 우측 패널 대신 턴 footer 인라인 (패널 폐지)
            footer.append(f"\n판정: {result['verify_detail']}", style="dim")
        if result.get("empty_final"):
            reason = result.get("empty_reason") or "unknown"
            hint = "출력 예산 소진(재시도 권장)" if reason == "length_truncation" else "재시도 권장"
            footer.append(f"\n빈 응답 — {hint} (사유: {reason}) · 히스토리에 저장하지 않음",
                          style=self._theme_color("warning"))
            self.notify(f"빈 응답: {reason} · {hint} · 히스토리 미저장", severity="error", timeout=6)
        if not run.turn_widgets:   # 산문·툴 세그먼트가 하나도 없는 빈 턴 — 시각적 공백 방지
            await run.conv.mount(Static("(응답 없음)", classes="turn-footer", markup=False))
        await run.conv.mount(Static(footer, classes="turn-footer"))
        run.conv.anchor()
        if run is not self._active:
            self.notify(f"'{run.session.title or run.session.id}' 세션 답변 완료", timeout=4)
        self._end_turn(run)

    @on(TurnCancelled)
    async def _on_turn_cancelled(self, msg: TurnCancelled) -> None:
        run = self._run_of(msg)
        await self._close_prose(run)
        for w in run.turn_widgets:
            if isinstance(w, ProcessGroup):
                w.summarize()
        await run.conv.mount(Static(Text("⏹ 중단됨 · 이 턴은 히스토리에 남기지 않음", style=f"bold {self._theme_color('warning')}"), classes="turn-footer"))
        run.conv.anchor()
        self._end_turn(run)

    @on(TurnFailed)
    async def _on_turn_failed(self, msg: TurnFailed) -> None:
        run = self._run_of(msg)
        run.failed = True   # 패널에 ✕ 로 남는다. 다음 턴을 시작하면 지워진다
        await self._close_prose(run)
        err_color = f"bold {self._theme_color('error')}"
        await run.conv.mount(Static(Text(f"턴 실패: {msg.error}", style=err_color), classes="turn-footer"))
        run.conv.anchor()
        self.notify(f"턴 실패: {msg.error}", severity="error", timeout=8)
        self._end_turn(run)

    @on(CompactDone)
    async def _on_compact_done(self, msg: CompactDone) -> None:
        # /compact worker 완료 — busy 해제·입력 재활성(턴과 동일 경로) 후 결과 안내
        run = self._run_of(msg)
        run.ctx_used = 0   # 히스토리가 줄었으니 점유 최대값도 다시 센다
        self._end_turn(run)
        self.notify(msg.note, title="문맥 압축", timeout=5)

    # --- 과정 가시화 (ToolActivityHandler 콜백 → 그룹 안의 스텝 줄) ---

    async def _group(self, run: SessionRun) -> ProcessGroup:
        """현재 스텝 구간의 그룹. 없으면 산문을 닫고 대화 끝에 새로 만든다."""
        if run.cur_group is None or not run.cur_group.is_attached:
            await self._close_prose(run)
            run.cur_group = await self._mount_group(run)
        return run.cur_group

    async def _mount_group(self, run: SessionRun, before=None) -> ProcessGroup:
        group = ProcessGroup()
        await run.conv.mount(group, before=before)
        run.turn_widgets.append(group)   # retry 폐기 대상(스텝은 그룹과 함께 제거된다)
        run.conv.anchor()
        return group

    @on(ToolStart)
    async def _on_tool_start(self, msg: ToolStart) -> None:
        # 툴 진입 → 현재 산문 세그먼트를 닫아 다음 토큰이 새 세그먼트를 열게 한다(시간순 인터리브).
        run = self._run_of(msg)
        group = await self._group(run)
        # mount(await) 전에 매핑에 넣는다. 그 사이 턴이 끝나면 _end_turn 의 미완 스텝 정리가 이 스텝을 놓친다.
        if msg.target is None:
            run.cur_explore = None      # 편집·실행 도구는 탐색 병합을 끊는다
            step = StepLine(msg.title, msg.name)
            run.tool_steps[msg.run_id] = step
            await group.add(step)
        else:
            fresh = run.cur_explore is None or not run.cur_explore.is_attached or run.cur_explore.full
            if fresh:
                run.cur_explore = ExploreStep()
            run.cur_explore.add(msg.run_id, *msg.target)
            run.tool_steps[msg.run_id] = run.cur_explore
            if fresh:
                await group.add(run.cur_explore)
        run.conv.anchor()

    @on(ReasoningEnd)
    async def _on_reasoning_end(self, msg: ReasoningEnd) -> None:
        # 사고는 그 호출의 본문보다 먼저 일어났으므로, 산문 세그먼트가 흐르는 중이면 그 앞 그룹에 넣는다.
        run = self._run_of(msg)
        run.cur_explore = None      # 새 LLM 라운드. 다음 탐색이 이전 라운드의 줄에 섞이면 순서가 뒤틀린다
        if not self._thinking_visible:
            return
        if run.prose_md is not None and run.prose_md.is_attached:
            if run.prose_group is None or not run.prose_group.is_attached:
                run.prose_group = await self._mount_group(run, before=run.prose_md)
            group = run.prose_group
        else:
            group = await self._group(run)
        await group.add(ReasoningStep(msg.text))
        run.conv.anchor()

    @on(ToolEnd)
    async def _on_tool_end(self, msg: ToolEnd) -> None:
        run = self._run_of(msg)
        step = run.tool_steps.pop(msg.run_id, None)
        if step is None:
            return
        self._finish_step(step, msg.run_id, msg.output, msg.ok)
        if not msg.ok:
            run.cur_explore = None      # 실패가 섞이면 다음 탐색은 새 줄
            return
        if msg.name in DIFF_TOOLS:
            summary, _, body = msg.output.partition("\n")
            if body.startswith("@@"):
                await self._mount_diff(run, summary, body)

    async def _mount_diff(self, run: SessionRun, title: str, diff: str) -> None:
        """편집 diff 를 과정 그룹 밖 대화에 붙인다. 결과이므로 턴이 끝나 그룹이 접혀도 그대로 보여야 한다."""
        block = DiffBlock(title, diff)
        await run.conv.mount(block)
        run.turn_widgets.append(block)
        run.cur_group = run.cur_explore = None   # 이후 스텝은 diff 아래 새 그룹으로 (시간순 유지)
        run.conv.anchor()

    @staticmethod
    def _finish_step(step, run_id: str, output: str, ok: bool) -> None:
        if isinstance(step, ExploreStep):
            step.finish_one(run_id, output, ok)
        else:
            step.finish(output, ok)

    @on(ApprovalRequest)
    def _on_approval(self, msg: ApprovalRequest) -> None:
        run = self._run_of(msg)
        run.approval = msg
        if run is self._active:
            self._show_approval(run)
        else:
            # 배경 세션의 승인은 그 세션을 열 때 묻는다 — 보고 있던 대화 위로 모달이 튀어나오지 않게 한다.
            self.notify(f"'{run.session.title or run.session.id}' 세션이 '{msg.tool}' 승인을 기다립니다.",
                        severity="warning", timeout=8)

    def _show_approval(self, run: SessionRun) -> None:
        msg = run.approval
        if msg is None:
            return
        # 라벨은 툴 롤업과 무관하게 항상 노출되므로 승인 대기를 라벨로 띄운다(detail 은 롤업에
        # 가려 안 보임). 모달 종료 시 직전 라벨로 복원한다.
        prev_label = run.label
        run.label = "승인 대기"

        def resolve(ok) -> None:
            run.approval = None
            run.label = prev_label
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
        self._begin_close()

    def action_ctrl_c(self) -> None:
        # 선택이 있으면 복사. 아니면 1회: 보고 있는 세션의 답변 중단(대기 중이면 종료 예고), 2초 안에 2회: 종료.
        if self._copy_selection():
            self.screen.clear_selection()
            return
        now = time.monotonic()
        again = now - self._ctrl_c_at < _CTRL_C_WINDOW_S
        self._ctrl_c_at = now
        if again:
            self._force_exit()
            return
        run = self._active
        if run.busy and run.runner.cancel_turn():
            if isinstance(self.screen, ApprovalModal):
                self.screen.dismiss(False)   # 승인 대기로 막힌 루프를 풀어야 취소가 처리된다
            run.label = "중단 중"
            self.notify("답변을 중단합니다. 바로 한 번 더 누르면 koda 를 종료합니다.", timeout=3)
            return
        self.notify("한 번 더 Ctrl+C 를 누르면 종료합니다 (Ctrl+Q 는 바로 저장 후 종료).", timeout=2)

    def _force_exit(self) -> None:
        """Ctrl+C 연타. 진행 중인 턴은 모두 끊고 열린 세션을 저장한 뒤 바로 나간다."""
        saved = False
        for run in self._runs.values():
            run.runner.cancel_turn()
            try:
                saved = run.runner.save_session() or saved
            except Exception:
                pass
        self.exit(saved)

    def _begin_close(self) -> None:
        if self._close_requested:
            return
        busy = [r.session.id for r in self._runs.values() if r.busy]
        if busy:
            self.notify(f"턴 실행 중 — 완료 후 종료할 수 있습니다 ({', '.join(busy)})", severity="warning")
            return
        self._close_requested = True
        self.query_one("#prompt", PromptArea).disabled = True
        self._refresh_statusline()
        self._activity_on(self._active, "세션 저장·메모리 갱신")
        self.run_worker(self._close_all_sessions, thread=True, exclusive=True, group="close")

    def _close_all_sessions(self) -> None:
        """/exit·Ctrl+Q: 열려 있는 세션을 모두 저장한다(worker thread). 메모리는 턴마다 이미 반영됐다."""
        saved = False
        for run in list(self._runs.values()):
            try:
                saved = run.runner.save_session() or saved
            except Exception as e:
                # 저장 실패로 SessionClosed 가 누락되면 앱이 종료 못 한다 — 항상 post 로 이어간다
                self.post_message(PhaseEvent(f"세션 저장 실패({type(e).__name__})"))
        self.post_message(SessionClosed(self._active_id, saved))

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

    # MCP 는 화면이 뜬 뒤 백그라운드로 세션당 1회 붙는다(로그는 stderr 가 아니라 /mcp 화면·상태줄).
    dirs = None if args.no_mcp else mcp_dirs(ws.root, args.mcps_dir)

    # 설정은 앱이 들고 다닌다(/model 이 교체). statusline 모델명은 PRIMARY 가 아니라 실제로 선택된 provider 기준.
    settings, model_name = None, ""
    try:
        from ..config import load_model_settings
        settings = load_model_settings()
        provider, _ = select_provider(settings)
        model_name = settings.local_model if provider == "local" else settings.openai_model
    except Exception:
        pass

    app = KodaTui(ws, session, memory, dirs, auto_yes=args.yolo, model_name=model_name, settings=settings)
    app.run()
    final = app.runner.session   # /clear 로 세션이 교체됐을 수 있으므로 최종 세션 기준
    if final.history:
        _err(f"세션 저장됨: {final.id}  (재개: koda 실행 후 /resume {final.id})")
    return 0
