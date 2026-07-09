from __future__ import annotations

from pathlib import Path

_PROMPTS = Path(__file__).resolve().parents[1] / "src" / "koda" / "prompts"


def _all_prompt_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_PROMPTS.glob("*.md")))


def test_no_stale_argv_contract():
    assert "argv" not in _all_prompt_text()


def test_no_workspace_prefixed_tool_names():
    text = _all_prompt_text()
    for name in ("workspace_read_file", "workspace_run_command", "workspace_edit_file",
                 "workspace_search_text", "workspace_run_readonly_command"):
        assert name not in text
