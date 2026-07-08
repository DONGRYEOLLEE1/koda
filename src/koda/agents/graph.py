"""분기 멀티에이전트 아키텍처 (langgraph StateGraph + AgentSpec 레지스트리).

router 가 요청을 read/edit 로 분류하고, 분기에 따라 서브에이전트를 실행한다.
verify 는 워크스페이스 **부수효과가 있는 edit 경로에만** 걸린다(검증 = 부수효과 게이트):

    START → route → reader → END                                          (읽기 전용, 검증 없음)
    START → route → [plan] → coder → verify → (fail·재시도<1 → plan) → END  (파일 변경)

plan 은 독립 노드다 (plan/PLANNER_AGENT.md): router 가 복잡 편집(plan_needed)으로 판단했거나
verify-fail 재수립일 때만 경유한다. planner 는 읽기 전용 도구로 탐색하며 마크다운 체크리스트를
on_token 으로 실시간 스트리밍하고, 개수·형식 상한은 enforce_checklist 가 결정론 집행한다.

- 외부 MCP 도구(mcps/ 폴더에서 로드)는 **reader·coder 양쪽에** 주입된다 — 분기는 워크스페이스
  변경 권한의 차이지 지식 접근의 차이가 아니다. MCP 는 async 라 전 경로를 async 로 돌린다.
- verifier 는 tool-loop(왕복 2~3회) 대신, 코드가 모은 워크스페이스 스냅샷 + 실행 답변으로 **단일
  구조화 판정**한다 → 지연을 크게 줄이고 tool-loop 빈응답 오탐도 없앤다. (Qwen 은 추론 모델이라
  왕복 수를 줄이는 것이 핵심. 짧은 max_tokens 캡(512)은 정상 추론을 잘라 오탐을 냈고, 대신 넉넉한
  전역 상한 4096 으로 퇴행성 무한 사고 폭주만 차단한다 — llm.py 참조.)
- on_phase 콜백으로 각 단계 전환(route/read/edit/verify)을 알려 CLI 가 진행 상황을 표시한다.
- 추적은 langfuse 로만 한다(로컬 저장 없음). CallbackHandler 를 config 로 전파해 각 에이전트/툴/
  모델 호출의 입출력과 단계별 token 사용량을 기록한다.
"""
from __future__ import annotations

import re
from typing import Literal, TypedDict
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langchain.tools import tool
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from ..artifacts import ArtifactStore
from ..config import load_model_settings
from ..diffscan import diff_report, snapshot
from ..llm import _no_think, _thinking, build_model
from ..mcp import load_mcp_tools
from ..memory import Memory
from ..prompts import load_prompt
from ..skills import load_skills
from ..tracing import get_tracer
from ..workspace import Workspace
from ..tools.vision import make_vision_tools
from .specs import AGENTS
from .streaming import _to_text, astream_agent
from .verify import _judge


class _Route(BaseModel):
    """라우팅 판정 — json_schema 로 문법 강제 (ROADMAP §2.1). enum 밖 토큰은 생성 불가."""

    # read=파일 도구만, inspect=read-only 셸 조사(investigator), edit=파일 변경 (AGENT_ROSTER §4)
    route: Literal["read", "inspect", "edit"]
    # 복잡 편집(복수 파일·테스트 요구·광범위 변경) 여부 — plan 서브단계 가동 조건 (§3.3).
    # 판단은 router LLM, 가동·개수 제한은 koda.
    plan_needed: bool = False


class _State(TypedDict, total=False):
    task: str
    history: list      # 멀티턴 히스토리 [{"role","content"}] — user/assistant 최종답변만
    route: str
    answer: str
    verdict: str
    verify_detail: str
    retries: int        # verify-fail 재시도 횟수
    empty_reason: str   # 빈 final 분류: no_content | length_truncation | unknown (§1.1)
    empty_retries: int  # 빈 응답 재시도 횟수 (§1.3)
    plan_needed: bool   # router 의 복잡 편집 판단 (§3.3)
    plan_text: str      # 수립된 마크다운 체크리스트 계획 (coder 지시·verify 증거에 포함)


# [ \t]* 이어야 한다 — \s* 는 MULTILINE 에서 앞 빈 줄의 개행까지 삼켜 항목에 \n 이 앞붙는다.
# 인식은 관대하게(-/* 불릿, 유연한 공백, 빈 박스), 절단은 결정론으로 — 변형 하나로 계획
# 전체가 무효화되는 것을 막는다 (deepseek·폴백 리뷰 수렴 Medium). 체크됨([x])은 제외.
_CHECK_ITEM = re.compile(r"^[ \t]*[-*][ \t]+\[[ \t]*\][ \t]+.+$", re.MULTILINE)


def enforce_checklist(plan_text: str, max_items: int = 5) -> str:
    """`- [ ]` 항목을 결정론 파싱해 상한을 집행한다 (PLANNER_AGENT §3.6).

    structured output 을 포기해도 "임계·게이트는 koda 결정론 집행" 원칙은 유지한다 —
    "5개 이내" 를 프롬프트 부탁으로 격하하지 않는다. (개수 판단은 planner.md 가 복잡도
    비례로 지시하고, 상한 초과만 여기서 절단한다)
    - 항목 0개(형식 이탈·산문만 출력) → 빈 문자열 = "계획 실패, 계획 없이 진행" 경로
    - max_items 초과 → 앞에서부터 절단 (LLM 불복종을 koda가 차단)
    - 항목 사이의 부연 텍스트는 버리고 체크리스트만 남긴다 (coder 지시 오염 방지)
    """
    # CRLF 정규화 — $ 는 \n 앞에만 매칭되므로 \r 이 항목 끝에 섞여 들어온다 (deepseek 리뷰 High)
    items = _CHECK_ITEM.findall(plan_text.replace("\r\n", "\n"))
    return "\n".join(items[:max_items])


# planner 는 계획만 세운다. 벌크 웹검색/크롤 도구를 쥐면 계획 단계에서 과다 조사로 히스토리를
# 폭증시켜 컨텍스트 창을 넘긴다. 조사는 reader/coder 로 미루고, planner MCP 툴셋에서 이 부류만 결정론적으로 뺀다.
# context7(query-docs·resolve-library-id) 같은 압축 문서조회는 가벼워 그대로 남긴다.
_PLANNER_TOOL_DENY = ("web_search", "crawl", "scrape", "deep_research")


def _planner_tools(mcp_tools: list) -> list:
    """planner 에 넘길 MCP 도구에서 벌크 웹검색/크롤 부류를 이름으로 제외한다."""
    return [t for t in mcp_tools
            if not any(k in (getattr(t, "name", "") or "").lower() for k in _PLANNER_TOOL_DENY)]


def _build_graph(model, fast_model, ws: Workspace, approver, on_token, on_phase, mcp_tools,
                 memory_text="", store=None, explore_model=None, skills=(), context_budget=16000):
    # explore 는 reader 와 다른 reasoning 예산을 쓸 수 있다 (미지정 시 reader 와 동일)
    explore_model = model if explore_model is None else explore_model
    # pre-edit 스냅샷 (§2.2): coder 최초 진입 시 1회 채운다. verify-fail 재시도에도 유지 —
    # diff 는 "턴 시작 대비" 누적이어야 재시도가 만든 변경까지 판정 증거에 잡힌다.
    pre_edit: dict = {}
    # explore 위임 상한 (§3.1): 턴당 1회 — reader↔explore 무한 핑퐁을 koda가 캡.
    explore_used = {"n": 0}

    @tool
    async def explore(question: str) -> str:
        """읽기 전용 탐색 보조 에이전트에 위임한다. 여러 파일·문서를 훑어야 하는 넓은 탐색에
        쓰고, 결과는 요약으로 돌아온다. 단일 파일 확인은 직접 workspace_read_file 을 써라.

        Args:
            question: 탐색으로 알아내야 할 것 (구체적으로)
        """
        if explore_used["n"] >= 1:
            return "오류: 탐색 상한 도달 — 지금까지 수집한 정보로 답하라."
        explore_used["n"] += 1
        on_phase("tool:explore(위임)")
        sub = AGENTS["explore"].build(explore_model, ws, approver, extra_tools=mcp_tools, store=store,
                                      context_budget=context_budget)
        # 요약만 main 히스토리로 — 탐색 과정(파일 덤프·검색 로그)은 서브에이전트 컨텍스트에 격리
        r = await astream_agent(sub, [{"role": "user", "content": question}], lambda t: None)
        return r.answer or "탐색 실패(빈 응답) — 직접 도구로 확인하라."


    async def route(state: _State, config):
        on_phase("route")
        # 멀티턴이면 직전 답변 일부를 맥락으로 제공 — "방금 만든 파일에 추가해줘" 같은
        
        rtask = state["task"]
        hist = state.get("history") or []
        last_answer = next((m["content"] for m in reversed(hist) if m.get("role") == "assistant"), "")
        if last_answer:
            rtask = f"[직전 대화 맥락]\n{last_answer[-400:]}\n\n[분류할 현재 요청]\n{state['task']}"
        msgs = [SystemMessage(load_prompt("router")), HumanMessage(rtask)]
        try:  # json_schema 문법 강제 (no_think 모델) — 형식 이탈이 구조적으로 불가능
            r = await fast_model.with_structured_output(_Route, method="json_schema").ainvoke(
                msgs, config=config
            )
            return {"route": r.route, "plan_needed": r.plan_needed, "retries": 0}
        except Exception:  # 문법 옵션 미지원/실패 → 기존 텍스트 파싱 폴백 (§2.1)
            pass
        resp = await fast_model.ainvoke(msgs, config=config)
        text = _to_text(resp.content).strip().lower()
        first = text.split()[0] if text.split() else ""
        if first.startswith("edit"):
            route_val = "edit"
        elif first.startswith("inspect"):
            route_val = "inspect"
        elif first.startswith("read"):
            route_val = "read"
        elif "edit" in text and "read" not in text and "inspect" not in text:
            route_val = "edit"
        else:  # 애매/malformed → 안전하게 inspect (reader 상위집합·read-only, §4.1)
            route_val = "inspect"
        # 폴백에선 복잡도 판단 불가 → 보수적으로 plan 미가동 (리뷰 Low — 의도 명시)
        return {"route": route_val, "plan_needed": False, "retries": 0}

    async def reader(state: _State, config):
        on_phase("read")
        # 외부 참조 MCP(문서·웹검색)는 워크스페이스를 변경하지 않으므로 reader 에도 준다.
        # (없으면 "최신 버전 기준 분석" 류 read 작업이 낡은 학습 지식으로 오답을 낸다 — 실측)
        agent = AGENTS["reader"].build(model, ws, approver, extra_tools=[*mcp_tools, explore],
                                       memory_text=memory_text, store=store, skills=skills, context_budget=context_budget)
        hist = list(state.get("history") or [])
        msgs = hist + [{"role": "user", "content": state["task"]}]
        res = await astream_agent(agent, msgs, on_token, config, on_event=on_phase)
        empty_retries = 0
        if res.empty:
            # 빈 응답 재시도(1회): 지시 변형 + **no_think 강등** — length 폭주(reasoning 이
            # 출력 예산을 소진)를 같은 조건으로 재현하지 않기 위함 (§1.3)
            on_phase(f"retry:{res.empty_reason}")
            empty_retries = 1
            retry_agent = AGENTS["reader"].build(fast_model, ws, approver, extra_tools=[*mcp_tools, explore],
                                                 memory_text=memory_text, store=store, skills=skills, context_budget=context_budget)
            msgs = hist + [{"role": "user", "content": state["task"] + "\n\n" + load_prompt("empty_retry")}]
            res = await astream_agent(retry_agent, msgs, on_token, config, on_event=on_phase)
        return {"answer": res.answer, "empty_reason": res.empty_reason, "empty_retries": empty_retries}

    async def investigator(state: _State, config):
        on_phase("inspect")
        # read-only 셸 조사 에이전트: 파일 도구 + allowlist read-only 명령(ls/find/grep 등).
        # reader 와 동일 배선(+mcp, +explore) — 분기는 도구 권한 차이지 지식 접근 차이가 아니다.
        agent = AGENTS["investigator"].build(model, ws, approver, extra_tools=[*mcp_tools, explore],
                                             memory_text=memory_text, store=store, skills=skills, context_budget=context_budget)
        hist = list(state.get("history") or [])
        msgs = hist + [{"role": "user", "content": state["task"]}]
        res = await astream_agent(agent, msgs, on_token, config, on_event=on_phase)
        empty_retries = 0
        if res.empty:
            on_phase(f"retry:{res.empty_reason}")
            empty_retries = 1
            retry_agent = AGENTS["investigator"].build(fast_model, ws, approver, extra_tools=[*mcp_tools, explore],
                                                       memory_text=memory_text, store=store, skills=skills, context_budget=context_budget)
            msgs = hist + [{"role": "user", "content": state["task"] + "\n\n" + load_prompt("empty_retry")}]
            res = await astream_agent(retry_agent, msgs, on_token, config, on_event=on_phase)
        return {"answer": res.answer, "empty_reason": res.empty_reason, "empty_retries": empty_retries}

    async def plan(state: _State, config):
        # 독립 plan 노드 (PLANNER_AGENT §3.3): 읽기 전용 도구로 탐색하며 마크다운 체크리스트를
        # on_token 으로 실시간 스트리밍한다. 개수·형식은 enforce_checklist 가 결정론 집행.
        on_phase("plan")
        # 워크스페이스 구조 파악은 planner 가 read-only 셸(ls/tree/find)로 필요할 때 직접 한다.
        # (하드코딩 list_files(".")[:100] 주입 제거 — 정렬상 앞부분이 .omc 등 내부 파일이라 오도)
        task = load_prompt("planner_task").format(task=state["task"])
        # verify-fail 재수립에는 실패 컨텍스트를 반드시 주입한다 — 없으면 같은 입력으로
        # 동일 계획을 재생산해 재시도 예산만 소진한다 (§3.3 교차검증 ①).
        if state.get("verdict") == "fail":
            task += "\n\n" + load_prompt("planner_retry").format(
                plan_text=state.get("plan_text") or "(없음)",
                verify_detail=state.get("verify_detail") or "(없음)",
            )
        # 체크리스트 생성은 구조적 작업이라 깊은 추론이 불필요하다. thinking 모델은 폭주로
        # 시간·토큰만 태우므로(실측 trace e32faab: plan #1 의 52s 가 4096 truncation) coder와 동일하게 fast_model(no_think) 을 쓴다.
        agent = AGENTS["planner"].build(fast_model, ws, approver, extra_tools=_planner_tools(mcp_tools),
                                        memory_text=memory_text, store=store, skills=skills, context_budget=context_budget)
        hist = list(state.get("history") or [])
        res = await astream_agent(agent, hist + [{"role": "user", "content": task}],
                                  on_token, config, on_event=on_phase)
        if res.empty:
            # 이미 no_think 이므로 모델 강등 대신 지시 변형만으로 1회 재시도 (coder 와 동일 패턴).
            on_phase(f"retry:{res.empty_reason}")
            msgs = hist + [{"role": "user", "content": task + "\n\n" + load_prompt("empty_retry")}]
            res = await astream_agent(agent, msgs, on_token, config, on_event=on_phase)
        plan_text = enforce_checklist(res.answer)
        if not plan_text:
            on_phase("plan:실패(계획 없이 진행)")
        return {"plan_text": plan_text}

    async def coder(state: _State, config):
        on_phase("edit")
        if "snap" not in pre_edit:
            pre_edit["snap"] = snapshot(ws.root)
        agent = AGENTS["coder"].build(fast_model, ws, approver, extra_tools=[*mcp_tools, explore],
                                      memory_text=memory_text, store=store, skills=skills, context_budget=context_budget)
        task = state["task"]
        if state.get("plan_text"):  # plan 노드가 수립한 체크리스트 (§3.4). 수행 지시는 coder.md.
            task += "\n\n## 작업 계획\n" + state["plan_text"]
        if state.get("verdict") == "fail":  # 재시도: 검증 피드백 포함
            task += "\n\n" + load_prompt("coder_retry").format(detail=state.get("verify_detail", ""))
        hist = list(state.get("history") or [])
        res = await astream_agent(agent, hist + [{"role": "user", "content": task}], on_token, config, on_event=on_phase)
        empty_retries = state.get("empty_retries", 0)
        if res.empty:  # 빈 응답으로 루프가 편집 전에 끝남 → 지시 변형 후 1회 재시도 (coder 는 이미 no_think)
            on_phase(f"retry:{res.empty_reason}")
            empty_retries += 1
            msgs = hist + [{"role": "user", "content": task + "\n\n" + load_prompt("empty_retry")}]
            res = await astream_agent(agent, msgs, on_token, config, on_event=on_phase)
        retries = state.get("retries", 0) + (1 if state.get("verdict") == "fail" else 0)
        return {"answer": res.answer, "retries": retries,
                "empty_reason": res.empty_reason, "empty_retries": empty_retries}

    async def verify(state: _State, config):
        on_phase("verify")
        # 증거 수집은 결정론 (§2.2): 턴 시작 스냅샷 대비 해시 비교 → 변경 목록 + diff.
        # 신규(untracked) 파일 본문도 diff 에 포함된다. 판단만 LLM 이 한다.
        changed, diff = diff_report(pre_edit.get("snap", {}), snapshot(ws.root))
        vtask = load_prompt("verifier_task").format(
            task=state["task"], answer=state.get("answer", ""),
            changed="\n".join(changed) or "(변경 없음)", diff=diff or "(없음)",
        )
        if state.get("plan_text"):  # 판정 지시는 verifier.md, 여기선 증거 라벨만 붙인다
            vtask += "\n\n[작업 계획(TODO)]\n" + state["plan_text"]
        # 독립 리뷰 (§3.1 review-only): 변경이 크면 diff 만 보는 별도 시선을 verifier 에 공급.
        # 가동 조건은 결정론(변경 2파일+ 또는 diff 2000자+), 소견 내용은 LLM.
        if len(changed) >= 2 or len(diff) > 2000:
            on_phase("review")
            try:
                rv = await fast_model.ainvoke(
                    [SystemMessage(load_prompt("reviewer")),
                     HumanMessage(load_prompt("reviewer_task").format(
                         task=state["task"], changed="\n".join(changed), diff=diff))],
                    config=config,
                )
                review = _to_text(rv.content).strip()
                if review:
                    vtask += "\n\n[독립 리뷰 소견]\n" + review
            except Exception:
                pass  # 리뷰 실패는 판정을 막지 않는다 (verifier 가 diff 로 직접 판정)
        # no_think 모델로 판정 (§2.1): 문법 강제(json_schema)는 reasoning 블록과 충돌할 수
        # 있어 thinking 을 끈다. 판정 근거는 스냅샷 증거이므로 장문 사고가 필요하지 않다.
        verdict, detail = await _judge(fast_model, vtask, config)
        return {"verdict": verdict, "verify_detail": detail}

    def plan_edge(state: _State):
        """edit 경로 진입 3분기 (PLANNER_AGENT §3.1)."""
        if state.get("verdict") == "fail":
            return "plan"   # verify-fail → 반드시 plan 재수립 (실패 컨텍스트 포함, §3.3)
        if state.get("plan_needed"):
            return "plan"   # router 판단 → plan
        return "coder"      # 단순 편집 → coder 직행

    def route_edge(state: _State):
        if state["route"] == "edit":
            return plan_edge(state)
        return "investigator" if state["route"] == "inspect" else "reader"

    def verify_edge(state: _State):
        # fail 재시도는 coder 재실행이 아니라 plan 재수립부터 (§3.5). retries 증가는 coder 가
        # verdict=="fail" 을 보고 수행하므로 plan 경유에도 정확히 1회만 증가한다.
        if state["route"] == "edit" and state["verdict"] == "fail" and state.get("retries", 0) < 1:
            return "plan"
        return END

    g = StateGraph(_State)
    g.add_node("route", route)
    g.add_node("reader", reader)
    g.add_node("investigator", investigator)
    g.add_node("plan", plan)
    g.add_node("coder", coder)
    g.add_node("verify", verify)
    g.add_edge(START, "route")
    g.add_conditional_edges("route", route_edge,
                            {"reader": "reader", "investigator": "investigator", "plan": "plan", "coder": "coder"})
    # read 경로는 verify 를 거치지 않는다: 검증은 "워크스페이스 부수효과"에 대한 게이트이고,
    # 부수효과 없는 분석·답변의 산문 품질 평가는 비용(수십 초)만 들고 신뢰할 수 없다 — 실측상
    # 낡은 학습 지식으로 잘못된 분석을 pass 시키는 고무도장에 불과했다.
    g.add_edge("reader", END)
    g.add_edge("investigator", END)
    g.add_edge("plan", "coder")
    g.add_edge("coder", "verify")
    g.add_conditional_edges("verify", verify_edge, {"plan": "plan", END: END})
    return g.compile()


async def run_task(
    task: str, ws: Workspace, approver, on_token,
    on_phase=None, settings=None, mcps_dir="mcps", history: list | None = None,
    mcp_tools: list | None = None, run_id: str | None = None,
    extra_callbacks: list | None = None,
) -> dict:
    """단일 턴 실행.

    history: 멀티턴 맥락(이전 user/assistant 쌍)을 에이전트에 전달.
    mcp_tools: 사전 로드된 MCP 도구(멀티턴 REPL 이 세션당 1회 로드해 재사용 — 툴 목록이
    안정되어야 프롬프트 prefix 캐시가 유지된다). None 이면 mcps_dir 에서 로드한다.
    run_id: artifact 네임스페이스(§2.3). chat 은 세션 id 를 넘겨 턴 간 이어 쓴다.
    extra_callbacks: langchain config.callbacks 에 추가로 얹을 핸들러(예: TUI 툴 가시화).
    langfuse 추적과 동일 채널이라 실행 로직을 건드리지 않는다. CLI 는 넘기지 않는다.
    """
    settings = settings or load_model_settings()
    model, provider, _reason = build_model(settings)
    on_phase = on_phase or (lambda p: None)

    if mcp_tools is None:
        mcp_tools = []
        if mcps_dir:  # --no-mcp 면 mcps_dir=None → 외부 로드 자체를 생략
            try:
                mcp_tools = await load_mcp_tools(mcps_dir, on_log=lambda m: on_phase(f"mcp:{m}"))
            except Exception as e:
                on_phase(f"mcp:로드 실패 {type(e).__name__}")

    # 로컬 Qwen 에만 thinking 비활성 적용 (OpenAI 폴백은 chat_template_kwargs 를 모른다)
    fast_model = _no_think(model) if provider == "local" else model
    # reader·explore: thinking 활성 + **에이전트별 reasoning 예산 차등 적용** (.env 재정의 가능).
    # 각각 별도 인스턴스라 서로/fast_model 에 영향 없다. 서버는 --reasoning-budget 플래그
    # 없이 떠야 요청별 값이 적용된다 (b9430: CLI 값이 요청 값에 우선 — llm._thinking 주석).
    if provider == "local":
        reader_model = _thinking(model, settings.thinking_budget_reader)
        explore_model = _thinking(model, settings.thinking_budget_explore)
    else:
        reader_model = explore_model = model
    memory_text = Memory(ws.root).read()  # 영구 메모리 주입 (없으면 빈 문자열)
    skills = load_skills(ws.root)  # 워크스페이스 스킬 (없으면 빈 리스트)
    artifact_ns = run_id or uuid4().hex[:8]
    store = ArtifactStore(ws.root, artifact_ns)  # 절단 원문 보존 (§2.3)
    # vision 도구 (§3.2): 서버가 mmproj 미탑재면 도구가 복구 가능한 에러 문자열을 반환한다
    props_url = settings.local_base_url.rstrip("/").removesuffix("/v1") + "/props"
    extra_tools = [*mcp_tools, *make_vision_tools(ws, fast_model, props_url)]
    tracer = get_tracer()
    graph = _build_graph(reader_model, fast_model, ws, approver, on_token, on_phase, extra_tools,
                         memory_text, store=store, explore_model=explore_model, skills=skills,
                         context_budget=settings.context_budget_tokens)
    # CallbackHandler 가 이 graph 실행을 하나의 langfuse trace 로 자동 기록한다
    # (각 에이전트/툴/모델 호출 = 관측, 단계별 token 사용량 포함).
    # artifact_ns 를 trace metadata 에 실어 langfuse ↔ .koda/artifacts 상호참조를 유지 (리뷰 Low)
    config = {"callbacks": [*tracer.callbacks, *(extra_callbacks or [])], "run_name": task[:60],
              "metadata": {"provider": provider, "artifact_ns": artifact_ns}}
    final = await graph.ainvoke({"task": task, "history": history or []}, config=config)
    tracer.flush()
    answer = final.get("answer", "")
    return {
        "final": answer,
        "route": final.get("route"),
        "verdict": final.get("verdict"),
        "verify_detail": final.get("verify_detail", ""),
        # 빈 final 실패 계약 (§1.2): run 은 non-zero exit, chat 은 히스토리 저장 생략에 사용
        "empty_final": not answer.strip(),
        "empty_reason": final.get("empty_reason", ""),
        "empty_retries": final.get("empty_retries", 0),
        "retries": final.get("retries", 0),
        "trace_url": tracer.trace_url(),
    }
