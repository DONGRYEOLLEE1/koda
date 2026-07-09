"""Permission/HITL middleware: 위험 도구 실행 전 게이트 (AgentMiddleware).

**안전(읽기 전용) 도구만 자동 허용**하고, 그 외 모든 도구(내장 위험 도구 + MCP/외부 주입 도구)는
approver 승인 없이는 실행하지 않는다(default-deny). 동기(wrap_tool_call)·비동기(awrap_tool_call)
실행 경로 모두에서 게이트한다 — MCP 도구는 async 로 실행되므로 양쪽이 필요하다.
"""
from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from ..denygate import check_command
from ..tools import SAFE_TOOLS


def _command_text(call: dict) -> str:
    args = call.get("args") or {}
    v = args.get("argv") or args.get("command") or ""
    return " ".join(map(str, v)) if isinstance(v, list) else str(v)


class PermissionMiddleware(AgentMiddleware):
    """approver: (tool_name, args) -> bool. SAFE_TOOLS 외 모든 도구를 게이트한다.

    approver 이전에 정적 deny gate(denygate)가 먼저 돈다 — hard deny 는 `--yes`(auto 승인)로도
    우회 불가, confirm 등급은 대화형 승인만 허용한다(ROADMAP §2.5).
    """

    def __init__(self, approver, agent: str = "main", mcp_auto_approve=()):
        super().__init__()
        self._approver = approver
        self._agent = agent
        self._mcp_auto = frozenset(mcp_auto_approve)  # 자동 승인 허용된 읽기 전용 MCP 이름(opt-in)

    def _tool_error(self, call, msg):
        return ToolMessage(content=msg, tool_call_id=call.get("id", ""), name=call.get("name"), status="error")

    def _denied(self, request):
        """게이트 검사. 거부면 ToolMessage(error), 허용이면 None."""
        call = getattr(request, "tool_call", {}) or {}
        name = call.get("name")
        if name == "run_command":
            hit = check_command(_command_text(call))
            if hit is not None:
                tier, reason = hit
                if tier == "deny":
                    return self._tool_error(
                        call, f"거부됨(정적 차단): {reason}. 이 명령은 승인 여부와 무관하게 실행할 수 없다."
                    )
                if getattr(self._approver, "auto", False):  # confirm: 자동 승인 무효
                    return self._tool_error(
                        call, f"거부됨(HITL 필수): {reason}. 자동 승인(--yes)으로는 실행할 수 없다."
                    )
        if name not in SAFE_TOOLS and name not in self._mcp_auto and not self._approver(name, call.get("args")):
            return ToolMessage(
                content=f"거부됨: 사용자가 '{name}' 실행을 승인하지 않았습니다.",
                tool_call_id=call.get("id", ""),
                name=name,
                status="error",
            )
        return None

    def wrap_tool_call(self, request, handler):
        return self._denied(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        return self._denied(request) or await handler(request)
