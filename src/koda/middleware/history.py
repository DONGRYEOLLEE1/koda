from __future__ import annotations

import json
import re

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages

from ..config import MAX_OUTPUT_TOKENS

# 스키마 크기를 재지 못할 때만 쓰는 폴백. 실측이 원칙이다.
DEFAULT_TOOLS_RESERVE = 4000


def _msg_text(m) -> str:
    c = getattr(m, "content", "") or ""
    if isinstance(c, list):  # 멀티모달 content block 리스트
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    return c if isinstance(c, str) else str(c)


# 문자 단위 파이썬 루프는 1M 규모 히스토리에서 호출당 0.2초를 먹는다(정규식은 0.046초). 판정 범위와 결과는 동일하다.
_CJK_RE = re.compile("[\u3040-\u9fff\uac00-\ud7a3\uf900-\ufaff]")


def _count_tokens(messages) -> int:
    """CJK 보정 토큰 추정. count_tokens_approximately 는 한/중/일을 ~2x 과소평가해 트림 누락→400 크래시를 부르므로 real ≈ base + cjk/3 으로 보정한다."""
    base = count_tokens_approximately(messages)
    cjk = sum(_CJK_RE.subn("", _msg_text(m))[1] for m in messages)
    return base + cjk // 3


def tool_schema_tokens(tools, default: int = DEFAULT_TOOLS_RESERVE) -> int:
    """바인딩된 도구 스키마가 프롬프트에서 차지하는 토큰(실측).

    고정 예약값은 MCP 서버를 붙이면 크게 빗나간다 — 도구 44개는 약 12k, 74개는 약 61k 로,
    4,000 으로 가정하면 트림이 통과시킨 요청이 서버에서 컨텍스트 초과로 거절된다(#91).
    도구가 아예 없으면 0 이고, 스키마 변환에 실패했을 때만 default 로 물러선다.
    """
    if not tools:
        return 0
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool
        schema = json.dumps([convert_to_openai_tool(t) for t in tools], ensure_ascii=False)
    except Exception:
        return default
    return _count_tokens([HumanMessage(schema)])


class HistoryTrimMiddleware(AgentMiddleware):
    def __init__(self, max_prompt_tokens: int = 16000, tools_reserve: int = DEFAULT_TOOLS_RESERVE,
                 output_reserve: int = MAX_OUTPUT_TOKENS):
        """max_prompt_tokens: 프롬프트 총 토큰 상한. tools_reserve: 도구 스키마 몫(specs.build 가 실측값을 넘긴다). output_reserve: 응답을 생성할 자리."""
        super().__init__()
        self._max = max_prompt_tokens
        self._reserve = tools_reserve + output_reserve

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
        # messages 에 허용되는 예산 = 총 상한 − system − (도구 스키마 + 출력 자리). 최소 하한으로 바닥 방지.
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
