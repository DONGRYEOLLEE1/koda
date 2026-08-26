from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ..config import load_model_settings
from ..llm import _no_think, astructured, build_model
from ..memory import KINDS, Memory
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


class MemoryJudgement(BaseModel):
    """방금 끝난 턴에서 다음 세션에도 쓸모 있는 것만 유형별로 고른 결과. 대부분의 턴은 세 목록이 모두 비어 있다."""

    semantic: list[str] = Field(default_factory=list, description="사용자·프로젝트에 대한 지속적 사실·선호. 완결된 한 문장, 80자 이내, 최대 2개")
    episodic: list[str] = Field(default_factory=list, description="이 턴에서 실제로 한 일과 결과 중 다음에 참고할 경험. 오늘 날짜를 앞에 붙인 한 문장, 최대 2개")
    procedural: list[str] = Field(default_factory=list, description="사용자가 앞으로 지키라고 한 규칙·작업 방식. 완결된 한 문장, 최대 2개")


async def judge_memory(task: str, answer: str, existing: str = "", settings=None) -> dict[str, list[str]]:
    """턴 하나를 보고 저장할 메모리를 유형별로 고른다 (판단은 LLM, 상한·형식 집행은 여기서)."""
    settings = settings or load_model_settings()
    model, _provider, _ = build_model(settings)
    m = _no_think(model, settings)
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    human = (f"[오늘] {today}\n\n[이미 저장된 메모리]\n{existing or '(없음)'}\n\n"
             f"[사용자 질의]\n{task}\n\n[에이전트 답변]\n{answer[:1500]}")
    r = await astructured(m, MemoryJudgement, [SystemMessage(load_prompt("memory_judge")), HumanMessage(human)])
    if r is None:
        return {}
    return {k: [n.strip() for n in getattr(r, k) if n and n.strip() and len(n) <= 200][:2] for k in KINDS}


async def remember_from_turn(memory: Memory, task: str, answer: str, settings=None) -> list[tuple[str, str]]:
    """턴 완료 직후: 판정 → 저장 → 상한 초과 유형 압축. 저장된 (유형, 항목) 목록을 돌려준다."""
    saved: list[tuple[str, str]] = []
    for kind, notes in (await judge_memory(task, answer, memory.read(), settings)).items():
        added = [n for n in notes if memory.remember(n, kind)]
        saved += [(kind, n) for n in added]
        if added and memory.needs_compaction(kind):
            compacted = await compact_memory(memory._read_kind(kind), settings)
            if compacted:
                memory.replace(kind, compacted)
    return saved
