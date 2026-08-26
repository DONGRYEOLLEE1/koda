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


def test_system_prompt_ends_with_today_kst_date():
    # 날짜가 없으면 웹검색 결과의 '최근'을 학습 시점 기준으로 해석한다. 분 단위 시각은 캐시 prefix 를 깨므로 넣지 않는다.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from koda.agents.specs import AGENTS
    text = AGENTS["answer"].system_prompt()
    today = datetime.now(ZoneInfo("Asia/Seoul"))
    assert f"오늘은 {today:%Y-%m-%d}" in text and "KST" in text
    assert text.rstrip().endswith("판단한다.")           # 날짜 절이 prefix 의 맨 뒤
    assert f"{today:%H:%M}" not in text
