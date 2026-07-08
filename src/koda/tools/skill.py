"""스킬 본문 조회 도구 (읽기 전용). 에이전트가 skill(name) 으로 SKILL.md 전체 지침을 불러온다."""

from __future__ import annotations

from langchain.tools import tool

from ..skills import Skill, skill_body


def make_skill_tools(skills: list[Skill]) -> list:
    """스킬 목록에 바인딩된 조회 도구. skills 가 비면 빈 리스트."""

    if not skills:
        return []

    by_name = {s.name: s for s in skills}
    names_list = "\n".join(f"  - {s.name}: {s.description}" for s in skills)

    @tool
    def skill(name: str) -> str:
        """관련 스킬의 전체 지침을 불러온다. 현재 요청에 관련된 스킬이 있다고 판단될 때만 호출하라.

        Args:
            name: 불러올 스킬 이름
        """
        s = by_name.get(name)
        if s is None:
            return (
                f"'{name}' 스킬이 없습니다. 사용 가능한 스킬:\n"
                f"{names_list}"
            )
        return skill_body(s)

    return [skill]
