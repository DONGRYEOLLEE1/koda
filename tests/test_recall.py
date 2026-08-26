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
