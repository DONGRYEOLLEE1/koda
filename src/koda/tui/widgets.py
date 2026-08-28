from __future__ import annotations

import re
import time

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from .model import DIFF_TOOLS

_PREVIEW_LIMIT = 500
_SECRET_KEY = re.compile(r"(?i)token|secret|key|password|authorization|credential")


class PromptArea(TextArea):
    """질의 입력창(다중행). Enter=제출, 줄바꿈=Alt+Enter·Shift+Enter·Ctrl+J, 내용에 따라 자동 성장하며 높이는 CSS max-height 로 상한(초과분은 내부 스크롤).

    Ctrl+J(LF)는 Enter(CR)와 항상 구분돼 전달되므로, Alt+Enter 가 평범한 Enter 로 합쳐져 버리는 터미널(향상된 키보드 프로토콜 미지원)에서도 동작하는 줄바꿈 폴백이다.
    """

    _NEWLINE_KEYS = ("alt+enter", "shift+enter", "ctrl+j")

    _PICKER_KEYS = ("up", "down", "tab", "enter", "escape")

    class Submitted(Message):
        """Enter 제출. value 는 개행 포함 원문."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    class PickerNav(Message):
        """후보 목록(@경로·/명령)이 열려 있을 때의 이동·선택·닫기 키."""

        def __init__(self, key: str) -> None:
            self.key = key
            super().__init__()

    picker_open = False   # 앱이 후보 목록을 띄우는 동안 True. 이때 방향키·Tab·Enter·Esc 는 목록으로 간다

    def slash_token(self) -> tuple[str, tuple[int, int]] | None:
        """첫 줄이 `/` 로 시작하고 커서가 그 첫 단어 안에 있으면 (입력 중인 명령, 시작 위치)."""
        row, col = self.cursor_location
        head = self.document.get_line(0)[:col]
        if row != 0 or not head.startswith("/") or any(c.isspace() for c in head):
            return None
        return head, (0, 0)

    def at_token(self) -> tuple[str, tuple[int, int]] | None:
        """커서 앞의 `@질의`. (질의, @ 위치) 를 돌려주고 없으면 None. `@` 는 줄 처음이나 공백 뒤에만 인정한다."""
        row, col = self.cursor_location
        line = self.document.get_line(row)[:col]
        i = line.rfind("@")
        if i < 0 or (i > 0 and not line[i - 1].isspace()):
            return None
        query = line[i + 1:]
        if any(c.isspace() for c in query):
            return None
        return query, (row, i)

    def complete(self, start: tuple[int, int], text: str) -> None:
        """start 부터 커서까지를 text 로 바꾼다(후보 선택)."""
        self.replace(text, start, self.cursor_location)

    async def _on_key(self, event) -> None:
        if self.picker_open and event.key in self._PICKER_KEYS:
            event.stop()
            event.prevent_default()
            self.post_message(self.PickerNav(event.key))
            return
        # Enter 는 TextArea 기본 동작(개행 삽입) 대신 제출로 가로챈다. 줄바꿈은 _NEWLINE_KEYS.
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        if event.key in self._NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)


_TITLE_LIMIT = 110      # 스텝 제목 한 줄 상한. 넘치면 … 으로 끊고 전체는 본문에 둔다
_SUMMARY_LIMIT = 60
_SENTENCE_LIMIT = 80
MAX_EXPLORE = 32        # 한 탐색 줄에 병합하는 최대 건수
_EXIT = re.compile(r"^exit_code=(\d+)\n")


def _cut(text: str, limit: int) -> str:
    return text[:limit] + "…" if len(text) > limit else text


def first_line(output: str, limit: int = _SUMMARY_LIMIT) -> str:
    """출력 요약 한 줄. 셸 출력은 exit_code 줄 대신 stdout 첫 줄을 쓰고, 실패면 exit_code 를 앞세운다."""
    m = _EXIT.match(output)
    body = output[m.end():] if m else output
    lines = [" ".join(ln.split()) for ln in body.splitlines() if ln.strip() and ln not in ("stdout:", "stderr:")]
    head = lines[0] if lines else ""
    if m and m.group(1) != "0":
        head = f"exit_code={m.group(1)}" + (f" · {head}" if head else "")
    return _cut(head, limit)


def first_sentence(text: str, limit: int = _SENTENCE_LIMIT) -> str:
    """사고 원문의 첫 문장(마침표·줄바꿈 기준). 모델 언어를 바꾸지 않는다."""
    head = re.split(r"(?<=[.。!?])\s|\n", text.strip(), maxsplit=1)[0].strip()
    return _cut(head, limit)


class StepLine(Collapsible):
    """과정 한 줄. `⏺ 제목 ⎿ 요약` 이고 클릭하면 출력 전체. 상태(진행·성공·실패)는 CSS 클래스로 색만 바꾼다."""

    def __init__(self, title: str, name: str = "", glyph: str = "⏺") -> None:
        self._label = title
        self._tool_name = name   # DOMNode 가 _name 을 위젯 name 으로 예약하므로 충돌 회피
        self._glyph = glyph
        self._summary = ""
        self._body = Static("실행 중…", markup=False)
        super().__init__(self._body, title="", collapsed=True, collapsed_symbol="", expanded_symbol="", classes="-run")
        self._refresh_title()

    def _refresh_title(self) -> None:
        text = f"{self._glyph} {_cut(self._label, _TITLE_LIMIT)}"
        if self._summary:
            text += f"  ⎿ {self._summary}"
        self.title = Content(text)   # 경로의 대괄호가 마크업으로 먹히지 않게 평문으로

    def set_state(self, ok: bool | None) -> None:
        self.remove_class("-run", "-ok", "-fail")   # set_classes 는 Collapsible 의 -collapsed 까지 지운다
        self.add_class("-run" if ok is None else ("-ok" if ok else "-fail"))

    def finish(self, output: str, ok: bool) -> None:
        self.set_state(ok)
        self._summary = first_line(output)
        self._refresh_title()
        summary, _, body = output.partition("\n")
        if self._tool_name in DIFF_TOOLS and body.startswith("@@"):
            # 편집 성공: 요약줄은 평문(Text), diff 본문만 Syntax("diff")로 토큰 채색(전체 줄 배경 X — solid-블록 회피).
            self._body.update(Group(Text(summary), Syntax(body, "diff", theme="ansi_dark", word_wrap=False)))
        else:
            self._body.update(output or "(출력 없음)")


class ExploreStep(StepLine):
    """연속 탐색(읽기·목록·검색·조사·문서·웹)을 한 줄에 누적한다. 제목은 ToolStart 순, 출력은 run_id 로 제자리에 넣는다."""

    def __init__(self) -> None:
        self._entries: list[tuple[str, str, str]] = []   # (run_id, 종류, 대상)
        self._outputs: dict[str, tuple[str, bool]] = {}
        super().__init__("탐색")

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def full(self) -> bool:
        return self.count >= MAX_EXPLORE

    def add(self, run_id: str, kind: str, target: str) -> None:
        self._entries.append((run_id, kind, target))
        self.set_state(None)
        self._label = "탐색 · " + " · ".join(self._segments())
        self._refresh_title()

    def _segments(self) -> list[str]:
        """같은 종류가 이어지면 대상을 모아 `a, b 읽음` 꼴로 합친다."""
        out: list[str] = []
        for _, kind, target in self._entries:
            if kind in ("읽음", "목록", "검색") and out and out[-1].endswith(f" {kind}"):
                out[-1] = out[-1][: -len(kind) - 1] + f", {target} {kind}"
            elif kind in ("읽음", "목록", "검색"):
                out.append(f"{target} {kind}")
            elif kind == "조사":
                out.append(f"$ {target}")
            else:
                out.append(f"{kind} {target}")
        return out

    def finish_one(self, run_id: str, output: str, ok: bool) -> None:
        self._outputs[run_id] = (output, ok)
        if len(self._outputs) >= self.count:
            self.set_state(all(v[1] for v in self._outputs.values()))
        if not ok:
            self.set_state(False)
        parts = []
        for rid, kind, target in self._entries:
            body, _ = self._outputs.get(rid, ("실행 중…", True))
            parts.append(f"--- {kind} {target}\n{body or '(출력 없음)'}")
        self._body.update("\n".join(parts))


class ReasoningStep(StepLine):
    """LLM 호출 하나의 사고. 제목은 첫 문장(흐린 이탤릭), 클릭하면 원문."""

    def __init__(self, text: str) -> None:
        super().__init__(first_sentence(text) or "사고", glyph="∙")
        self.set_state(True)
        self._body.update(text)


class ProcessGroup(Collapsible):
    """한 턴의 연속 스텝 묶음. 진행 중엔 펼쳐 두고, 턴이 끝나면 summarize 로 요약 제목 한 줄로 접는다."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        super().__init__(title="과정", collapsed=False, collapsed_symbol="▸", expanded_symbol="▾")

    async def add(self, step: StepLine) -> None:
        await self.query_one(Collapsible.Contents).mount(step)

    def summarize(self) -> None:
        counts = {"사고": 0, "탐색": 0, "편집": 0, "실행": 0, "도구": 0}
        for step in self.query(StepLine):
            if isinstance(step, ReasoningStep):
                counts["사고"] += 1
            elif isinstance(step, ExploreStep):
                counts["탐색"] += step.count
            elif step._tool_name in DIFF_TOOLS or step._tool_name in ("write_file", "create_dir", "delete_path"):
                counts["편집"] += 1
            elif step._tool_name == "run_command":
                counts["실행"] += 1
            else:
                counts["도구"] += 1
        parts = [f"{k} {n}" for k, n in counts.items() if n]
        elapsed = int(time.monotonic() - self._t0)
        self.title = " · ".join(["과정", *parts, f"{elapsed}s"])
        self.collapsed = True


def _args_preview(tool: str, args: object) -> str:
    """args 요약 — 길이 제한 + secret 성 키 redaction, raw dump 금지."""
    if tool == "run_command" and isinstance(args, dict):
        v = args.get("argv") or args.get("command") or ""
        text = "$ " + (" ".join(map(str, v)) if isinstance(v, list) else str(v))
    else:
        if isinstance(args, dict):
            args = {k: ("[REDACTED]" if _SECRET_KEY.search(str(k)) else v) for k, v in args.items()}
        text = repr(args)
    return text[:_PREVIEW_LIMIT] + (" …(생략)" if len(text) > _PREVIEW_LIMIT else "")


class ApprovalModal(ModalScreen[bool]):
    """위험 도구 승인 — 승인/거부 버튼, ESC(닫기)=거부."""

    BINDINGS = [("escape", "deny", "거부")]

    def __init__(self, tool: str, args: object) -> None:
        super().__init__()
        self.tool = tool
        self._args = args

    def compose(self) -> ComposeResult:
        with Vertical(id="approval_dialog"):
            yield Static(f"[승인 필요] 도구: {self.tool}", id="approval_title", markup=False)
            yield Static(_args_preview(self.tool, self._args), id="approval_args", markup=False)
            yield Static("대화형 승인입니다 (--yolo 자동 승인과 구분됨).", id="approval_note")
            with Horizontal(id="approval_buttons"):
                yield Button("승인", variant="success", id="approve")
                yield Button("거부", variant="error", id="deny")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve")

    def action_deny(self) -> None:
        self.dismiss(False)


_STATUS = {"connecting": ("⋯", "dim", "연결 중"), "ok": ("✔", "green", "연결됨"),
           "fail": ("✘", "red", "실패"), "off": ("⊘", "dim", "꺼짐")}


def _server_state(server, connecting: bool) -> str:
    if not server.enabled:
        return "off"
    if connecting:
        return "connecting"
    return "fail" if server.error else "ok"


class McpScreen(ModalScreen[None]):
    """/mcp 서버 관리 화면. 서버 목록에서 r 재연결 · d 켜기/끄기 · ESC 닫기. 강조된 서버의 도구가 아래에 보인다."""

    BINDINGS = [("escape", "close", "닫기"), ("r", "reconnect", "재연결"), ("d", "toggle", "켜기/끄기")]

    def __init__(self, servers: list, dirs: list, connecting: bool, on_toggle, on_reconnect) -> None:
        super().__init__()
        self._servers = servers
        self._dirs = dirs
        self._connecting = connecting
        self._on_toggle = on_toggle
        self._on_reconnect = on_reconnect
        self._selected = servers[0].name if servers else None   # 강조 서버. 목록을 다시 그려도 이름으로 유지한다

    def _row(self, s) -> Text:
        glyph, style, label = _STATUS[_server_state(s, self._connecting)]
        t = Text()
        t.append(f"{glyph} ", style=f"bold {style}")
        t.append(f"{s.name:<14}", style="bold")
        t.append(f" {label:<4} ", style=style)
        t.append(f" {s.source} · {s.transport} ", style="dim")
        t.append(f" 도구 {len(s.tools)}" if not s.error else f" {s.error[:50]}", style="dim" if not s.error else "red")
        return t

    def _detail(self, s) -> str:
        lines = [f"{s.target}"]
        if s.error:
            lines.append(f"오류: {s.error}")
        for t in s.tools:
            desc = (t.description or "").strip().splitlines()[0][:70] if getattr(t, "description", "") else ""
            lines.append(f"  · {t.name}" + (f"  {desc}" if desc else ""))
        return "\n".join(lines) or "(도구 없음)"

    def compose(self) -> ComposeResult:
        with Vertical(id="mcp_dialog"):
            yield Static("MCP 서버  (r 재연결 · d 켜기/끄기 · ESC 닫기)", id="mcp_title", markup=False)
            if self._servers:
                yield OptionList(*(Option(self._row(s), id=s.name) for s in self._servers), id="mcp_list")
                yield Static("", id="mcp_detail", markup=False)
            else:
                yield Static("설정된 서버가 없습니다. 아래 폴더에 *.json 으로 mcpServers 를 두면 다음 실행 때 연결합니다.", id="mcp_detail", markup=False)
            yield Static("설정: " + " · ".join(str(d) for d in self._dirs), id="mcp_dirs", markup=False)

    def on_mount(self) -> None:
        if self._servers:
            self.query_one("#mcp_list", OptionList).highlighted = 0

    def _current(self):
        return next((s for s in self._servers if s.name == self._selected), None)

    def refresh_servers(self, connecting: bool = False) -> None:
        """연결 결과·토글이 바뀌었을 때 행과 상세를 다시 그린다."""
        self._connecting = connecting
        if not self._servers:
            return
        lst = self.query_one("#mcp_list", OptionList)
        lst.clear_options()
        lst.add_options([Option(self._row(s), id=s.name) for s in self._servers])
        names = [s.name for s in self._servers]
        lst.highlighted = names.index(self._selected) if self._selected in names else 0
        self._show_detail()

    def _show_detail(self) -> None:
        s = self._current()
        self.query_one("#mcp_detail", Static).update(self._detail(s) if s else "")

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._selected = event.option.id
        self._show_detail()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_toggle(self) -> None:
        if (s := self._current()) is not None:
            self._on_toggle(s)
            self.refresh_servers(self._connecting)

    def action_reconnect(self) -> None:
        if (s := self._current()) is not None:
            self._on_reconnect(s)


class InfoScreen(ModalScreen[None]):
    """제목과 본문 한 덩어리를 보여 주는 읽기 전용 모달(/status). ESC 로 닫는다."""

    BINDINGS = [("escape", "close", "닫기")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="info_dialog"):
            yield Static(self._title, id="info_title", markup=False)
            yield Static(self._body, id="info_body", markup=False)

    def action_close(self) -> None:
        self.dismiss(None)


class SessionPicker(ModalScreen[str | None]):
    """/resume 세션 선택 — 최근 수정순 목록에서 Enter 로 고르고 ESC 로 닫는다(None)."""

    BINDINGS = [("escape", "cancel", "닫기")]

    def __init__(self, sessions: list[dict]) -> None:
        super().__init__()
        self._sessions = sessions

    @staticmethod
    def _label(s: dict) -> Text:
        import datetime
        when = datetime.datetime.fromtimestamp(s["updated"]).strftime("%m-%d %H:%M")
        first = s.get("title") or " ".join(s["first_task"].split())[:60] or "(질의 없음)"
        t = Text()
        t.append(s["id"], style="bold")
        t.append(f"  {s['turns']}턴  {when}  ", style="dim")
        t.append(first)
        return t

    def compose(self) -> ComposeResult:
        with Vertical(id="session_dialog"):
            yield Static("재개할 세션을 고르세요 (Enter 선택 · ESC 닫기)", id="session_title", markup=False)
            yield OptionList(*(Option(self._label(s), id=s["id"]) for s in self._sessions), id="session_list")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
