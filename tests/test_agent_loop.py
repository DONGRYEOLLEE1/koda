"""결정론적 e2e: create_agent + permission middleware 루프 (fake tool-calling model).

라이브 엔드포인트 없이 전체 루프를 검증한다. 도구 실행은 파일시스템 부수효과로 확인한다.
"""
from __future__ import annotations

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from koda.middleware import PermissionMiddleware
from koda.tools import make_all_tools
from koda.workspace import Workspace


class ScriptedChatModel(BaseChatModel):
    """스크립트된 AIMessage 를 순서대로 반환하는 fake (create_agent 호환)."""

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


def _agent(model, ws, approver, extra_tools=()):
    return create_agent(model, make_all_tools(ws) + list(extra_tools),
                        middleware=[PermissionMiddleware(approver, "main")])


def _ws(tmp_path, files):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return Workspace(tmp_path)


def test_loop_runs_tool_with_approval(tmp_path):
    ws = _ws(tmp_path, {})
    model = ScriptedChatModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "workspace_write_file", "args": {"path": "out.txt", "content": "hi"}, "id": "1"}]),
        AIMessage(content="파일 생성 완료"),
    ])
    out = _agent(model, ws, lambda n, a: True).invoke({"messages": [{"role": "user", "content": "파일 만들어"}]})
    assert (ws.root / "out.txt").read_text(encoding="utf-8") == "hi"   # 도구 실제 실행
    assert out["messages"][-1].content == "파일 생성 완료"


def test_loop_permission_blocks_dangerous(tmp_path):
    ws = _ws(tmp_path, {})
    model = ScriptedChatModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "workspace_write_file", "args": {"path": "NO.txt", "content": "x"}, "id": "1"}]),
        AIMessage(content="승인 거부로 실행하지 못했습니다."),
    ])
    _agent(model, ws, lambda n, a: False).invoke({"messages": [{"role": "user", "content": "파일 만들어"}]})
    assert not (ws.root / "NO.txt").exists()      # 게이트에 막혀 실행 안 됨


def test_extra_tools_mcp_seam(tmp_path):
    ws = _ws(tmp_path, {})

    @tool
    def fake_mcp_write(name: str) -> str:
        """가짜 MCP 도구: 마커 파일 생성."""
        (ws.root / f"{name}.marker").write_text("mcp", encoding="utf-8")
        return "done"

    model = ScriptedChatModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "fake_mcp_write", "args": {"name": "x"}, "id": "1"}]),
        AIMessage(content="완료"),
    ])
    _agent(model, ws, lambda n, a: True, extra_tools=[fake_mcp_write]).invoke(
        {"messages": [{"role": "user", "content": "ping"}]})
    assert (ws.root / "x.marker").exists()        # 주입된 MCP 자리 도구가 실행됨
