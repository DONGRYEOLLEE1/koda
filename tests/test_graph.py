from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import koda.agents.graph as G
from contextlib import contextmanager
from types import SimpleNamespace
from koda.agents.graph import _build_graph, _planner_tools, enforce_checklist
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


def test_checklist_crlf_normalized():
    # CRLF 입력에서 \r 이 항목 끝에 섞이면 안 된다
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
    # 인식은 관대하게 — 변형 하나로 계획 전체가 무효화되지 않는다
    text = "* [ ] 별표 불릿\n-  [ ] 대시 뒤 공백 2개\n-\t[ ] 탭 구분\n- [  ] 박스 안 공백 2개"
    assert len(enforce_checklist(text).splitlines()) == 4
    # 단, 체크됨([x])·박스 없는 불릿은 여전히 계획 항목이 아니다
    assert enforce_checklist("* [x] 완료 항목\n* 그냥 불릿") == ""


# --- 그래프 배선: plan 노드/엣지 ---

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


def test_graph_has_answer_node_direct_to_end():
    ws = Workspace(Path(tempfile.mkdtemp()))
    g = _build_graph(None, None, ws, lambda n, a: True, lambda t: None, lambda p: None, [])
    drawable = g.get_graph()
    pairs = {(e.source, e.target) for e in drawable.edges}
    assert ("route", "answer") in pairs
    assert ("answer", "__end__") in pairs     # 일반 질의는 verify 를 거치지 않는다
    assert ("answer", "verify") not in pairs


class _AnswerRouter:
    """route 노드용 fast_model 대역 — 항상 answer 로 분류."""

    def with_structured_output(self, schema, method=None):
        route_obj = schema(route="answer", plan_needed=False)

        class _S:
            async def ainvoke(self, msgs, config=None):
                return route_obj

        return _S()


def test_answer_gets_mcp_only_no_file_tools(monkeypatch, tmp_path):
    # 일반 질의 에이전트는 외부 참조(MCP)만 쥔다 — 파일 도구·explore 가 있으면 지식으로 답할
    # 질문에도 검색부터 한다(실측: "베스쳔이 뭐야?" 에 search_text 2회 + 웹검색 1회).
    spy = _CaptureSpec()
    monkeypatch.setitem(G.AGENTS, "answer", spy)

    async def fake_astream(agent, messages, on_token, config=None, on_event=None):
        return AgentResult(answer="배스천은 폐쇄망의 유일한 출입구 서버다.")

    monkeypatch.setattr(G, "astream_agent", fake_astream)
    mcp = [_FakeTool("web_search_exa")]
    g = _build_graph(object(), _AnswerRouter(), Workspace(tmp_path), lambda n, a: True,
                     lambda t: None, lambda p: None, mcp)
    final = asyncio.run(g.ainvoke({"task": "베스쳔이 뭐야?", "history": []}))
    assert final["route"] == "answer" and final.get("verdict") is None
    names = [getattr(t, "name", "") for t in spy.extra_tools]
    assert names == ["web_search_exa"]        # explore 없음, MCP 만


# --- plan 빈 응답 재시도: LLM 없는 결정론 e2e ---

class _SpySpec:
    """AGENTS 항목 대역: build 에 전달된 model 을 기록만 한다 (astream_agent 는 fake)."""

    def __init__(self):
        self.models = []

    def build(self, model, ws, approver, extra_tools=(), memory_text="", store=None, skills=(),
              context_budget=28000, **kwargs):
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


class _EditRouter:
    """route 노드용 fast_model 대역 — edit + plan_needed=False → coder 직행."""

    def with_structured_output(self, schema, method=None):
        route_obj = schema(route="edit", plan_needed=False)

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
              context_budget=28000, **kwargs):
        self.extra_tools = list(extra_tools)
        return super().build(model, ws, approver)


def test_reader_and_explore_use_differential_models(monkeypatch, tmp_path):
    """에이전트별 thinking 예산 차등 배선: reader 와 explore 가 각자의 thinking_budget_tokens 를 담은 서로 다른 모델 인스턴스를 받는다."""
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
    # enforce_checklist 통과본이 state 에 실리고 coder 지시에 주입된다
    assert final["plan_text"] == "- [ ] A\n- [ ] B"
    # 계획은 데이터 라벨로만 실린다(수행 지시는 coder.md 로 이관 — 인라인 프롬프트 제거)
    assert "## 작업 계획\n" in calls[2]
    assert "- [ ] A" in calls[2]


# --- 빈응답 저비용 복구: 전체 재실행 대신 누적 messages 요약 1콜 (issue #19 병목 2) ---

def test_coder_empty_recovers_without_full_rerun(monkeypatch, tmp_path):
    # 편집을 끝냈는데 최종 요약만 빈 경우: 도구 결과가 담긴 messages 로 요약 복구하고,
    # 에이전트 전체 재실행(도구 재호출)은 하지 않는다.
    coder_spy = _SpySpec()
    monkeypatch.setitem(G.AGENTS, "coder", coder_spy)

    astream_calls: list = []

    async def fake_astream(agent, messages, on_token, config=None, on_event=None):
        astream_calls.append(messages[-1]["content"])
        return AgentResult(answer="", empty_reason="no_content", messages=[object(), object()])

    monkeypatch.setattr(G, "astream_agent", fake_astream)

    recover_calls: list = []

    async def fake_resummarize(model, messages, nudge, on_token, config=None):
        recover_calls.append(messages)
        return "복구된 요약"

    monkeypatch.setattr(G, "resummarize", fake_resummarize)

    async def fake_judge(model, vtask, config):   # verify 는 이 테스트 관심 밖 → pass 로 종료
        return "pass", "ok"

    monkeypatch.setattr(G, "_judge", fake_judge)

    g = _build_graph(object(), _EditRouter(), Workspace(tmp_path), lambda n, a: True,
                     lambda t: None, lambda p: None, [])
    final = asyncio.run(g.ainvoke({"task": "간단 편집", "history": []}))

    assert len(astream_calls) == 1              # coder 1회만 — 전체 재실행 없음
    assert len(recover_calls) == 1              # 저비용 요약 복구 사용
    assert final["answer"] == "복구된 요약"


def test_empty_full_rerun_only_when_no_messages(monkeypatch, tmp_path):
    # messages 조차 통째로 비면(스트림 실패) 요약할 근거가 없어 전체 재실행 폴백을 쓴다.
    coder_spy = _SpySpec()
    monkeypatch.setitem(G.AGENTS, "coder", coder_spy)

    astream_calls: list = []

    async def fake_astream(agent, messages, on_token, config=None, on_event=None):
        astream_calls.append(messages[-1]["content"])
        if len(astream_calls) == 1:
            return AgentResult(answer="", empty_reason="unknown", messages=[])  # 근거 없음
        return AgentResult(answer="재실행 성공", messages=[object()])

    monkeypatch.setattr(G, "astream_agent", fake_astream)

    async def fake_judge(model, vtask, config):   # verify 는 이 테스트 관심 밖 → pass 로 종료
        return "pass", "ok"

    monkeypatch.setattr(G, "_judge", fake_judge)

    g = _build_graph(object(), _EditRouter(), Workspace(tmp_path), lambda n, a: True,
                     lambda t: None, lambda p: None, [])
    final = asyncio.run(g.ainvoke({"task": "편집", "history": []}))

    assert len(astream_calls) == 2                        # 폴백 전체 재실행 1회
    assert astream_calls[1].endswith(load_prompt("empty_retry"))
    assert final["answer"] == "재실행 성공"


# --- 무변경이어도 verify 는 LLM 판정을 거친다: "편집 요청인데 무변경 → FAIL" 안전 게이트 유지 ---

def test_verify_still_judges_when_no_file_change(monkeypatch, tmp_path):
    # 파일 변경이 없다고 판정을 건너뛰면 "편집 요청했는데 아무것도 안 함"을 pass 로 은폐한다.
    # verifier 는 무변경 증거(변경 없음)를 받아 스스로 pass/fail 을 판정해야 한다(재시도 복구 경로 보존).
    coder_spy, planner_spy = _SpySpec(), _SpySpec()
    monkeypatch.setitem(G.AGENTS, "coder", coder_spy)
    monkeypatch.setitem(G.AGENTS, "planner", planner_spy)   # fail → plan 재수립 경로용

    async def fake_astream(agent, messages, on_token, config=None, on_event=None):
        return AgentResult(answer="완료했다고 주장", messages=[object()])   # 파일 변경 없음

    monkeypatch.setattr(G, "astream_agent", fake_astream)

    seen: list = []

    async def fake_judge(model, vtask, config):
        seen.append(vtask)
        return "fail", "변경 없음"

    monkeypatch.setattr(G, "_judge", fake_judge)

    g = _build_graph(object(), _EditRouter(), Workspace(tmp_path), lambda n, a: True,
                     lambda t: None, lambda p: None, [])
    final = asyncio.run(g.ainvoke({"task": "a.txt 를 만들어라", "history": []}))

    assert len(seen) >= 1                                  # 무변경이어도 판정을 건너뛰지 않음
    assert "(변경 없음)" in seen[0]                         # 무변경 증거가 verifier 에 전달됨
    assert final["verdict"] == "fail"                     # 은폐하지 않고 fail 로 노출


# --- resummarize: 누적 messages + nudge 로 요약 1콜 ---

def test_resummarize_recovers_from_messages():
    from langchain_core.messages import AIMessage, HumanMessage

    from koda.agents.streaming import resummarize

    class _M:
        async def ainvoke(self, msgs, config=None):
            assert isinstance(msgs[-1], HumanMessage)     # nudge 가 마지막에 붙는다
            return AIMessage("요약: 서버를 8000 에 재기동함")

    tokens: list = []
    out = asyncio.run(resummarize(_M(), [HumanMessage("작업"), AIMessage("")], "요약하라", tokens.append))
    assert out.startswith("요약:")
    assert "".join(tokens) == out                         # on_token 으로 흘렸다


def test_resummarize_empty_messages_skips_model():
    from koda.agents.streaming import resummarize

    class _Boom:
        async def ainvoke(self, msgs, config=None):
            raise AssertionError("빈 messages 면 모델을 호출하면 안 된다")

    out = asyncio.run(resummarize(_Boom(), [], "요약하라", lambda t: None))
    assert out == ""


# --- 툴 가시화: run_task 가 extra_callbacks 를 config.callbacks 에 얹는다 (TUI 채널) ---

class _NoTracer:
    callbacks: list = []

    def __init__(self):
        self.turns: list = []

    def flush(self):
        pass

    def trace_url(self):
        return None

    @contextmanager
    def turn(self, name, task):
        rec = {"name": name, "task": task}
        self.turns.append(rec)
        yield SimpleNamespace(done=lambda answer: rec.update(answer=answer))


def _dummy_settings():
    from koda.config import ModelSettings
    return ModelSettings(primary="local", local_api_key="x",
                         local_base_url="http://localhost:9", local_model="m",
                         fallback_enabled=False, openai_api_key="x",
                         openai_base_url="http://localhost:8", openai_model="o")


def test_run_task_appends_extra_callbacks_without_touching_core(monkeypatch, tmp_path):
    """TUI 툴 가시화는 config.callbacks 채널로만 얹힌다(실행 코어·비노출 정책 무변경). 콜백 미전달(CLI 경로)이면 기존 동작 그대로다."""
    captured: dict = {}

    class _FakeGraph:
        async def ainvoke(self, state, config=None):
            captured["callbacks"] = list((config or {}).get("callbacks") or [])
            return {"answer": "완료", "route": "read"}

    monkeypatch.setattr(G, "_build_graph", lambda *a, **k: _FakeGraph())
    monkeypatch.setattr(G, "build_model", lambda s: (object(), "local", None))
    monkeypatch.setattr(G, "_no_think", lambda m, s: m)
    monkeypatch.setattr(G, "_thinking", lambda m, s, b: m)
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


def test_run_task_names_trace_by_session_title_and_records_raw_io(monkeypatch, tmp_path):
    """trace 이름은 세션 제목(첫 턴에만 LLM 으로 짓고 이후 턴은 넘겨받음), 입력·출력은 질의·답변 원문이다."""
    calls = []

    class _FakeGraph:
        async def ainvoke(self, state, config=None):
            return {"answer": "완료", "route": "read"}

    async def fake_title(task, settings=None):
        calls.append(task)
        return "세션 제목"

    tracer = _NoTracer()
    monkeypatch.setattr(G, "_build_graph", lambda *a, **k: _FakeGraph())
    monkeypatch.setattr(G, "build_model", lambda s: (object(), "local", None))
    monkeypatch.setattr(G, "_no_think", lambda m, s: m)
    monkeypatch.setattr(G, "_thinking", lambda m, s, b: m)
    monkeypatch.setattr(G, "make_vision_tools", lambda *a, **k: [])
    monkeypatch.setattr(G, "get_tracer", lambda: tracer)
    monkeypatch.setattr(G, "make_title", fake_title)
    settings = _dummy_settings()

    r1 = asyncio.run(G.run_task("첫 질의", Workspace(tmp_path), lambda n, a: True, lambda t: None,
                                settings=settings, mcps_dir=None))
    assert r1["title"] == "세션 제목" and calls == ["첫 질의"]
    r2 = asyncio.run(G.run_task("둘째 질의", Workspace(tmp_path), lambda n, a: True, lambda t: None,
                                settings=settings, mcps_dir=None, trace_name=r1["title"]))
    assert r2["title"] == "세션 제목" and calls == ["첫 질의"]            # 이후 턴은 제목 호출 없음
    assert tracer.turns == [{"name": "세션 제목", "task": "첫 질의", "answer": "완료"},
                            {"name": "세션 제목", "task": "둘째 질의", "answer": "완료"}]


def test_run_task_probes_vision_on_active_provider(monkeypatch, tmp_path):
    """비전 지원 탐색은 LOCAL 이 아니라 실제로 선택된 provider 의 base_url 을 본다."""
    seen = []

    class _FakeGraph:
        async def ainvoke(self, state, config=None):
            return {"answer": "완료", "route": "read"}

    monkeypatch.setattr(G, "_build_graph", lambda *a, **k: _FakeGraph())
    monkeypatch.setattr(G, "build_model", lambda s: (object(), "openai", None))
    monkeypatch.setattr(G, "_no_think", lambda m, s: m)
    monkeypatch.setattr(G, "_thinking", lambda m, s, b: m)
    monkeypatch.setattr(G, "make_vision_tools", lambda ws, model, props_url: seen.append(props_url) or [])
    monkeypatch.setattr(G, "get_tracer", lambda: _NoTracer())
    asyncio.run(G.run_task("q", Workspace(tmp_path), lambda n, a: True, lambda t: None,
                           settings=_dummy_settings(), mcps_dir=None, trace_name="t"))
    assert seen == ["http://localhost:8/props"]          # openai_base_url 기준(local 은 :9)
