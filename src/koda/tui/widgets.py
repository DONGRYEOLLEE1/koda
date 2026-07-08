"""TUI 위젯: 승인 modal (plan/TEXTUAL_TUI_PLAN.md §5.5)."""
from __future__ import annotations

import re

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

_PREVIEW_LIMIT = 500
_SECRET_KEY = re.compile(r"(?i)token|secret|key|password|authorization|credential")


def _args_preview(tool: str, args: object) -> str:
    """args 요약 — 길이 제한 + secret 성 키 redaction, raw dump 금지 (§9)."""
    if tool == "workspace_run_command" and isinstance(args, dict):
        v = args.get("argv") or args.get("command") or ""
        text = "$ " + (" ".join(map(str, v)) if isinstance(v, list) else str(v))
    else:
        if isinstance(args, dict):
            args = {k: ("[REDACTED]" if _SECRET_KEY.search(str(k)) else v) for k, v in args.items()}
        text = repr(args)
    return text[:_PREVIEW_LIMIT] + (" …(생략)" if len(text) > _PREVIEW_LIMIT else "")


class ApprovalModal(ModalScreen[bool]):
    """위험 도구 승인 — 승인/거부 버튼, ESC(닫기)=거부 (§5.5, AC 7)."""

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
