from __future__ import annotations

import pytest
from langchain.chat_models import init_chat_model
from pydantic import BaseModel

from koda.config import ModelSettings
from koda.llm import (
    _UNSUPPORTED_METHODS,
    _no_think,
    _thinking,
    astructured,
    select_provider,
    set_structured_method,
    structured,
)

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


def test_provider_openai_primary_uses_key_without_health_check():
    # 기본 구성(primary=openai): 키가 있으면 로컬 헬스체크 없이 바로 openai
    called = []
    p, _ = select_provider(_cfg(primary="openai", openai_api_key="sk-test"),
                           health=lambda u: called.append(u) or True)
    assert p == "openai" and not called


def test_provider_openai_primary_falls_back_to_local_without_key():
    assert select_provider(_cfg(primary="openai", openai_api_key=""), health=lambda u: True)[0] == "local"
    # 로컬도 죽어 있으면 openai 로 남는다 — preflight 가 키 안내를 낸다
    assert select_provider(_cfg(primary="openai", openai_api_key=""), health=lambda u: False)[0] == "openai"


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


# --- 구조화 출력: 방식 폴백 + 미지원 캐시 ---

class _Schema(BaseModel):
    ok: bool = True


class _FakeModel:
    """with_structured_output 이 method 별로 성패를 달리하는 가짜 모델. 시도 이력을 기록한다."""

    def __init__(self, works: str | None, model_name: str = "fake"):
        self.model_name = model_name
        self._works = works        # 이 method 만 성공, 나머지는 400
        self.attempts: list[str] = []

    def with_structured_output(self, schema, method):
        self.attempts.append(method)
        works = self._works
        outer = self

        class _Runnable:
            def invoke(self, messages, config=None):
                if method != works:
                    raise _bad_request()
                return schema()

            async def ainvoke(self, messages, config=None):
                return self.invoke(messages, config)

        return _Runnable()


def _bad_request():
    e = Exception("Error code: 400 - This response_format type is unavailable now")
    e.status_code = 400
    return e


@pytest.fixture(autouse=True)
def _clear_unsupported():
    # 전역 상태라 테스트 간 오염을 막는다
    _UNSUPPORTED_METHODS.clear()
    set_structured_method("")
    yield
    _UNSUPPORTED_METHODS.clear()
    set_structured_method("")


def test_structured_falls_back_to_function_calling():
    # json_schema 미지원 엔드포인트에서도 구조화 출력이 유지된다 — 텍스트 폴백으로 떨어지면
    # router 의 plan_needed 판단이 통째로 죽는다.
    m = _FakeModel(works="function_calling")
    assert structured(m, _Schema, []) is not None
    assert m.attempts == ["json_schema", "function_calling"]


def test_structured_remembers_unsupported_method():
    # 400 은 능력 부재라 재시도해도 같다 — 두 번째 호출은 json_schema 를 건너뛴다.
    m = _FakeModel(works="function_calling")
    structured(m, _Schema, [])
    m.attempts.clear()
    structured(m, _Schema, [])
    assert m.attempts == ["function_calling"]


def test_structured_returns_none_when_all_methods_fail():
    m = _FakeModel(works=None)
    assert structured(m, _Schema, []) is None      # 호출부가 텍스트 폴백으로 간다


def test_structured_does_not_cache_transient_errors():
    # 네트워크·타임아웃(400 아님)을 캐시하면 그 방식이 세션 내내 죽는다.
    class _Flaky(_FakeModel):
        def with_structured_output(self, schema, method):
            self.attempts.append(method)
            raise TimeoutError("네트워크 지연")

    m = _Flaky(works=None)
    assert structured(m, _Schema, []) is None
    assert not _UNSUPPORTED_METHODS


def test_astructured_shares_the_same_fallback():
    import asyncio
    m = _FakeModel(works="function_calling")
    assert asyncio.run(astructured(m, _Schema, [])) is not None
    assert m.attempts == ["json_schema", "function_calling"]


def test_structured_method_override_skips_probing():
    # 방식을 고정하면 미지원 방식을 아예 시도하지 않아 첫 턴의 400 왕복도 사라진다.
    set_structured_method("function_calling")
    m = _FakeModel(works="function_calling")
    assert structured(m, _Schema, []) is not None
    assert m.attempts == ["function_calling"]      # json_schema 는 시도조차 안 함


def test_structured_method_override_empty_restores_probing():
    set_structured_method("")
    m = _FakeModel(works="function_calling")
    structured(m, _Schema, [])
    assert m.attempts == ["json_schema", "function_calling"]


def test_invalid_structured_method_rejected():
    with pytest.raises(ValueError):
        _cfg(structured_method="grammar")


def test_derive_carries_responses_api_flag():
    from langchain.chat_models import init_chat_model
    base = init_chat_model("gpt-5.6-terra", model_provider="openai", base_url="https://api.openai.com/v1",
                           api_key="k", temperature=0, max_tokens=4096, use_responses_api=True)
    m = _no_think(base, _cfg(reasoning_style="effort"))
    assert m.use_responses_api is True and m.reasoning_effort == "none"
