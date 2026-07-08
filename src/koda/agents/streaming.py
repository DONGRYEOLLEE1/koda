"""스트리밍 소비 + 실행 결과 계약 (ROADMAP §1.1).

답변 정본은 스트림 청크가 아니라 **최종 state 의 마지막 AIMessage** 에서 수집한다.
같은 노드 실행 안에서 두 번째 `astream(stream_mode="messages")` 가 청크를 소비자에게
전달하지 않는 실측 결함(trace e913037f) 때문에, 청크는 실시간 표시(on_token) 전용이다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class AgentResult:
    """에이전트 1회 실행의 관측 가능한 결과.

    - answer: 최종 state 정본 (성공 판정의 유일한 근거)
    - streamed_text: 화면에 흘린 청크 누적 (UX 증거 — 판정에 쓰지 않음)
    - finish_reason / usage: endpoint 관측값. 없으면 "unknown"/{} — 성공 판정에 쓰지 않는다.
    - empty_reason: ""(비어있지 않음) | no_content | length_truncation | unknown
    """

    answer: str
    streamed_text: str = ""
    finish_reason: str = "unknown"
    usage: dict = field(default_factory=dict)
    empty_reason: str = ""

    @property
    def empty(self) -> bool:
        return not self.answer.strip()


def _to_text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(str(c) for c in content) if content else ""


def _final_ai(final_state, start: int = 0) -> tuple[str, str, dict]:
    """최종 state 의 **이번 시도 구간**(messages[start:])에서 마지막 AI 메시지의
    (답변 텍스트, finish_reason, usage) 를 추출한다.

    start 경계가 핵심이다(codex 리뷰 H1): 멀티턴 히스토리가 입력에 포함되므로, 전체를
    역순 탐색하면 이번 시도가 빈 응답일 때 **이전 턴의 답변을 현재 답으로 오인**해
    빈 응답 게이트(§1.2/§1.3)를 통째로 우회한다. 마지막 AI 의 content 가 비면 빈 답이다.
    """
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
    """빈 답변 분류 (결정론 게이트 — ROADMAP §1.1/§1.3)."""
    if answer.strip():
        return ""
    if finish_reason == "length":
        return "length_truncation"  # thinking 폭주가 출력 예산을 소진 (§1.3)
    return "no_content" if finish_reason != "unknown" else "unknown"


async def astream_agent(agent, messages: list, on_token, config=None, on_event=None) -> AgentResult:
    """에이전트를 async 스트리밍으로 돌리며 토큰을 흘리고, 실행 결과 계약을 반환.

    messages: 대화 메시지 리스트(멀티턴 히스토리 + 현재 요청). 단일 턴이면 user 1개.
    on_event: 도구 실행 시작 등 진행 이벤트 통지("tool:<이름>") — 무출력 구간 가시화.
    """
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
        # AIMessageChunk 만 stdout 으로 흘린다. ToolMessage·HumanMessage 등은
        # 도구 출력이 사용자에게 누출되지 않도록 무조건 건너뛴다(클래스명 + type 이중 필터).
        # 실측: 이 langchain 버전의 AIMessageChunk.type 은 "ai" 가 아니라 "AIMessageChunk" 다
        # — "ai" 만 허용하면 모든 청크가 걸러져 토큰 스트리밍이 전면 죽는다(회수 재생만 남음).
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
            # thinking 모델의 reasoning 구간: content 없는 청크가 토큰 속도로 도착한다.
            # reasoning 텍스트 자체는 endpoint 가 노출하지 않으므로(실측 — additional_kwargs·
            # content_blocks 모두 빈 값) 청크 도착 자체를 진행 신호로 쓴다. 1초 쓰로틀.
            think_chunks += 1
            now = time.monotonic()
            if now - last_think >= 1.0:
                on_event(f"think:{think_chunks}")
                last_think = now
    answer, finish, usage = _final_ai(final_state, start=len(messages))
    streamed_text = "".join(streamed)
    if answer.strip() and not streamed_text:
        # 스트림 청크가 유실돼도(중첩 스트림 결함) 정본 답변을 화면에 보여준다 (codex 리뷰 H2)
        on_token(answer)
    return AgentResult(
        answer=answer,
        streamed_text=streamed_text,
        finish_reason=finish,
        usage=usage,
        empty_reason=_empty_reason(answer, finish),
    )
