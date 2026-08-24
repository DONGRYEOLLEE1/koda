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


def test_qwen_thinking_sets_budget_extra_body():
    # 에이전트별 예산이 llama.cpp 최상위 요청 파라미터로 실린다 (chat_template_kwargs 밖!)
    cfg = _cfg(reasoning_style="qwen")
    base = _base_model()
    m = _thinking(base, cfg, 512)
    assert m.extra_body == {"chat_template_kwargs": {"enable_thinking": True},
                            "thinking_budget_tokens": 512}
    assert m is not base and not base.extra_body     # 원본 인스턴스 불변 (공유 오염 방지)
    assert m.max_tokens == base.max_tokens           # 폭주 상한 등 기존 파라미터 승계
    # 서로 다른 예산 = 서로 다른 인스턴스 (reader/explore 차등 적용의 전제)
    m2 = _thinking(base, cfg, 128)
    assert m2.extra_body["thinking_budget_tokens"] == 128 and m2 is not m


def test_qwen_no_think_disables_thinking_without_budget():
    m = _no_think(_base_model(), _cfg(reasoning_style="qwen"))
    assert m.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_effort_style_uses_native_field_not_extra_body():
    # reasoning_effort 는 langchain_openai 정식 필드다 — extra_body 로 우회하면 Responses API
    # 변환을 타지 못한다.
    cfg = _cfg(reasoning_style="effort", reasoning_effort="high")
    on = _thinking(_base_model(), cfg, 512)
    assert on.reasoning_effort == "high" and not on.extra_body
    off = _no_think(_base_model(), cfg)
    assert off.reasoning_effort == "none" and not off.extra_body


def test_off_style_sends_nothing_and_reuses_instance():
    # 미지원 엔드포인트(DeepSeek·gpt-4o)에 알 수 없는 필드를 보내면 400 이다 — 아무것도 싣지 않고,
    # 바꿀 파라미터가 없으므로 원본 인스턴스를 그대로 재사용한다.
    cfg = _cfg(reasoning_style="off")
    base = _base_model()
    assert _thinking(base, cfg, 512) is base
    assert _no_think(base, cfg) is base
    assert not base.extra_body and not base.reasoning_effort


def test_invalid_reasoning_style_rejected():
    import pytest
    with pytest.raises(ValueError):
        _cfg(reasoning_style="thinking")
