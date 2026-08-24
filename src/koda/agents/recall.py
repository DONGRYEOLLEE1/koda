from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import load_model_settings
from ..llm import _no_think, build_model
from ..prompts import load_prompt
from .streaming import _to_text


def _history_text(history: list) -> str:
    return "\n".join(f"[{m['role']}] {m['content']}" for m in history)


async def summarize_history(history: list, settings=None) -> str:
    """오래된 대화의 압축 요약 생성 (패턴 5 — 요약 내용은 LLM 이 만든다)."""
    settings = settings or load_model_settings()
    model, _provider, _ = build_model(settings)
    m = _no_think(model, settings)
    resp = await m.ainvoke([SystemMessage(load_prompt("history_summary")), HumanMessage(_history_text(history))])
    return _to_text(resp.content).strip()


async def compact_memory(memory_text: str, settings=None) -> str:
    """상한 초과한 영구 메모리를 LLM 통합 요약으로 압축 (내용 판단은 LLM 몫)."""
    settings = settings or load_model_settings()
    model, _provider, _ = build_model(settings)
    m = _no_think(model, settings)
    resp = await m.ainvoke([SystemMessage(load_prompt("memory_compact")), HumanMessage(memory_text)])
    return _to_text(resp.content).strip()


async def extract_memories(history: list, settings=None) -> list[str]:
    """세션 대화에서 영구 기억할 사실을 추출 (패턴 4 — 기억 가치 판단은 LLM 이 한다)."""
    settings = settings or load_model_settings()
    model, _provider, _ = build_model(settings)
    m = _no_think(model, settings)
    resp = await m.ainvoke([SystemMessage(load_prompt("memory_extract")), HumanMessage(_history_text(history))])
    lines = [ln.strip() for ln in _to_text(resp.content).splitlines() if ln.strip()]
    return lines[:3]
