"""핵심 모듈: StepBudgetMiddleware (도구 호출 스텝 하드 캡). request 를 흉내내 단위 검증."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from koda.middleware.step_budget import StepBudgetMiddleware


def _req(name="read_file", call_id="t1"):
    return SimpleNamespace(tool_call={"name": name, "args": {}, "id": call_id})


def _counting_handler(state):
    def handler(request):
        state["n"] += 1
        return ToolMessage(content="ok", tool_call_id="t1")

    return handler


def test_allows_up_to_cap():
    state = {"n": 0}
    mw = StepBudgetMiddleware(max_steps=3)
    for _ in range(3):
        res = mw.wrap_tool_call(_req(), _counting_handler(state))
        assert res.content == "ok"
    assert state["n"] == 3                     # 상한까지 모두 실행


def test_blocks_over_cap():
    state = {"n": 0}
    mw = StepBudgetMiddleware(max_steps=3)
    for _ in range(3):
        mw.wrap_tool_call(_req(), _counting_handler(state))
    res = mw.wrap_tool_call(_req(), _counting_handler(state))   # 4번째
    assert state["n"] == 3                     # 도구는 실행되지 않음
    assert res.status == "error"
    assert "상한" in res.content
    assert res.tool_call_id == "t1"


def test_before_agent_resets_budget():
    state = {"n": 0}
    mw = StepBudgetMiddleware(max_steps=2)
    for _ in range(2):
        mw.wrap_tool_call(_req(), _counting_handler(state))
    assert mw.wrap_tool_call(_req(), _counting_handler(state)).status == "error"  # 소진
    mw.before_agent(state={}, runtime=None)    # 새 실행 시작 → 예산 리셋
    res = mw.wrap_tool_call(_req(), _counting_handler(state))
    assert res.content == "ok"
    assert state["n"] == 3                      # 리셋 후 다시 실행됨


def test_async_path_gates():
    state = {"n": 0}
    mw = StepBudgetMiddleware(max_steps=1)

    async def ahandler(request):
        state["n"] += 1
        return ToolMessage(content="ok", tool_call_id="t1")

    async def run():
        await mw.abefore_agent(state={}, runtime=None)
        first = await mw.awrap_tool_call(_req(), ahandler)
        second = await mw.awrap_tool_call(_req(), ahandler)
        return first, second

    first, second = asyncio.run(run())
    assert first.content == "ok"
    assert second.status == "error"            # 두 번째는 상한 초과
    assert state["n"] == 1


# --- 실제 create_agent 루프에서 캡·리셋이 동작하는지 (before_agent 훅이 프레임워크에서 발화) ---


class _Scripted(BaseChatModel):
    responses: list
    _i: int = PrivateAttr(default=0)

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        msg = self.responses[self._i]
        self._i += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "scripted"


def test_e2e_caps_real_loop_and_resets_per_invocation():
    counter = {"n": 0}

    @tool
    def ping() -> str:
        """카운터를 올린다."""
        counter["n"] += 1
        return "pong"

    def call():
        return AIMessage(content="", tool_calls=[{"name": "ping", "args": {}, "id": "x"}])

    # 실행 2회분: 각 [tool, tool, (상한 초과로 차단될)tool, 최종답].
    model = _Scripted(responses=[
        call(), call(), call(), AIMessage(content="끝1"),
        call(), call(), call(), AIMessage(content="끝2"),
    ])
    agent = create_agent(model, [ping], middleware=[StepBudgetMiddleware(max_steps=2)])

    r1 = agent.invoke({"messages": [{"role": "user", "content": "go"}]})
    assert counter["n"] == 2                     # 상한 2 → 도구 2회만 실행, 3번째는 error 반환
    assert r1["messages"][-1].content == "끝1"

    r2 = agent.invoke({"messages": [{"role": "user", "content": "again"}]})
    # before_agent 가 실제로 호출돼 예산이 리셋되면 다시 2회 허용(총 4). 리셋 안 되면 2 유지.
    assert counter["n"] == 4
    assert r2["messages"][-1].content == "끝2"
