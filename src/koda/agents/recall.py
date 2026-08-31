from __future__ import annotations

import re
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


_TITLE_LIMIT = 15
_SUMMARY_TITLE = re.compile(r"^제목\s*[:：]\s*(.+)$", re.MULTILINE)


def _clean_title(text: str) -> str:
    return " ".join(text.split()).strip("\"'“”「」 ")[:_TITLE_LIMIT].strip()


async def summarize_history(history: list, settings=None) -> tuple[str, str]:
    """오래된 대화의 (압축 요약, 세션 제목). 요약 끝의 `제목:` 줄을 분리하며, 그 줄이 없으면 제목은 빈 문자열이다."""
    settings = settings or load_model_settings()
    model, _provider, _ = build_model(settings)
    m = _no_think(model, settings)
    resp = await m.ainvoke([SystemMessage(load_prompt("history_summary")), HumanMessage(_history_text(history))])
    text = _to_text(resp.content).strip()
    match = _SUMMARY_TITLE.search(text)
    if not match:
        return text, ""
    return _SUMMARY_TITLE.sub("", text).strip(), _clean_title(match.group(1))


async def make_title(task: str, answer: str = "", settings=None) -> str:
    """세션 제목(10~15자)을 질의와 첫 답변으로 짓는다. 내용이 빈약하거나(LLM 이 `없음`) 호출이 실패하면 빈 문자열을 돌려 호출자가 다음 실질 턴에서 재시도하게 한다."""
    try:
        settings = settings or load_model_settings()
        model, _provider, _ = build_model(settings)
        m = _no_think(model, settings)
        human = f"[질의]\n{task}\n\n[답변]\n{answer[:500]}"
        resp = await m.ainvoke([SystemMessage(load_prompt("session_title")), HumanMessage(human)])
        title = _clean_title(_to_text(resp.content))
    except Exception:
        return ""
    return "" if title == "없음" else title


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
