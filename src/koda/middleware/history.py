"""턴 내부 이력 트리밍 middleware — 나가는 요청 프롬프트를 토큰 예산 이하로 결정론 절단.

한 턴의 ReAct 루프에서 도구 결과가 누적되어 단일 요청 프롬프트가 서버 컨텍스트 창(n_ctx)을
넘으면 llama.cpp 가 400(exceed_context_size)으로 턴을 하드 크래시한다. `ToolOutputTrimMiddleware`
는 '도구 출력 1건'의 상한이라 '턴 누적'을 못 막는다 — 이 미들웨어가 매 모델 호출 직전 메시지를
예산 이하로 잘라 그 결함을 닫는다 (plan/CONTEXT_MANAGEMENT §5.A).

- 비파괴적: `wrap_model_call` 로 이 호출의 요청만 수정하고 graph state 는 건드리지 않는다
  (`ToolOutputTrimMiddleware.wrap_tool_call` 과 동형).
- 쌍 무결성: `trim_messages(start_on="human", end_on=("human","tool"))` 로 `AIMessage(tool_calls)`
  ↔ `ToolMessage` 쌍이 잘려 orphan 이 되지 않게 한다.
- 무손실에 가까움: 잘린 도구 원문은 이미 `.koda/artifacts/` 에 저장돼 있어(§2.3) 에이전트가
  `read_artifact` 로 되찾을 수 있다 = 정보 완전 소실 아님.
- 요약(compaction)이 아니라 결정론 트림을 쓰는 이유: 느린 로컬 LLM 에서 초과 스텝마다 요약
  LLM 을 호출하면 지연이 폭증한다. 트림은 추가 호출 0, 예산 준수 보장(plan §6 단계1).
"""
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
    """CJK 보정 토큰 추정. `count_tokens_approximately` 는 4 chars/token 가정이라 한/중/일을
    ~2x 과소평가한다(실측: 한국어 1.95x, 코드 1.05x). 이 koda는 한국어 위주라 그대로 쓰면
    실제로는 컨텍스트 창을 넘겨도 예산 이하로 오판해 트림이 안 걸리고 400 이 난다. CJK 는
    ~1.4~2 chars/token 이므로 real ≈ base + cjk/3 으로 보정한다. 언더카운트는 400 을 부르고
    오버카운트는 조금 일찍 트림할 뿐이라, 안전측(약간 과대평가)인 /3 을 쓴다(실측 대비 ±20%)."""
    base = count_tokens_approximately(messages)
    cjk = sum(1 for m in messages for ch in _msg_text(m) if _is_cjk(ch))
    return base + cjk // 3


class HistoryTrimMiddleware(AgentMiddleware):
    def __init__(self, max_prompt_tokens: int = 16000, tools_reserve: int = 4000):
        """max_prompt_tokens: 프롬프트 총 토큰 상한(char 추정 기준). char 휴리스틱이 CJK·
        도구호출 혼합을 최대 ~1.9x 과소평가하므로 n_ctx 의 약 절반으로 보수 설정해 그 오차를
        흡수한다(config.context_budget_tokens 참조). tools_reserve: 도구 스키마 몫 예약."""
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
        # 첫 human(작업 지시)은 항상 보존하고, 나머지에서 최근 tail 만 예산 내로 트림한다.
        # 에이전트 히스토리는 선두 human 하나 + 이후 ai/tool 누적이라, trim_messages 에
        # start_on="human" 을 그냥 걸면 tail 을 human 으로 시작 못 해 빈 결과가 된다(실측).
        # → head(첫 human) 를 떼고 rest 를 start_on=("ai","human") 로 잘라 붙인다.
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
        # kept_rest 가 비면(단일 초대형 tail 쌍이 예산 초과 — 예: 추론모델의 긴 CoT 를 담은
        # AIMessage) new_msgs 가 head(작업 지시)만 남아 히스토리가 통째로 증발한다. 그 경우
        # 원본을 그대로 보낸다 — 400 위험은 감수하되 컨텍스트 소실 + 도구 반복 루프를 막는다.
        # (ToolOutputTrimMiddleware 는 도구 '출력'만 캡하고 AIMessage.content 는 안 건드리므로
        #  거대 CoT 단일 메시지는 여기서 못 줄인다 — agy 리뷰에서 재현 확인된 치명 케이스.)
        if not kept_rest or len(new_msgs) >= len(msgs):
            return request
        return request.override(messages=new_msgs)

    def wrap_model_call(self, request, handler):
        return handler(self._fit(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._fit(request))
