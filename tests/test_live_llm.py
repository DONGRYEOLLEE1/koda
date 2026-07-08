"""로컬 LLM 라이브 테스트 (langchain). 엔드포인트 미가용 시 자동 skip(기본 스위트 보호).

    uv run pytest tests/test_live_llm.py -s -v
"""
from __future__ import annotations

import pytest

from koda.config import load_model_settings
from koda.llm import _health, build_model


def _live_or_skip():
    settings = load_model_settings()
    if not _health(settings.local_base_url, timeout=2.0):
        pytest.skip(f"로컬 엔드포인트 미가용: {settings.local_base_url}")
    return settings


def test_live_model_invoke():
    settings = _live_or_skip()
    chat, provider, _ = build_model(settings)
    resp = chat.invoke("한 단어로만 답해: 대한민국의 수도는?")
    assert isinstance(resp.content, str) and resp.content.strip()
    print(f"\n[{provider}] LLM: {resp.content}")


def test_live_branching_run_streams(tmp_path):
    settings = _live_or_skip()
    (tmp_path / "hello.txt").write_text("world\n", encoding="utf-8")

    import asyncio

    from koda.agents import run_task
    from koda.workspace import Workspace

    tokens: list[str] = []
    result = asyncio.run(run_task(
        "hello.txt 파일 내용을 확인해서 알려줘",
        Workspace(tmp_path), approver=lambda n, a: True,
        on_token=tokens.append, settings=settings, mcps_dir=None,
    ))
    assert result["final"].strip()              # 최종 답변 존재
    assert "".join(tokens).strip()              # 토큰이 스트리밍됨
    assert result["route"] in ("read", "edit")  # 라우팅됨
    if result["route"] == "edit":               # 검증은 부수효과(edit) 경로에만
        assert result["verdict"] in ("pass", "fail")
    else:
        assert result["verdict"] is None
    print(f"\nroute={result['route']} verdict={result['verdict']} answer={result['final'][:100]}")
