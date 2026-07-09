from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages


def _msg_text(m) -> str:
    c = getattr(m, "content", "") or ""
    if isinstance(c, list):  # 멀티모달 content block 리스트
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    return c if isinstance(c, str) else str(c)


def _is_cjk(ch: str) -> bool:
    return ("぀" <= ch <= "鿿") or ("가" <= ch <= "힣") or ("豈" <= ch <= "﫿")


def _count_tokens(messages) -> int:
    """CJK 보정 토큰 추정. count_tokens_approximately 는 한/중/일을 ~2x 과소평가해 트림 누락→400 크래시를 부르므로 real ≈ base + cjk/3 으로 보정한다."""
    base = count_tokens_approximately(messages)
    cjk = sum(1 for m in messages for ch in _msg_text(m) if _is_cjk(ch))
    return base + cjk // 3


class HistoryTrimMiddleware(AgentMiddleware):
    def __init__(self, max_prompt_tokens: int = 16000, tools_reserve: int = 4000):
        """max_prompt_tokens: 프롬프트 총 토큰 상한(char 휴리스틱 오차를 흡수하려 n_ctx 의 절반 정도로 보수 설정). tools_reserve: 도구 스키마 몫 예약."""
        super().__init__()
        self._max = max_prompt_tokens
        self._reserve = tools_reserve

    def _system_tokens(self, request) -> int:
        sm = getattr(request, "system_message", None)
        if sm is None:
            return 0
        text = sm if isinstance(sm, str) else getattr(sm, "content", "") or ""
        try:
            return _count_tokens([SystemMessage(str(text))])
        except Exception:
            return len(str(text)) // 3   # 폴백도 CJK 보수측(3 chars/token)

    def _fit(self, request):
        msgs = list(request.messages)
        # messages 에 허용되는 예산 = 총 상한 − system − 도구 예약. 최소 하한으로 바닥 방지.
        budget = max(2000, self._max - self._system_tokens(request) - self._reserve)
        if _count_tokens(msgs) <= budget:
            return request
        # 첫 human(작업 지시)은 항상 보존하고 나머지 tail 만 자른다 — head 를 떼고 rest 를
        # start_on=("ai","human") 로 트림해야 빈 결과를 피한다.
        head, rest = ([], msgs)
        if msgs and isinstance(msgs[0], HumanMessage):
            head, rest = [msgs[0]], msgs[1:]
        head_tok = _count_tokens(head) if head else 0
        kept_rest = trim_messages(
            rest, max_tokens=max(1000, budget - head_tok), strategy="last",
            token_counter=_count_tokens,
            start_on=("ai", "human"), end_on=("human", "tool"),  # ai 로 시작 = 쌍 무결성 보존
            include_system=False, allow_partial=False,
        )
        new_msgs = head + kept_rest
        # kept_rest 가 비면(단일 초대형 tail 쌍이 예산 초과) head 만 남아 히스토리가 증발한다 —
        # 그 경우 원본을 그대로 보내 컨텍스트 소실·도구 반복 루프를 막는다.
        if not kept_rest or len(new_msgs) >= len(msgs):
            return request
        return request.override(messages=new_msgs)

    def wrap_model_call(self, request, handler):
        return handler(self._fit(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._fit(request))
