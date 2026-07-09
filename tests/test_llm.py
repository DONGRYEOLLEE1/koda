from __future__ import annotations

from langchain.chat_models import init_chat_model

from koda.config import ModelSettings
from koda.llm import _no_think, _thinking, select_provider

_BASE = {
    "primary": "local", "local_api_key": "nothing",
    "local_base_url": "http://h:1/v1", "local_model": "m",
    "fallback_enabled": True, "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1", "openai_model": "gpt-4o-mini",
}


def _cfg(**kw):
    return ModelSettings(_env_file=None, **{**_BASE, **kw})


def test_provider_local_healthy():
    assert select_provider(_cfg(), health=lambda u: True)[0] == "local"


def test_provider_falls_back_when_local_down():
    assert select_provider(_cfg(openai_api_key="sk-test"), health=lambda u: False)[0] == "openai"


def test_provider_no_fallback_without_key():
    assert select_provider(_cfg(openai_api_key=""), health=lambda u: False)[0] == "local"


def _base_model():
    return init_chat_model("m.gguf", model_provider="openai", base_url="http://h:1/v1",
                           api_key="k", temperature=0, max_tokens=4096)


def test_thinking_sets_budget_extra_body():
    # 에이전트별 예산이 llama.cpp 최상위 요청 파라미터로 실린다 (chat_template_kwargs 밖!)
    base = _base_model()
    m = _thinking(base, 512)
    assert m.extra_body == {"chat_template_kwargs": {"enable_thinking": True},
                            "thinking_budget_tokens": 512}
    assert m is not base and not base.extra_body     # 원본 인스턴스 불변 (공유 오염 방지)
    assert m.max_tokens == base.max_tokens           # 폭주 상한 등 기존 파라미터 승계
    # 서로 다른 예산 = 서로 다른 인스턴스 (reader/explore 차등 적용의 전제)
    m2 = _thinking(base, 128)
    assert m2.extra_body["thinking_budget_tokens"] == 128 and m2 is not m


def test_no_think_disables_thinking_without_budget():
    m = _no_think(_base_model())
    assert m.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
