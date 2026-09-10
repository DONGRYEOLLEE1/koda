from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from langchain.agents import create_agent

from ..middleware import (
    HistoryTrimMiddleware,
    PermissionMiddleware,
    StepBudgetMiddleware,
    ToolOutputTrimMiddleware,
)
from ..config import DEFAULT_CONTEXT_BUDGET
from ..middleware.history import tool_schema_tokens
from ..prompts import load_prompt
from ..tools import make_all_tools, make_inspect_tools, make_readonly_tools
from ..tools.artifact import make_artifact_tools
from ..tools.skill import make_skill_tools
from ..workspace import Workspace


@dataclass
class AgentSpec:
    """선언적 에이전트 정의. 툴셋·프롬프트·구조화출력을 담는다."""

    name: str
    toolset: Callable[[Workspace], list]
    response_format: type | None = None
    max_steps: int = 25  # 이 에이전트 1회 실행당 도구 호출 하드 캡 (도구 루프 폭주 방지)

    def system_prompt(self, memory_text: str = "", skills=(), context_budget: int = DEFAULT_CONTEXT_BUDGET) -> str:
        """시스템 프롬프트 조립. 캐시 prefix 순서: 정적(프롬프트·예산·스킬) → 메모리 → 날짜(매일 바뀜)."""
        system = load_prompt(self.name)
        # 컨텍스트 예산 소프트 가이드 — 하드 집행은 StepBudget·HistoryTrim 이 하고, 이 문구는 모델 유도용이다.
        system += (
            f"\n\n## 컨텍스트 예산\n\n"
            f"실효 컨텍스트 예산은 약 {context_budget // 1000}k 토큰이다(무제한 아님). 아끼도록 도구를 "
            f"골라라: 파일은 전문 대신 **필요한 범위만 발췌**해 읽고, 여러 파일은 반복 호출하지 말고 "
            f"**한 콜에 일괄로 훑으며**(셸 파이프·글롭), 넓은 탐색은 explore 에 위임해 요약만 받는다. "
            f"예산을 넘기면 오래된 히스토리가 잘려나가니 처음부터 핵심만 컨텍스트에 들여라."
        )
        # 캐시 prefix 순서: 정적(스킬)이 동적(memory)보다 앞이어야 growing memory 가 캐시를 깨지 않는다.
        if skills:
            system += "\n\n" + load_prompt("skills_intro") + "\n" + "\n".join(
                f"- {s.name}: {s.description}" for s in skills
            )
        if memory_text:  # 영구 메모리는 스킬 뒤 고정 위치 (더 동적 → prefix 뒤쪽)
            system += "\n\n## 메모리 (이 워크스페이스에 대해 기억된 사실·경험·규칙)\n" + memory_text
        # 오늘 날짜(KST)를 맨 뒤에 둔다 — 없으면 웹검색으로 최신 자료를 받아도 '최근'·'올해'를 학습 시점 기준으로 해석한다.
        # 시각(분)까지 넣으면 호출마다 prefix 가 바뀌어 프롬프트 캐시가 깨지므로 날짜만 넣는다.
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        system += (f"\n\n## 현재 날짜\n\n오늘은 {now:%Y-%m-%d} ({'월화수목금토일'[now.weekday()]}요일, KST) 이다. "
                   f"날짜·기간·최신 여부는 네 학습 시점이 아니라 이 날짜를 기준으로 판단한다.")
        return system

    def build(self, model, ws, approver, extra_tools=(), memory_text: str = "", store=None,
              skills=(), context_budget: int = DEFAULT_CONTEXT_BUDGET, mcp_auto_approve=()):
        system = self.system_prompt(memory_text, skills, context_budget)
        tools = self.toolset(ws) + list(extra_tools)
        if store is not None:  # 절단된 도구 출력의 원문 재조회 경로
            tools += make_artifact_tools(store)
        if skills:  # 스킬 본문 조회 도구 (읽기 전용)
            tools += make_skill_tools(list(skills))
        return create_agent(
            model,
            tools,
            system_prompt=system,
            response_format=self.response_format,
            # 순서: 스텝 상한(최외곽) → 권한 게이트 → 도구출력 1건 상한 → 턴 누적 프롬프트 상한.
            middleware=[
                StepBudgetMiddleware(self.max_steps),
                PermissionMiddleware(approver, self.name, mcp_auto_approve),
                ToolOutputTrimMiddleware(store=store, context_budget=context_budget),
                # 도구 스키마는 MCP 를 붙이면 고정 예약값을 크게 넘는다 — 여기서 실제 목록을 재서 넘긴다.
                HistoryTrimMiddleware(max_prompt_tokens=context_budget,
                                      tools_reserve=tool_schema_tokens(tools)),
            ],
        )


# max_steps 는 조사 경로가 관측된 40콜 폭주보다 확실히 낮게, coder 는 편집·검증 루프용이라 여유를 둔다.
AGENTS = {
    # answer 는 파일 도구가 없다 — 일반 질의에 워크스페이스 도구를 쥐여 주면 지식으로 답할 질문에도 검색부터 한다.
    "answer": AgentSpec("answer", lambda ws: [], max_steps=5),
    "reader": AgentSpec("reader", make_readonly_tools, max_steps=20),
    "investigator": AgentSpec("investigator", make_inspect_tools, max_steps=25),
    "coder": AgentSpec("coder", make_all_tools, max_steps=35),
    "explore": AgentSpec("explore", make_readonly_tools, max_steps=15),
    "planner": AgentSpec("planner", make_inspect_tools, max_steps=15),
}
