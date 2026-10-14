from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

import koda.agents.graph as G
from contextlib import contextmanager
from types import SimpleNamespace
from koda.agents.graph import _build_graph, _planner_tools, _vision_methods, enforce_checklist
from koda.agents.streaming import AgentResult
from koda.agents.toollog import ToolLog
from koda.llm import STRUCTURED_METHODS, set_structured_method
from koda.prompts import load_prompt
from koda.workspace import Workspace


@pytest.fixture(autouse=True)
def _reset_structured_method():
    # run_task pins the structured-output method process-wide; later test files must not inherit it.
    yield
    set_structured_method("")


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


def _worked_log():
    """도구를 실제로 돌린 턴의 기록. 이게 없으면 '도구 0회 + 무변경' 결정론 게이트가 먼저 fail 을 낸다."""
    log = ToolLog()
    log.calls.append(("edit_file", "a.py", True, "수정함"))
    return log


def test_planner_tools_drops_web_search_keeps_docs():
    # 실측: exa web_search_exa 14회 누적이 32768 초과의 원인. context7 압축 조회는 유지.
    tools = [_FakeTool("web_search_exa"), _FakeTool("query-docs"),
             _FakeTool("resolve-library-id"), _FakeTool("firecrawl_scrape")]
    kept = [t.name for t in _planner_tools(tools)]
    assert kept == ["query-docs", "resolve-library-id"]  # 벌크 검색/크롤만 제외


def test_planner_tools_drops_render_and_diff_keeps_parse():
    # The planner may read a capture while planning but never renders or diffs; that is the coder's loop.
    tools = [_FakeTool("parse_screen"), _FakeTool("render_page"), _FakeTool("screen_diff"), _FakeTool("analyze_image")]
    assert [t.name for t in _planner_tools(tools)] == ["parse_screen", "analyze_image"]


def test_vision_methods_follow_vision_structured_method():
    assert _vision_methods(SimpleNamespace(vision_structured_method="")) is None
    assert _vision_methods(SimpleNamespace(vision_structured_method="json_schema")) == ("json_schema",)
    assert _vision_methods(SimpleNamespace(vision_structured_method="auto")) == STRUCTURED_METHODS


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
    # verify-fail 재시도는 라우터 판단을 따른다: 복잡 편집이면 plan 재수립, 단순 편집이면 coder 직행
    assert ("verify", "plan") in pairs
    assert ("verify", "coder") in pairs


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
                     lambda t: None, phases.append, [], tool_log=_worked_log())
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
                     lambda t: None, lambda p: None, [], tool_log=_worked_log())
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
                     lambda t: None, lambda p: None, [], tool_log=_worked_log())
    final = asyncio.run(g.ainvoke({"task": "편집", "history": []}))

    assert len(astream_calls) == 2                        # 폴백 전체 재실행 1회
    assert astream_calls[1].endswith(load_prompt("empty_retry"))
    assert final["answer"] == "재실행 성공"


# --- 무변경이어도 verify 는 LLM 판정을 거친다: "편집 요청인데 무변경 → FAIL" 안전 게이트 유지 ---

def test_no_tool_and_no_change_fails_without_llm(monkeypatch, tmp_path):
    """도구 0회 + 무변경이면 아무 일도 없었던 것이므로 LLM 판정 없이 fail 이다. 답변의 수행 주장은 증거가 아니다."""
    coder_spy, planner_spy = _SpySpec(), _SpySpec()
    monkeypatch.setitem(G.AGENTS, "coder", coder_spy)
    monkeypatch.setitem(G.AGENTS, "planner", planner_spy)

    async def fake_astream(agent, messages, on_token, config=None, on_event=None):
        return AgentResult(answer="완료했습니다. kill 로 전부 종료했습니다.", messages=[object()])

    monkeypatch.setattr(G, "astream_agent", fake_astream)
    seen: list = []

    async def fake_judge(model, vtask, config):
        seen.append(vtask)
        return "pass", "ok"

    monkeypatch.setattr(G, "_judge", fake_judge)
    g = _build_graph(object(), _EditRouter(), Workspace(tmp_path), lambda n, a: True,
                     lambda t: None, lambda p: None, [])
    final = asyncio.run(g.ainvoke({"task": "프론트 백엔드 다 죽여줘", "history": []}))

    assert final["verdict"] == "fail"
    assert seen == []                                      # LLM 판정을 부르지도 않는다
    assert "실행된 도구가 없고" in final["verify_detail"]


def test_no_tool_answer_triggers_one_retry(monkeypatch, tmp_path):
    """도구 0회로 답만 내면 1회 재촉한다 — 실행 결과를 지어낸 답이 그대로 확정되지 않게."""
    monkeypatch.setitem(G.AGENTS, "coder", _SpySpec())
    monkeypatch.setitem(G.AGENTS, "planner", _SpySpec())
    calls: list = []

    async def fake_astream(agent, messages, on_token, config=None, on_event=None):
        calls.append(messages[-1]["content"])
        return AgentResult(answer="완료했습니다.", messages=[object()])

    monkeypatch.setattr(G, "astream_agent", fake_astream)

    async def fake_judge(model, vtask, config):
        return "pass", "ok"

    monkeypatch.setattr(G, "_judge", fake_judge)
    phases: list = []
    g = _build_graph(object(), _EditRouter(), Workspace(tmp_path), lambda n, a: True,
                     lambda t: None, phases.append, [])
    asyncio.run(g.ainvoke({"task": "포트 8791 죽여줘", "history": []}))

    assert "retry:도구 미사용" in phases
    assert calls[1].endswith(load_prompt("no_tool_retry"))   # 재촉 프롬프트가 붙는다


def test_verify_judges_with_observed_tool_evidence(monkeypatch, tmp_path):
    """도구를 실제로 돌렸으면 LLM 판정을 거치고, 관측된 실행 목록이 증거로 전달된다."""
    monkeypatch.setitem(G.AGENTS, "coder", _SpySpec())
    monkeypatch.setitem(G.AGENTS, "planner", _SpySpec())

    async def fake_astream(agent, messages, on_token, config=None, on_event=None):
        return AgentResult(answer="완료했다고 주장", messages=[object()])

    monkeypatch.setattr(G, "astream_agent", fake_astream)
    seen: list = []

    async def fake_judge(model, vtask, config):
        seen.append(vtask)
        return "fail", "변경 없음"

    monkeypatch.setattr(G, "_judge", fake_judge)
    log = ToolLog()
    log.calls.append(("run_command", "pkill -f 8791", True, "exit_code=0 · 포트 소멸"))
    g = _build_graph(object(), _EditRouter(), Workspace(tmp_path), lambda n, a: True,
                     lambda t: None, lambda p: None, [], tool_log=log)
    final = asyncio.run(g.ainvoke({"task": "a.txt 를 만들어라", "history": []}))

    assert len(seen) >= 1                                   # 무변경이어도 판정을 건너뛰지 않는다
    assert "(변경 없음)" in seen[0]
    assert "run_command(pkill -f 8791)" in seen[0] and "성공: exit_code=0 · 포트 소멸" in seen[0]   # 명령과 출력이 함께 증거로 들어간다
    assert "주장" in seen[0]                                 # 답변은 주장으로 라벨링된다
    assert final["verdict"] == "fail"


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
    def turn(self, name, task, session_id=""):
        rec = {"name": name, "task": task, "session_id": session_id}
        self.turns.append(rec)

        def done(answer, name=None):
            rec["answer"] = answer
            if name:
                rec["name"] = name

        yield SimpleNamespace(done=done)


def _dummy_settings(**overrides):
    from koda.config import ModelSettings
    values = dict(primary="local", local_api_key="x",
                  local_base_url="http://localhost:9", local_model="m",
                  fallback_enabled=False, openai_api_key="x",
                  openai_base_url="http://localhost:8", openai_model="o")
    values.update(overrides)
    return ModelSettings(**values)


def test_run_task_appends_extra_callbacks_without_touching_core(monkeypatch, tmp_path):
    """TUI 툴 가시화는 config.callbacks 채널로만 얹힌다(실행 코어·비노출 정책 무변경). 콜백 미전달(CLI 경로)이면 기존 동작 그대로다."""
    captured: dict = {}

    class _FakeGraph:
        async def ainvoke(self, state, config=None):
            captured["callbacks"] = list((config or {}).get("callbacks") or [])
            return {"answer": "완료", "route": "read"}

    async def no_title(task, answer="", settings=None):
        return "t"

    monkeypatch.setattr(G, "_build_graph", lambda *a, **k: _FakeGraph())
    monkeypatch.setattr(G, "build_model", lambda s: (object(), "local", None))
    monkeypatch.setattr(G, "_no_think", lambda m, s: m)
    monkeypatch.setattr(G, "_thinking", lambda m, s, b: m)
    monkeypatch.setattr(G, "make_vision_tools", lambda *a, **k: [])
    monkeypatch.setattr(G, "get_tracer", lambda: _NoTracer())
    monkeypatch.setattr(G, "make_title", no_title)

    settings = _dummy_settings()
    sentinel = object()

    asyncio.run(G.run_task("q", Workspace(tmp_path), lambda n, a: True, lambda t: None,
                           settings=settings, mcps_dir=None, extra_callbacks=[sentinel]))
    assert sentinel in captured["callbacks"]          # TUI 경로: 핸들러 주입됨

    asyncio.run(G.run_task("q", Workspace(tmp_path), lambda n, a: True, lambda t: None,
                           settings=settings, mcps_dir=None))
    assert sentinel not in captured["callbacks"]       # CLI 경로: 추가 없음(기존 동작)


def test_run_task_makes_title_after_answer_and_records_raw_io(monkeypatch, tmp_path):
    """trace_name 이 비어 있으면 답변까지 본 뒤 제목을 1회 짓고, trace 이름을 그 제목으로 확정한다. 입력·출력은 질의·답변 원문, run_id 는 session_id 로 실린다."""
    calls = []

    class _FakeGraph:
        async def ainvoke(self, state, config=None):
            return {"answer": "완료", "route": "read"}

    async def fake_title(task, answer="", settings=None):
        calls.append((task, answer))
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
                                settings=settings, mcps_dir=None, run_id="sess1"))
    assert r1["title"] == "세션 제목" and calls == [("첫 질의", "완료")]   # 답변을 본 뒤 짓는다
    r2 = asyncio.run(G.run_task("둘째 질의", Workspace(tmp_path), lambda n, a: True, lambda t: None,
                                settings=settings, mcps_dir=None, run_id="sess1", trace_name=r1["title"]))
    assert r2["title"] == "세션 제목" and len(calls) == 1                  # 제목이 있으면 호출 없음
    assert tracer.turns == [{"name": "세션 제목", "task": "첫 질의", "session_id": "sess1", "answer": "완료"},
                            {"name": "세션 제목", "task": "둘째 질의", "session_id": "sess1", "answer": "완료"}]


def test_run_task_empty_title_names_trace_by_task_head(monkeypatch, tmp_path):
    """제목 생성이 빈 문자열(빈약한 대화)이면 결과 title 은 비고, trace 이름은 질의 앞부분으로 대신한다."""

    class _FakeGraph:
        async def ainvoke(self, state, config=None):
            return {"answer": "완료", "route": "read"}

    async def empty_title(task, answer="", settings=None):
        return ""

    tracer = _NoTracer()
    monkeypatch.setattr(G, "_build_graph", lambda *a, **k: _FakeGraph())
    monkeypatch.setattr(G, "build_model", lambda s: (object(), "local", None))
    monkeypatch.setattr(G, "_no_think", lambda m, s: m)
    monkeypatch.setattr(G, "_thinking", lambda m, s, b: m)
    monkeypatch.setattr(G, "make_vision_tools", lambda *a, **k: [])
    monkeypatch.setattr(G, "get_tracer", lambda: tracer)
    monkeypatch.setattr(G, "make_title", empty_title)

    r = asyncio.run(G.run_task("안녕 반가워", Workspace(tmp_path), lambda n, a: True, lambda t: None,
                               settings=_dummy_settings(), mcps_dir=None))
    assert r["title"] == ""
    assert tracer.turns[0]["name"] == "안녕 반가워"


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


def _capture_extra_tools(monkeypatch, captured: dict):
    """Stub the graph build to record the extra_tools run_task assembles, with the usual run_task stubs."""

    class _FakeGraph:
        async def ainvoke(self, state, config=None):
            return {"answer": "완료", "route": "read"}

    def fake_build_graph(model, fast_model, ws, approver, on_token, on_phase, mcp_tools, *a, **k):
        captured["tools"] = list(mcp_tools)
        return _FakeGraph()

    monkeypatch.setattr(G, "_build_graph", fake_build_graph)
    monkeypatch.setattr(G, "build_model", lambda s: (object(), "openai", None))
    monkeypatch.setattr(G, "_no_think", lambda m, s: m)
    monkeypatch.setattr(G, "_thinking", lambda m, s, b: m)
    monkeypatch.setattr(G, "get_tracer", lambda: _NoTracer())


def test_run_task_puts_screen_tools_in_extra_tools(monkeypatch, tmp_path):
    """The three screen tools ride extra_tools next to analyze_image and the MCP tools, so every agent receives them."""
    captured: dict = {}
    _capture_extra_tools(monkeypatch, captured)
    asyncio.run(G.run_task("q", Workspace(tmp_path), lambda n, a: True, lambda t: None,
                           settings=_dummy_settings(), mcps_dir=None, trace_name="t",
                           mcp_tools=[_FakeTool("web_search_exa")]))
    names = [getattr(t, "name", "") for t in captured["tools"]]
    assert names == ["web_search_exa", "analyze_image", "parse_screen", "render_page", "screen_diff"]


def test_run_task_shares_the_vision_model_and_props_url(monkeypatch, tmp_path):
    """analyze_image and the screen tools get the same build_vision_model result; the /props probe follows VISION_BASE_URL when set."""
    captured: dict = {}
    _capture_extra_tools(monkeypatch, captured)
    vision_model, seen = object(), {}
    monkeypatch.setattr(G, "build_vision_model",
                        lambda s, fallback, provider="": seen.update(fallback=fallback, provider=provider) or vision_model)
    monkeypatch.setattr(G, "make_vision_tools",
                        lambda ws, model, props_url: seen.update(vision=(model, props_url)) or [])
    monkeypatch.setattr(G, "make_screen_tools",
                        lambda ws, model, props_url, methods=None: seen.update(screen=(model, props_url, methods)) or [])

    asyncio.run(G.run_task("q", Workspace(tmp_path), lambda n, a: True, lambda t: None,
                           settings=_dummy_settings(), mcps_dir=None, trace_name="t"))
    assert seen["fallback"] is not None                      # the fast model is the fallback
    assert seen["provider"] == "openai"                       # the provider build_model chose, not PRIMARY
    assert seen["vision"] == (vision_model, "http://localhost:8/props")
    assert seen["screen"] == (vision_model, "http://localhost:8/props", None)

    settings = _dummy_settings(vision_model="qwen-vl", vision_base_url="http://vision:7/v1",
                               vision_structured_method="json_schema")
    asyncio.run(G.run_task("q", Workspace(tmp_path), lambda n, a: True, lambda t: None,
                           settings=settings, mcps_dir=None, trace_name="t"))
    assert seen["vision"] == (vision_model, "http://vision:7/props")
    assert seen["screen"] == (vision_model, "http://vision:7/props", ("json_schema",))


def test_verify_fail_retry_follows_router_decision(monkeypatch, tmp_path):
    """실패 재시도 경로는 라우터의 plan_needed 를 턴 내내 따른다: 단순 편집은 coder 직행, 복잡 편집만 plan 재수립."""
    async def fake_astream(agent, messages, on_token, config=None, on_event=None):
        return AgentResult(answer="했다", messages=[object()])

    monkeypatch.setattr(G, "astream_agent", fake_astream)

    async def always_fail(model, vtask, config):
        return "fail", "확인 불가"

    monkeypatch.setattr(G, "_judge", always_fail)

    for router, plan_expected in ((_EditRouter(), False), (_FakeRouter(), True)):
        planner_spy, coder_spy = _SpySpec(), _SpySpec()
        monkeypatch.setitem(G.AGENTS, "planner", planner_spy)
        monkeypatch.setitem(G.AGENTS, "coder", coder_spy)
        g = _build_graph(object(), router, Workspace(tmp_path), lambda n, a: True,
                         lambda t: None, lambda p: None, [], tool_log=_worked_log())
        final = asyncio.run(g.ainvoke({"task": "작업", "history": []}))
        assert final["verdict"] == "fail" and final["retries"] == 1      # 재시도는 어느 경로든 1회
        if plan_expected:
            assert len(planner_spy.models) == 2                          # 최초 + 실패 후 재수립
        else:
            assert planner_spy.models == []                              # 계획이 필요 없던 턴은 plan 을 타지 않는다
        assert len(coder_spy.models) == 2                                # coder 는 두 경로 모두 2회
