from __future__ import annotations

import re

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Static, TextArea

_PREVIEW_LIMIT = 500
_SECRET_KEY = re.compile(r"(?i)token|secret|key|password|authorization|credential")


class PromptArea(TextArea):
    """질의 입력창(다중행). Enter=제출, 줄바꿈=Alt+Enter·Shift+Enter·Ctrl+J, 내용에 따라 자동 성장하며 높이는 CSS max-height 로 상한(초과분은 내부 스크롤).

    Ctrl+J(LF)는 Enter(CR)와 항상 구분돼 전달되므로, Alt+Enter 가 평범한 Enter 로 합쳐져 버리는 터미널(향상된 키보드 프로토콜 미지원)에서도 동작하는 줄바꿈 폴백이다.
    """

    _NEWLINE_KEYS = ("alt+enter", "shift+enter", "ctrl+j")

    class Submitted(Message):
        """Enter 제출. value 는 개행 포함 원문."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    async def _on_key(self, event) -> None:
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


class ToolBlock(Collapsible):
    """대화 스트림 안의 툴 실행 블록. 접힘이 기본 — ToolStart 로 mount(진행 중 ○), ToolEnd 로 finish(✓/✗ + output)."""

    def __init__(self, title: str) -> None:
        self._label = title
        self._body = Static("실행 중…", markup=False)
        super().__init__(self._body, title=f"○ 🔧 {title}", collapsed=True)

    def finish(self, output: str, ok: bool) -> None:
        self.title = f"{'✓' if ok else '✗'} 🔧 {self._label}"
        self._body.update(output or "(출력 없음)")


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
            yield Static("대화형 승인입니다 (--yes 자동 승인과 구분됨).", id="approval_note")
            with Horizontal(id="approval_buttons"):
                yield Button("승인", variant="success", id="approve")
                yield Button("거부", variant="error", id="deny")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve")

    def action_deny(self) -> None:
        self.dismiss(False)
