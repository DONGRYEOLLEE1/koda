from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from koda.agents.streaming import _empty_reason, _final_ai, astream_agent


def _state(*msgs):
    return {"messages": list(msgs)}


def test_final_ai_picks_last_text_answer():
    st = _state(
        HumanMessage("q"),
        AIMessage("먼저 낸 답"),
        HumanMessage("q2"),
        AIMessage("최종 답", response_metadata={"finish_reason": "stop"}),
    )
    answer, finish, _ = _final_ai(st)
    assert answer == "최종 답" and finish == "stop"


def test_final_ai_length_truncated_empty_last():
    # 폭주 시나리오: 마지막 AI 가 reasoning 만으로 예산 소진 → content 빈 채 finish=length
    st = _state(
        HumanMessage("q"),
        AIMessage("", response_metadata={"finish_reason": "length"}),
    )
    answer, finish, _ = _final_ai(st)
    assert answer == "" and finish == "length"
    assert _empty_reason(answer, finish) == "length_truncation"


def test_final_ai_last_ai_only_no_backfill():
    # 회귀 방지: 마지막 AI 가 빈 content 면 앞의 텍스트로 되메우지 않는다 —
    # 빈 답은 빈 답으로 판정돼야 재시도 게이트가 돈다
    st = _state(
        AIMessage("본문 답변", response_metadata={"finish_reason": "stop"}),
        ToolMessage(content="tool out", tool_call_id="t1"),
        AIMessage("", response_metadata={"finish_reason": "tool_calls"}),
    )
    answer, finish, _ = _final_ai(st)
    assert answer == "" and finish == "tool_calls"


def test_final_ai_start_boundary_ignores_history_answers():
    # 멀티턴 히스토리의 이전 답변이 이번 시도의 답으로 오인되면 안 된다
    st = _state(
        HumanMessage("이전 질문"),
        AIMessage("이전 턴의 답변"),                    # 히스토리 (입력에 포함)
        HumanMessage("이번 질문"),                      # 입력 마지막
        AIMessage("", response_metadata={"finish_reason": "length"}),  # 이번 시도: 폭주로 빈 응답
    )
    answer, finish, _ = _final_ai(st, start=3)          # 입력 메시지 3개 이후 = 이번 시도 구간
    assert answer == "" and finish == "length"          # 이전 답변으로 되메우지 않음
    # 이번 시도가 AI 메시지를 아예 못 만든 경우: 경계가 없으면 히스토리 답변을 오인한다
    st2 = _state(HumanMessage("이전 질문"), AIMessage("이전 턴의 답변"), HumanMessage("이번 질문"))
    assert _final_ai(st2, start=3) == ("", "unknown", {})
    assert _final_ai(st2, start=0)[0] == "이전 턴의 답변"  # 경계 부재 시의 위험 재현


def test_empty_reason_enum():
    # no_content | length_truncation | unknown 세 값 고정
    assert _empty_reason("답 있음", "length") == ""
    assert _empty_reason("", "length") == "length_truncation"
    assert _empty_reason("  ", "stop") == "no_content"
    assert _empty_reason("", "tool_calls") == "no_content"
    assert _empty_reason("", "unknown") == "unknown"


class _StubAgent:
    """astream 스텁: (mode, item) 시퀀스를 그대로 방출."""

    def __init__(self, events):
        self._events = events

    async def astream(self, _input, config=None, stream_mode=None):
        for ev in self._events:
            yield ev


def test_reasoning_chunks_emit_think_event():
    """thinking 구간(content 없는 청크)은 think:N 이벤트로 가시화된다 (1초 쓰로틀 → 첫 발화 즉시)."""
    msgs = [{"role": "user", "content": "질문"}]
    final = _state(HumanMessage("질문"),
                   AIMessage("답변", response_metadata={"finish_reason": "stop"}))
    agent = _StubAgent([
        ("messages", (AIMessageChunk(content=""), {})),   # reasoning 청크 1
        ("messages", (AIMessageChunk(content=""), {})),   # reasoning 청크 2 (쓰로틀에 걸림)
        ("messages", (AIMessageChunk(content="답변"), {})),
        ("values", final),
    ])
    events, tokens = [], []
    res = asyncio.run(astream_agent(agent, msgs, tokens.append, on_event=events.append))
    assert events == ["think:1"]        # 첫 빈 청크 즉시 발화, 이후 1초 내 재발화 없음
    assert tokens == ["답변"]           # content 청크는 그대로 스트리밍 (회수 재생 아님)
    assert res.streamed_text == "답변"  # 청크가 실제로 흘렀다 → 회수 재생 미발동
    assert res.answer == "답변"


def test_tool_message_never_leaks_to_stdout():
    """도구 출력(ToolMessage)은 on_token 으로 절대 새지 않는다 — 필터 완화 후에도 유지."""
    msgs = [{"role": "user", "content": "질문"}]
    final = _state(HumanMessage("질문"),
                   AIMessage("답", response_metadata={"finish_reason": "stop"}))
    agent = _StubAgent([
        ("messages", (ToolMessage(content="비밀 도구 출력", tool_call_id="1"), {})),
        ("messages", (HumanMessage("주입 시도"), {})),
        ("messages", (AIMessageChunk(content="답"), {})),
        ("values", final),
    ])
    tokens = []
    asyncio.run(astream_agent(agent, msgs, tokens.append))
    assert tokens == ["답"]  # AIMessageChunk 만 통과
