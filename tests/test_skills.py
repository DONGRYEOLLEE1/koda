"""핵심 모듈: skills (워크스페이스 스킬 발견·주입·조회 도구). plan/SKILLS.md §6.

판단은 LLM 이 하므로(스킬 발동 여부) 여기선 하네스 몫만 검증한다:
발견(frontmatter 파싱)·빈 폴더·본문 조회·SAFE 게이트·프롬프트 주입 순서.
"""
from __future__ import annotations

from build_harness.agents.specs import AGENTS
from build_harness.skills import Skill, load_skills, skill_body
from build_harness.tools import SAFE_TOOLS
from build_harness.tools.skill import make_skill_tools


def _write_skill(root, name, frontmatter, body="본문 지침."):
    d = root / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")
    return d


# --- 발견 + frontmatter 파싱 ---

def test_load_skills_parses_frontmatter(tmp_path):
    _write_skill(tmp_path, "frontend-design",
                 "name: frontend-design\ndescription: 뚜렷한 시각 디자인 지침")
    skills = load_skills(str(tmp_path))
    assert len(skills) == 1
    assert skills[0].name == "frontend-design"
    assert skills[0].description == "뚜렷한 시각 디자인 지침"
    assert skills[0].path.endswith("frontend-design/SKILL.md")


def test_load_skills_missing_dir_is_empty(tmp_path):
    # skills/ 없음 = 정상(스킬은 선택 기능) → 무회귀
    assert load_skills(str(tmp_path)) == []


def test_load_skills_name_falls_back_to_dirname(tmp_path):
    _write_skill(tmp_path, "my-skill", "description: 이름 없는 스킬")
    skills = load_skills(str(tmp_path))
    assert skills[0].name == "my-skill"  # frontmatter name 없음 → 디렉터리명


def test_load_skills_yaml_multiline_description(tmp_path):
    # agy 리뷰 #5: yaml 우선 경로가 멀티라인 description 을 처리한다
    _write_skill(tmp_path, "s1", "name: s1\ndescription: >\n  첫 줄\n  둘째 줄")
    skills = load_skills(str(tmp_path))
    assert "첫 줄" in skills[0].description and "둘째 줄" in skills[0].description


def test_load_skills_minimal_parser_fallback_when_no_yaml(tmp_path, monkeypatch):
    # yaml 미설치 상황을 강제해 최소 파서 폴백 경로를 직접 검증(agy 최종 리뷰 갭)
    import sys
    monkeypatch.setitem(sys.modules, "yaml", None)  # import yaml → ImportError
    _write_skill(tmp_path, "s1", "name: s1\ndescription: 단일 줄 설명")
    skills = load_skills(str(tmp_path))
    assert skills[0].name == "s1" and skills[0].description == "단일 줄 설명"


# --- 본문 조회 도구 ---

def test_skill_tool_returns_body_and_guides_on_unknown(tmp_path):
    _write_skill(tmp_path, "s1", "name: s1\ndescription: d", body="# 지침\n따라 하라.")
    skills = load_skills(str(tmp_path))
    (tool,) = make_skill_tools(skills)
    out = tool.invoke({"name": "s1"})
    assert "따라 하라" in out and "---" not in out.split("\n")[0]  # frontmatter 제외 본문
    miss = tool.invoke({"name": "없는스킬"})
    assert "없습니다" in miss and "s1" in miss  # 에러 아닌 안내 + 사용 가능 목록


def test_make_skill_tools_empty_when_no_skills():
    assert make_skill_tools([]) == []


def test_skill_body_strips_frontmatter(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text("---\nname: x\n---\n순수 본문", encoding="utf-8")
    assert skill_body(Skill("x", "d", str(p), str(p.parent))) == "순수 본문"


# --- 안전: skill 은 읽기 전용 SAFE ---

def test_skill_is_safe_tool():
    assert "skill" in SAFE_TOOLS  # 승인 게이트 없이 통과(본문 반환만)


# --- 프롬프트 주입: build 가 create_agent 에 넘기는 실제 system 을 가로채 검증 ---

def test_build_injects_skill_list_and_orders_before_memory(monkeypatch):
    import build_harness.agents.specs as S
    captured = {}

    def fake_create_agent(model, tools, system_prompt, **kw):
        captured["system"] = system_prompt
        captured["tool_names"] = [getattr(t, "name", "") for t in tools]
        return object()

    monkeypatch.setattr(S, "create_agent", fake_create_agent)
    skills = [Skill("frontend-design", "뚜렷한 시각 디자인 지침", "", "")]
    AGENTS["coder"].build(object(), None, lambda n, a: True,
                          memory_text="기억된 사실", skills=skills)

    sys = captured["system"]
    assert "frontend-design: 뚜렷한 시각 디자인 지침" in sys        # L1 목록 주입
    assert "사용 가능한 스킬" in sys                                # skills_intro 헤더
    # 캐시 prefix 순서(agy #6): 정적 skills 가 동적 memory 보다 앞
    assert sys.index("사용 가능한 스킬") < sys.index("프로젝트 메모리")
    assert "skill" in captured["tool_names"]                        # skill 도구 주입


def test_build_without_skills_is_unchanged(monkeypatch):
    # skills=() 면 스킬 섹션·도구가 전혀 없다 (무회귀 — 수용기준 §7-3)
    import build_harness.agents.specs as S
    captured = {}
    monkeypatch.setattr(S, "create_agent",
                        lambda model, tools, system_prompt, **kw: captured.update(
                            system=system_prompt, names=[getattr(t, "name", "") for t in tools]) or object())
    AGENTS["coder"].build(object(), None, lambda n, a: True)
    assert "사용 가능한 스킬" not in captured["system"]
    assert "skill" not in captured["names"]
