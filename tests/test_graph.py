"""plan 독립 노드 그래프 배선 + enforce_checklist 결정론 게이트 (plan/PLANNER_AGENT.md §3.6, §9)."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import koda.agents.graph as G
from koda.agents.graph import _build_graph, _planner_tools, enforce_checklist
from koda.agents.specs import AGENTS
from koda.agents.streaming import AgentResult
from koda.prompts import load_prompt
from koda.workspace import Workspace


# --- enforce_checklist: LLM 없는 결정론 게이트 ---

def test_checklist_zero_items_means_plan_failure():
    # 형식 이탈(산문만) → 빈 문자열 = "계획 실패, 계획 없이 진행" 경로
    assert enforce_checklist("계획은 다음과 같다. 먼저 파일을 만들고...") == ""
    assert enforce_checklist("") == ""
    # 체크됨([x])·불릿(-)은 계획 항목이 아니다
    assert enforce_checklist("- [x] 이미 한 일\n- 그냥 불릿") == ""


def test_checklist_overflow_truncated_to_five():
    text = "\n".join(f"- [ ] 항목 {i}" for i in range(1, 11))
    out = enforce_checklist(text)
    assert out.count("- [ ]") == 5
    assert out.splitlines()[-1] == "- [ ] 항목 5"  # 앞에서부터 절단


def test_checklist_prose_stripped_items_kept():
    text = (
        "다음은 계획이다:\n\n"
        "- [ ] **A**: `a.py` 수정 — 검증: pytest\n"
        "중간 부연 설명 산문.\n"
        "- [ ] **B**: `b.py` 신규 — 검증: compileall\n\n"
        "이상입니다."
    )
    out = enforce_checklist(text)
    assert out == "- [ ] **A**: `a.py` 수정 — 검증: pytest\n- [ ] **B**: `b.py` 신규 — 검증: compileall"


def test_checklist_indented_items_counted():
    text = "  - [ ] 들여쓴 항목\n- [ ] 일반 항목"
    assert enforce_checklist(text).count("- [ ]") == 2


def test_checklist_crlf_normalized():
    # CRLF 입력에서 \r 이 항목 끝에 섞이면 안 된다 (deepseek 리뷰 High)
    out = enforce_checklist("- [ ] a\r\n- [ ] b\r\n")
    assert out == "- [ ] a\n- [ ] b"
    assert "\r" not in out


# --- _planner_tools: planner 는 벌크 웹검색을 못 받는다 (컨텍스트 폭증 방지) ---

class _FakeTool:
    def __init__(self, name):
        self.name = name


def test_planner_tools_drops_web_search_keeps_docs():
    # 실측: exa web_search_exa 14회 누적이 32768 초과의 원인. context7 압축 조회는 유지.
    tools = [_FakeTool("web_search_exa"), _FakeTool("query-docs"),
             _FakeTool("resolve-library-id"), _FakeTool("firecrawl_scrape")]
    kept = [t.name for t in _planner_tools(tools)]
    assert kept == ["query-docs", "resolve-library-id"]  # 벌크 검색/크롤만 제외


def test_checklist_common_variants_recognized():
    # 인식은 관대하게 — 변형 하나로 계획 전체가 무효화되지 않는다 (deepseek·폴백 리뷰 수렴)
    text = "* [ ] 별표 불릿\n-  [ ] 대시 뒤 공백 2개\n-\t[ ] 탭 구분\n- [  ] 박스 안 공백 2개"
    assert len(enforce_checklist(text).splitlines()) == 4
    # 단, 체크됨([x])·박스 없는 불릿은 여전히 계획 항목이 아니다
    assert enforce_checklist("* [x] 완료 항목\n* 그냥 불릿") == ""


# --- 그래프 배선: plan 노드/엣지 (§3.1, §3.5) ---

def test_planner_spec_registered_markdown_output():
    spec = AGENTS["planner"]
    assert spec.response_format is None  # structured output 아님 — 마크다운 직접 생성


def test_graph_has_plan_node_and_edges():
    ws = Workspace(Path(tempfile.mkdtemp()))
    g = _build_graph(None, None, ws, lambda n, a: True, lambda t: None, lambda p: None, [])
    drawable = g.get_graph()
    nodes = set(drawable.nodes)
    assert {"route", "reader", "plan", "coder", "verify"} <= nodes
    pairs = {(e.source, e.target) for e in drawable.edges}
    assert ("route", "plan") in pairs     # plan_needed=true → plan
    assert ("route", "coder") in pairs    # 단순 편집 → coder 직행
    assert ("plan", "coder") in pairs     # plan → coder 고정 엣지
    assert ("verify", "plan") in pairs    # verify-fail → plan 재수립 (coder 직행 재시도 폐지)
    assert ("verify", "coder") not in pairs


# --- plan 빈 응답 재시도 (§3.3, §9): LLM 없는 결정론 e2e ---

class _SpySpec:
    """AGENTS 항목 대역: build 에 전달된 model 을 기록만 한다 (astream_agent 는 fake)."""

    def __init__(self):
        self.models = []

    def build(self, model, ws, approver, extra_tools=(), memory_text="", store=None, skills=(),
              context_budget=28000):
        self.models.append(model)
        return object()


class _FakeRouter:
    """route 노드용 fast_model 대역 — 항상 edit + plan_needed=True 로 분류."""

    def with_structured_output(self, schema, method=None):
        route_obj = schema(route="edit", plan_needed=True)

        class _S:
            async def ainvoke(self, msgs, config=None):
                return route_obj

        return _S()


class _ReadRouter:
    """route 노드용 fast_model 대역 — 항상 read 로 분류."""

    def with_structured_output(self, schema, method=None):
        route_obj = schema(route="read", plan_needed=False)

        class _S:
            async def ainvoke(self, msgs, config=None):
                return route_obj

        return _S()


class _CaptureSpec(_SpySpec):
    """build 에 전달된 extra_tools 까지 기록한다 (explore 클로저 추출용)."""

    def __init__(self):
        super().__init__()
        self.extra_tools = []

    def build(self, model, ws, approver, extra_tools=(), memory_text="", store=None, skills=(),
              context_budget=28000):
        self.extra_tools = list(extra_tools)
        return super().build(model, ws, approver)


def test_reader_and_explore_use_differential_models(monkeypatch, tmp_path):
    """에이전트별 thinking 예산 차등 배선: reader 와 explore 가 각자의
    thinking_budget_tokens 를 담은 서로 다른 모델 인스턴스를 받는다."""
    reader_model, explore_model = object(), object()
    reader_spy, explore_spy = _CaptureSpec(), _SpySpec()
    monkeypatch.setitem(G.AGENTS, "reader", reader_spy)
    monkeypatch.setitem(G.AGENTS, "explore", explore_spy)

    async def fake_astream(agent, messages, on_token, config=None, on_event=None):
        return AgentResult(answer="답")

    monkeypatch.setattr(G, "astream_agent", fake_astream)

    g = _build_graph(reader_model, _ReadRouter(), Workspace(tmp_path), lambda n, a: True,
                     lambda t: None, lambda p: None, [], explore_model=explore_model)
    asyncio.run(g.ainvoke({"task": "질문", "history": []}))
    assert reader_spy.models == [reader_model]       # reader 는 reader 예산 모델

    explore_tool = next(t for t in reader_spy.extra_tools if getattr(t, "name", "") == "explore")
    asyncio.run(explore_tool.ainvoke({"question": "q"}))
    assert explore_spy.models == [explore_model]     # explore 는 explore 예산 모델


def test_plan_uses_fast_model_and_retries_on_empty(monkeypatch, tmp_path):
    think_model, fast_model = object(), _FakeRouter()
    planner_spy, coder_spy = _SpySpec(), _SpySpec()
    monkeypatch.setitem(G.AGENTS, "planner", planner_spy)
    monkeypatch.setitem(G.AGENTS, "coder", coder_spy)

    calls: list[str] = []

    async def fake_astream(agent, messages, on_token, config=None, on_event=None):
        calls.append(messages[-1]["content"])
        if len(calls) == 1:  # plan 1차: 빈 응답
            return AgentResult(answer="", finish_reason="length", empty_reason="length_truncation")
        if len(calls) == 2:  # plan 재시도: 정상 체크리스트
            return AgentResult(answer="- [ ] A\n- [ ] B")
        return AgentResult(answer="완료")  # coder

    monkeypatch.setattr(G, "astream_agent", fake_astream)

    async def fake_judge(model, vtask, config):
        return "pass", "ok"

    monkeypatch.setattr(G, "_judge", fake_judge)

    phases: list[str] = []
    g = _build_graph(think_model, fast_model, Workspace(tmp_path), lambda n, a: True,
                     lambda t: None, phases.append, [])
    final = asyncio.run(g.ainvoke({"task": "복잡 편집 요청", "history": []}))

    assert "plan" in phases and "retry:length_truncation" in phases
    # plan 은 thinking 폭주를 피하려 처음부터 fast_model 을 쓴다 — 재시도도 동일(모델 강등 없음).
    assert planner_spy.models == [fast_model]  # 재시도는 같은 agent 재사용(rebuild 없음)
    assert think_model not in planner_spy.models
    # 재시도 지시 변형: empty_retry 프롬프트가 뒤에 붙는다
    assert calls[1].endswith(load_prompt("empty_retry"))
    # enforce_checklist 통과본이 state 에 실리고 coder 지시에 주입된다 (§3.4)
    assert final["plan_text"] == "- [ ] A\n- [ ] B"
    # 계획 라벨은 바 데이터 라벨(수행 지시는 coder.md 로 이관 — 인라인 프롬프트 제거)
    assert "## 작업 계획\n" in calls[2]
    assert "- [ ] A" in calls[2]


# --- 툴 가시화: run_task 가 extra_callbacks 를 config.callbacks 에 얹는다 (TUI 채널) ---

class _NoTracer:
    callbacks: list = []

    def flush(self):
        pass

    def trace_url(self):
        return None


def _dummy_settings():
    from koda.config import ModelSettings
    return ModelSettings(primary="local", local_api_key="x",
                         local_base_url="http://localhost:9", local_model="m",
                         fallback_enabled=False, openai_api_key="x",
                         openai_base_url="http://localhost:8", openai_model="o")


def test_run_task_appends_extra_callbacks_without_touching_core(monkeypatch, tmp_path):
    """TUI 툴 가시화는 config.callbacks 채널로만 얹힌다(실행 코어·비노출 정책 무변경).
    콜백 미전달(CLI 경로)이면 추가 핸들러가 없어 기존 동작 그대로다."""
    captured: dict = {}

    class _FakeGraph:
        async def ainvoke(self, state, config=None):
            captured["callbacks"] = list((config or {}).get("callbacks") or [])
            return {"answer": "완료", "route": "read"}

    monkeypatch.setattr(G, "_build_graph", lambda *a, **k: _FakeGraph())
    monkeypatch.setattr(G, "build_model", lambda s: (object(), "local", None))
    monkeypatch.setattr(G, "_no_think", lambda m: m)
    monkeypatch.setattr(G, "_thinking", lambda m, b: m)
    monkeypatch.setattr(G, "make_vision_tools", lambda *a, **k: [])
    monkeypatch.setattr(G, "get_tracer", lambda: _NoTracer())

    settings = _dummy_settings()
    sentinel = object()

    asyncio.run(G.run_task("q", Workspace(tmp_path), lambda n, a: True, lambda t: None,
                           settings=settings, mcps_dir=None, extra_callbacks=[sentinel]))
    assert sentinel in captured["callbacks"]          # TUI 경로: 핸들러 주입됨

    asyncio.run(G.run_task("q", Workspace(tmp_path), lambda n, a: True, lambda t: None,
                           settings=settings, mcps_dir=None))
    assert sentinel not in captured["callbacks"]       # CLI 경로: 추가 없음(기존 동작)
