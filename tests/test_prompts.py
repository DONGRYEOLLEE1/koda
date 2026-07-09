"""프롬프트 회귀: 구식 도구 계약이 다시 새지 않게 잠근다 (§Phase A).

- argv 리스트 계약은 read-only 셸이 `bash -c` 조합으로 바뀌며 폐기됐다.
- 도구 이름의 `workspace_` 접두사는 제거됐다.
"""
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
