from __future__ import annotations

import asyncio

import koda.agents.recall as R
from koda.memory import MAX_CHARS, Memory


def _settings():
    from koda.config import ModelSettings
    return ModelSettings(_env_file=None, primary="openai", openai_api_key="k", openai_model="deepseek-v4-flash")


def test_judge_memory_caps_and_filters(monkeypatch):
    # 판단은 LLM, 상한(유형당 2개·200자)과 빈 항목 제거는 코드가 집행한다.
    async def fake_astructured(model, schema, messages, config=None, methods=None):
        return schema(semantic=["a", " ", "b", "c"], episodic=["x" * 300], procedural=[])

    monkeypatch.setattr(R, "astructured", fake_astructured)
    monkeypatch.setattr(R, "build_model", lambda s: (object(), "openai", ""))
    monkeypatch.setattr(R, "_no_think", lambda m, s: m)
    out = asyncio.run(R.judge_memory("q", "a", settings=_settings()))
    assert out == {"semantic": ["a", "b"], "episodic": [], "procedural": []}


def test_judge_memory_returns_empty_when_structured_output_fails(monkeypatch):
    async def none(*a, **k):
        return None

    monkeypatch.setattr(R, "astructured", none)
    monkeypatch.setattr(R, "build_model", lambda s: (object(), "openai", ""))
    monkeypatch.setattr(R, "_no_think", lambda m, s: m)
    assert asyncio.run(R.judge_memory("q", "a", settings=_settings())) == {}


class _FixedModel:
    def __init__(self, out):
        self.out = out

    async def ainvoke(self, msgs, config=None):
        from langchain_core.messages import AIMessage
        return AIMessage(self.out)


def test_make_title_limits_and_none_marker(monkeypatch):
    monkeypatch.setattr(R, "build_model", lambda s: (None, "openai", ""))
    monkeypatch.setattr(R, "_no_think", lambda m, s: _FixedModel('"세션 패널 구현 계획서 정리본"'))
    assert asyncio.run(R.make_title("질문", "답", settings=_settings())) == "세션 패널 구현 계획서 정리"   # 15자 절단
    monkeypatch.setattr(R, "_no_think", lambda m, s: _FixedModel("없음"))
    assert asyncio.run(R.make_title("안녕", "", settings=_settings())) == ""                            # 빈약 → 빈 제목


def test_summarize_history_splits_title_line(monkeypatch):
    monkeypatch.setattr(R, "build_model", lambda s: (None, "openai", ""))
    monkeypatch.setattr(R, "_no_think", lambda m, s: _FixedModel("본문 요약이다.\n제목: 패널 리팩토링"))
    out = asyncio.run(R.summarize_history([{"role": "user", "content": "q"}], settings=_settings()))
    assert out == ("본문 요약이다.", "패널 리팩토링")
    monkeypatch.setattr(R, "_no_think", lambda m, s: _FixedModel("제목 줄 없는 요약"))
    assert asyncio.run(R.summarize_history([{"role": "user", "content": "q"}], settings=_settings())) == ("제목 줄 없는 요약", "")


def test_remember_from_turn_stores_by_kind_and_compacts(tmp_path, monkeypatch):
    m = Memory(tmp_path)
    m.remember("이미 있음", "procedural")

    async def fake_judge(task, answer, existing="", settings=None):
        assert "이미 있음" in existing                       # 기존 메모리를 판정 입력으로 넘긴다
        return {"semantic": ["사용자는 부산에 산다"], "episodic": [], "procedural": ["이미 있음", "x" * (MAX_CHARS + 1)]}

    async def fake_compact(text, settings=None):
        return "- 압축됨"

    monkeypatch.setattr(R, "judge_memory", fake_judge)
    monkeypatch.setattr(R, "compact_memory", fake_compact)
    saved = asyncio.run(R.remember_from_turn(m, "내 이름은 드리, 부산 살아", "반갑습니다"))
    assert ("semantic", "사용자는 부산에 산다") in saved
    assert ("procedural", "이미 있음") not in saved                # 중복은 저장 안 함
    assert m.items("procedural") == ["압축됨"]                     # 상한 초과 유형만 압축
    assert m.items("semantic") == ["사용자는 부산에 산다"]
