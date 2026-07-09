"""도구 스텝 상한 middleware: 에이전트 1회 실행당 도구 호출 횟수를 하드 캡한다.

약한 모델이 "한 파일씩" 편향으로 40+콜을 태우다 타임아웃하는 폭주를, 하네스가 결정론으로 자른다
(plan/TOOL_ENHANCEMENT_PLAN.md §Phase A). 상한 도달 시 도구를 실행하지 않고 error ToolMessage 를
돌려, 모델이 지금까지 정보로 최종 답을 내도록 유도한다(그래프는 죽지 않는다).

카운터는 before_agent 에서 리셋한다 → astream_agent 1회 실행이 곧 예산 단위. verify-fail/empty
retry 가 같은 agent 인스턴스를 재사용해도(graph 의 coder 재시도) 매 실행마다 새 예산이 된다.
"""
from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage


class StepBudgetMiddleware(AgentMiddleware):
    """max_steps: 이 에이전트 1회 실행에서 허용하는 도구 호출 횟수. 초과 호출은 실행하지 않는다."""

    def __init__(self, max_steps: int):
        super().__init__()
        self._max = max_steps
        self._n = 0

    def before_agent(self, state, runtime):
        self._n = 0
        return None

    async def abefore_agent(self, state, runtime):
        self._n = 0
        return None

    def _over_budget(self, request):
        """예산 초과면 error ToolMessage, 아니면 None. 호출 시도마다 카운트를 올린다."""
        self._n += 1
        if self._n <= self._max:
            return None
        call = getattr(request, "tool_call", {}) or {}
        return ToolMessage(
            content=(
                f"도구 스텝 상한 {self._max}회에 도달했다. 더는 도구를 호출하지 말고, "
                f"지금까지 수집한 정보만으로 최종 답을 작성하라."
            ),
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )

    def wrap_tool_call(self, request, handler):
        return self._over_budget(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        return self._over_budget(request) or await handler(request)
