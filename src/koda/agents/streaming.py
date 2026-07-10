from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class AgentResult:
    """에이전트 1회 실행 결과. answer 가 판정의 유일한 근거, streamed_text 는 UX 증거일 뿐 판정엔 안 쓴다."""

    answer: str
    streamed_text: str = ""
    finish_reason: str = "unknown"
    usage: dict = field(default_factory=dict)
    empty_reason: str = ""
    messages: list = field(default_factory=list)  # 정본 state 의 누적 메시지(빈응답 저비용 복구용)

    @property
    def empty(self) -> bool:
        return not self.answer.strip()


def _to_text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(str(c) for c in content) if content else ""


def _final_ai(final_state, start: int = 0) -> tuple[str, str, dict]:
    """최종 state 의 이번 시도 구간(messages[start:])에서 마지막 AI 메시지를 추출한다. start 경계가 없으면 이전 턴 답변을 현재 답으로 오인해 빈 응답 게이트를 우회한다."""
    msgs = ((final_state or {}).get("messages") or [])[start:]
    for m in reversed(msgs):
        if getattr(m, "type", "") != "ai":
            continue
        meta = getattr(m, "response_metadata", None) or {}
        return (
            _to_text(m.content),
            meta.get("finish_reason") or "unknown",
            getattr(m, "usage_metadata", None) or {},
        )
    return "", "unknown", {}


def _empty_reason(answer: str, finish_reason: str) -> str:
    """빈 답변 분류(결정론 게이트)."""
    if answer.strip():
        return ""
    if finish_reason == "length":
        return "length_truncation"  # thinking 폭주가 출력 예산을 소진
    return "no_content" if finish_reason != "unknown" else "unknown"


async def astream_agent(agent, messages: list, on_token, config=None, on_event=None) -> AgentResult:
    """에이전트를 async 스트리밍으로 돌리며 토큰을 흘리고 실행 결과 계약을 반환한다."""
    final_state = None
    streamed: list[str] = []
    think_chunks = 0    # content 없는 청크 수 ≈ reasoning 토큰 수 (루프 내 주석 참조)
    last_think = 0.0
    async for mode, item in agent.astream(
        {"messages": messages}, config=config, stream_mode=["messages", "values"]
    ):
        if mode == "values":
            final_state = item  # 마지막 values = 최종 state (정본)
            continue
        msg = item[0] if isinstance(item, tuple) else item
        # AIMessageChunk 만 흘린다(클래스명+type 이중 필터) — 이 langchain 버전은 AIMessageChunk.type 이
        # "ai" 가 아니라 "AIMessageChunk" 라 "ai" 만 허용하면 스트리밍이 전면 죽는다.
        if type(msg).__name__ != "AIMessageChunk":
            continue
        if getattr(msg, "type", "") not in ("ai", "AIMessageChunk"):
            continue
        if on_event:  # 도구 호출 시작 감지 (첫 청크에만 name 이 실려 호출당 1회 발화)
            for tc in getattr(msg, "tool_call_chunks", None) or []:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                if name:
                    on_event(f"tool:{name}")
        text = _to_text(msg.content)
        if text:
            on_token(text)
            streamed.append(text)
        elif on_event:
            # reasoning 구간은 content 없는 청크만 도착한다(엔드포인트가 reasoning 텍스트를 노출 안 함)
            # — 청크 도착 자체를 진행 신호로 쓴다(1초 쓰로틀).
            think_chunks += 1
            now = time.monotonic()
            if now - last_think >= 1.0:
                on_event(f"think:{think_chunks}")
                last_think = now
    answer, finish, usage = _final_ai(final_state, start=len(messages))
    streamed_text = "".join(streamed)
    if answer.strip() and not streamed_text:
        # 스트림 청크가 유실돼도(중첩 스트림 결함) 정본 답변을 화면에 보여준다.
        on_token(answer)
    return AgentResult(
        answer=answer,
        streamed_text=streamed_text,
        finish_reason=finish,
        usage=usage,
        empty_reason=_empty_reason(answer, finish),
        messages=list((final_state or {}).get("messages") or []),
    )


async def resummarize(model, messages: list, nudge: str, on_token, config=None) -> str:
    """빈 최종 응답 복구 — 이미 끝난 도구 실행 결과가 담긴 누적 messages 에 요약 지시를 붙여 모델
    콜 1회로 보고를 받는다. 에이전트 전체 재실행(도구 재호출)을 피하므로 값싸다. 도구를 바인딩하지
    않은 raw 모델을 쓰므로 추가 도구 루프가 없다."""
    from langchain_core.messages import HumanMessage
    if not messages:
        return ""
    try:
        resp = await model.ainvoke(list(messages) + [HumanMessage(nudge)], config=config)
    except Exception:
        return ""
    text = _to_text(resp.content).strip()
    if text:
        on_token(text)
    return text
