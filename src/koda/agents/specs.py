from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from langchain.agents import create_agent

from ..middleware import HistoryTrimMiddleware, PermissionMiddleware, ToolOutputTrimMiddleware
from ..prompts import load_prompt
from ..tools import make_all_tools, make_readonly_tools
from ..tools.artifact import make_artifact_tools
from ..tools.skill import make_skill_tools
from ..workspace import Workspace


@dataclass
class AgentSpec:
    """선언적 에이전트 정의 (PLAN §8.1). 툴셋·프롬프트·구조화출력을 담는다."""

    name: str
    toolset: Callable[[Workspace], list]
    response_format: type | None = None

    def build(self, model, ws, approver, extra_tools=(), memory_text: str = "", store=None,
              skills=(), context_budget: int = 16000):
        system = load_prompt(self.name)
        # 캐시 prefix 순서 (agy 리뷰): 정적일수록 앞으로. 스킬 파일은 거의 불변이라 memory 앞에
        # 둬서, 세션 누적으로 성장하는 memory 가 바뀌어도 스킬 구간 캐시가 보존되게 한다.
        if skills:
            system += "\n\n" + load_prompt("skills_intro") + "\n" + "\n".join(
                f"- {s.name}: {s.description}" for s in skills
            )
        if memory_text:  # 영구 메모리는 스킬 뒤 고정 위치 (더 동적 → prefix 뒤쪽)
            system += "\n\n## 프로젝트 메모리 (이 워크스페이스에 대해 기억된 사실)\n" + memory_text
        tools = self.toolset(ws) + list(extra_tools)
        if store is not None:  # 절단된 도구 출력의 원문 재조회 경로 (§2.3)
            tools += make_artifact_tools(store)
        if skills:  # 스킬 본문 조회 도구 (읽기 전용)
            tools += make_skill_tools(list(skills))
        return create_agent(
            model,
            tools,
            system_prompt=system,
            response_format=self.response_format,
            # 순서: 권한 게이트 → 도구출력 1건 상한 → 턴 누적 프롬프트 상한(모델 호출 직전).
            middleware=[
                PermissionMiddleware(approver, self.name),
                ToolOutputTrimMiddleware(store=store),
                HistoryTrimMiddleware(max_prompt_tokens=context_budget),
            ],
        )


AGENTS = {
    "reader": AgentSpec("reader", make_readonly_tools),
    "coder": AgentSpec("coder", make_all_tools),
    "explore": AgentSpec("explore", make_readonly_tools),
    "planner": AgentSpec("planner", make_readonly_tools),
}
