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
from ..config import switch_model
from ..llm import select_provider
from ..mcp import connect_mcp, connect_server, disconnect_server, mcp_dirs
from ..memory import Memory, parse_remember
from ..session import Session
from ..workspace import Workspace
from .paths import index_paths, match_paths
from .model import (
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
    ExploreStep,
    InfoScreen,
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
    #conversation { width: 1fr; height: 1fr; padding: 0 1; }
    #prompt { height: auto; max-height: 10; }
    #path_picker { height: auto; max-height: 12; border: none; background: $panel; padding: 0 1; }
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
    McpScreen { align: center middle; }
    #mcp_dialog { width: 90%; max-width: 110; height: auto; max-height: 80%; border: thick $primary;
                  background: $surface; padding: 1 2; }
    #mcp_title { margin-bottom: 1; }
    #mcp_list { height: auto; max-height: 10; }
    #mcp_detail { color: $text-muted; margin-top: 1; height: auto; max-height: 14; }
    #mcp_dirs { color: $text-muted; margin-top: 1; }
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
        self.runner = TuiTurnRunner(self, ws, session, memory, [], auto_yes,
                                    run_task_fn=run_task_fn, approval_timeout=approval_timeout, settings=settings)
        self._model_name = model_name
        self._mode = "auto" if auto_yes else "manual"  # 승인 모드 뱃지(statusline)
        self._ctrl_c_at = 0.0                          # 마지막 Ctrl+C 시각(연타 판정)
        self._ctx_used = 0                             # 최근 LLM 호출의 프롬프트+출력 토큰 = 현재 컨텍스트 점유 근사값
        self._session_tokens = [0, 0]                  # 세션 누적 입력/출력 토큰 (/status)
        # MCP 는 화면이 뜬 뒤 백그라운드로 붙는다(None 이면 --no-mcp). 결과는 /mcp 화면과 상태줄에 보인다.
        self._mcp_dirs = mcp_dirs
        self._mcp: list = []
        self._mcp_connecting = bool(mcp_dirs)
        self._status = "대기 중"
        self._path_index: list[str] = []      # @경로 후보용 워크스페이스 색인 (3초마다 갱신)
        self._path_index_at = 0.0
        self._picker_dismissed: str | None = None  # Esc 로 닫은 토큰(@경로·/명령). 같은 토큰이면 다시 띄우지 않는다
        self._picker_start = (0, 0)               # 선택 시 바꿀 구간의 시작
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

    # --- 레이아웃 ---

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield SessionPanel(id="session_panel")
            with Vertical(id="main"):
                yield VerticalScroll(id="conversation")
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
        # maximize 는 한 위젯으로 화면을 덮어 대화·입력·상태줄을 가린다 — koda 는 이 넷이 한 화면에
        # 공존하는 단일 뷰라, 어떤 위젯(입력창·툴 블록 등)을 최대화해도 답변이 안 보이는 깨진 상태가
        # 된다. 그래서 커맨드 팔레트에서 maximize 명령을 통째로 뺀다(#21). maximize 의 유일한 트리거다.
        for command in super().get_system_commands(screen):
            if command.callback == screen.action_maximize:
                continue
            yield command

    # --- statusline: 모델 · 승인 모드 · MCP · 컨텍스트 잔량 · 상태 ---

    @property
    def _ctx_budget(self) -> int:
        return int(getattr(self.runner.settings, "context_budget_tokens", 64000) or 64000)

    @staticmethod
    def _k(n: int) -> str:
        return f"{n / 1000:.1f}k" if n < 10_000 else f"{n // 1000}k"

    def _statusline_text(self, status: str = "대기 중") -> Text:
        t = Text()
        t.append(self._model_name or "모델 미정", style="bold")
        t.append(f" · {self._mode}")
        if self._mcp_dirs:
            t.append(f" · {self._mcp_text()}")
        budget = self._ctx_budget
        left = max(0.0, 1 - self._ctx_used / budget)
        color = self._theme_color("success") if left > 0.5 else self._theme_color("warning") if left > 0.2 else self._theme_color("error")
        t.append(" · 컨텍스트 ")
        t.append(f"{self._k(self._ctx_used)}/{self._k(budget)} · {left:.0%} 남음", style=f"bold {color}")
        t.append(f" · {status}")
        return t

    def _mcp_text(self) -> str:
        if self._mcp_connecting:
            return "mcp 연결 중"
        failed = sum(1 for s in self._mcp if s.error and s.enabled)
        return f"mcp {len(self.runner.mcp_tools)}" + (f" (실패 {failed})" if failed else "")

    def _set_status(self, status: str) -> None:
        self._status = status
        self.query_one("#statusline", Static).update(self._statusline_text(status))

    @on(UsageUpdate)
    def _on_usage(self, msg: UsageUpdate) -> None:
        self._ctx_used = msg.input_tokens + msg.output_tokens
        self._session_tokens[0] += msg.input_tokens
        self._session_tokens[1] += msg.output_tokens
        self._set_status(self._status)

    # --- /status · /model ---

    def _active_model(self) -> tuple[str, str, str]:
        """(provider, 모델명, base_url). 설정이 없으면 표시용 이름만."""
        st = self.runner.settings
        if st is None:
            return "", self._model_name, ""
        if st.primary == "local":
            return "local", st.local_model, st.local_base_url
        return st.provider_name, st.openai_model, st.openai_base_url

    async def _cmd_status(self, arg: str) -> None:
        st, s = self.runner.settings, self.runner.session
        provider, model, base_url = self._active_model()
        mcp = ", ".join(f"{x.name}({'꺼짐' if not x.enabled else '실패' if x.error else len(x.tools)})" for x in self._mcp) or ("연결 중" if self._mcp_connecting else "없음")
        used_in, used_out = self._session_tokens
        lines = [
            f"세션        {s.id} · {len(s.turns)}턴" + (f" · {s.title}" if s.title else ""),
            f"모델        {model} ({provider or '?'}) · {base_url}",
            f"추론        style={getattr(st, 'reasoning_style', '?')} effort={getattr(st, 'reasoning_effort', '?')} · 구조화={getattr(st, 'structured_method', '?')}"
            + (" · Responses API" if getattr(st, "use_responses_api", False) else ""),
            f"컨텍스트    {self._k(self._ctx_used)} / {self._k(self._ctx_budget)} (최근 호출 기준) · 세션 누적 입력 {self._k(used_in)} 출력 {self._k(used_out)}",
            f"승인        {self._mode}",
            f"MCP         {mcp}",
            f"워크스페이스 {self.runner._ws.root}",
            f"메모리      {sum(len(self.runner.memory.items(k)) for k in ('semantic', 'episodic', 'procedural'))}개 항목",
        ]
        self.push_screen(InfoScreen("상태  (ESC 닫기)", "\n".join(lines)))

    async def _cmd_model(self, arg: str) -> None:
        # 인자 없음: provider 선택 → 모델명·base_url·키 입력 창. 인자 있음: 한 줄 단축 문법.
        usage = "사용법: /model (대화형) 또는 /model <모델명> [provider=local|openai|deepseek] [base_url=…] [key=…]"
        if self.runner.settings is None:
            self.notify("설정을 읽지 못해 전환할 수 없습니다(.env 확인).", severity="warning", timeout=5)
            return
        if not arg:
            self.push_screen(ModelScreen(self.runner.settings), self._apply_model_form)
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
            new = switch_model(self.runner.settings, model, provider=provider, base_url=base_url, api_key=api_key)
        except Exception as e:
            self.notify(f"전환 실패: {e}", severity="error", timeout=6)
            return
        self.runner.settings = new
        self._ctx_used = 0
        self._model_name = new.local_model if new.primary == "local" else new.openai_model
        self._set_status(self._status)
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
            servers = asyncio.run(connect_mcp(dirs, cwd=self.runner._ws.root))
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
            self.screen.refresh_servers(connecting=False)

    def _apply_mcp(self) -> None:
        """켜져 있고 연결된 서버의 도구만 다음 턴부터 쓴다."""
        self.runner.mcp_tools = [t for s in self._mcp for t in s.active_tools]
        self._set_status(self._status)

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

        self.push_screen(McpScreen(self._mcp, list(self._mcp_dirs or []), self._mcp_connecting, toggle, reconnect))

    def _theme_color(self, name: str) -> str:
        """테마 색상 hex 조회. 라이트/다크 자동 적응."""
        return self.theme_variables.get(name, name)

    # --- 세션 패널: 목록 · 현재 세션 상태 표시 · 재개/새로/삭제 ---

    def _panel_state(self) -> str:
        if isinstance(self.screen, ApprovalModal):
            return "approval"
        return "running" if self._busy else "idle"

    def _current_row(self) -> dict:
        """디스크를 읽지 않고 현재 세션의 패널 항목을 만든다 (스피너 틱마다 호출)."""
        s = self.runner.session
        return {"id": s.id, "title": s.title, "turns": len(s.turns),
                "first_task": s.turns[0]["task"] if s.turns else "", "updated": time.time()}

    def _session_rows(self) -> list[dict]:
        """저장된 세션 목록에 아직 저장 전인 현재 세션을 얹고, 현재 세션의 제목·턴 수는 메모리 값으로 맞춘다."""
        rows = Session.summaries(self.runner._ws.root)
        s = self.runner.session
        for r in rows:
            if r["id"] == s.id:
                r["title"] = s.title
                r["turns"] = len(s.turns)
                break
        else:
            rows.insert(0, self._current_row())
        return rows

    def refresh_panel(self) -> None:
        self.query_one(SessionPanel).refresh_sessions(
            self._session_rows(), self.runner.session.id, self._panel_state(), self._spin())

    def action_toggle_panel(self) -> None:
        panel = self.query_one(SessionPanel)
        panel.toggle_class("-hidden")
        if panel.has_class("-hidden"):
            self.query_one("#prompt", PromptArea).focus()

    @on(SessionPanel.Resume)
    async def _on_panel_resume(self, msg: SessionPanel.Resume) -> None:
        if msg.session_id == self.runner.session.id:
            self.query_one("#prompt", PromptArea).focus()
            return
        if self._busy:
            self.notify("턴 진행 중 — 끝난 뒤 전환할 수 있습니다.", severity="warning", timeout=4)
            return
        await self._resume_session(msg.session_id)

    @on(SessionPanel.New)
    async def _on_panel_new(self, msg: SessionPanel.New) -> None:
        if self._busy:
            self.notify("턴 진행 중 — 끝난 뒤 만들 수 있습니다.", severity="warning", timeout=4)
            return
        await self._cmd_clear("")
        self.query_one("#prompt", PromptArea).focus()

    @on(SessionPanel.Delete)
    def _on_panel_delete(self, msg: SessionPanel.Delete) -> None:
        if msg.session_id == self.runner.session.id:
            self.notify("현재 세션은 삭제할 수 없습니다 (새 세션으로 옮긴 뒤 지우세요).", severity="warning", timeout=4)
            return

        def done(ok) -> None:
            if ok and Session.delete(self.runner._ws.root, msg.session_id):
                self.notify(f"세션 삭제됨: {msg.session_id}", timeout=3)
            self.refresh_panel()

        self.push_screen(ConfirmScreen("세션 삭제", f"세션 {msg.session_id} 의 저장 파일을 삭제합니다. 되돌릴 수 없습니다.", yes="삭제"), done)

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
        # 세션 패널의 현재 항목 상태(스피너·승인 대기)도 같은 주기로 갱신한다.
        self.query_one(SessionPanel).set_current(self._current_row(), self._panel_state(), self._spin())

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
            self._path_index = index_paths(self.runner._ws)
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
            self._ctx_used = 0
            await self.query_one("#conversation", VerticalScroll).remove_children()
            self._activity_off()
            self._set_status("대기 중")
            self.notify(f"새 스레드 시작 · 세션 {new_id}", timeout=4)
        finally:
            self._busy = False
        self.refresh_panel()

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
        self.refresh_panel()

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
        self.refresh_panel()   # 턴 끝의 제목 확정·상태 복귀를 패널에 반영

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

    @on(TurnCancelled)
    async def _on_turn_cancelled(self, msg: TurnCancelled) -> None:
        await self._close_prose()
        for w in self._turn_widgets:
            if isinstance(w, ProcessGroup):
                w.summarize()
        conv = self.query_one("#conversation", VerticalScroll)
        await conv.mount(Static(Text("⏹ 중단됨 · 이 턴은 히스토리에 남기지 않음", style=f"bold {self._theme_color('warning')}"), classes="turn-footer"))
        conv.anchor()
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
        # mount(await) 전에 매핑에 넣는다. 그 사이 턴이 끝나면 _end_turn 의 미완 스텝 정리가 이 스텝을 놓친다.
        if msg.target is None:
            self._cur_explore = None      # 편집·실행 도구는 탐색 병합을 끊는다
            step = StepLine(msg.title, msg.name)
            self._tool_steps[msg.run_id] = step
            await group.add(step)
        else:
            fresh = self._cur_explore is None or not self._cur_explore.is_attached or self._cur_explore.full
            if fresh:
                self._cur_explore = ExploreStep()
            self._cur_explore.add(msg.run_id, *msg.target)
            self._tool_steps[msg.run_id] = self._cur_explore
            if fresh:
                await group.add(self._cur_explore)
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
        self._begin_close()

    def action_ctrl_c(self) -> None:
        # 선택이 있으면 복사. 아니면 1회: 답변 중단(대기 중이면 종료 예고), 2초 안에 2회: 종료.
        if self._copy_selection():
            self.screen.clear_selection()
            return
        now = time.monotonic()
        again = now - self._ctrl_c_at < _CTRL_C_WINDOW_S
        self._ctrl_c_at = now
        if again:
            self._force_exit()
            return
        if self._busy and self.runner.cancel_turn():
            if isinstance(self.screen, ApprovalModal):
                self.screen.dismiss(False)   # 승인 대기로 막힌 루프를 풀어야 취소가 처리된다
            self._activity_label = "중단 중"
            self.notify("답변을 중단합니다. 바로 한 번 더 누르면 koda 를 종료합니다.", timeout=3)
            return
        self.notify("한 번 더 Ctrl+C 를 누르면 종료합니다 (Ctrl+Q 는 바로 저장 후 종료).", timeout=2)

    def _force_exit(self) -> None:
        """Ctrl+C 연타. 진행 중인 턴은 끊고 세션을 저장한 뒤 바로 나간다."""
        self.runner.cancel_turn()
        saved = False
        try:
            if self.runner.session.history:
                self.runner.session.save()
                saved = True
        except Exception:
            pass
        self.exit(saved)

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

    # MCP 는 화면이 뜬 뒤 백그라운드로 세션당 1회 붙는다(로그는 stderr 가 아니라 /mcp 화면·상태줄).
    dirs = None if args.no_mcp else mcp_dirs(ws.root, args.mcps_dir)

    # 설정은 세션이 들고 다닌다(/model 이 교체). statusline 모델명은 PRIMARY 가 아니라 실제로 선택된 provider 기준.
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
