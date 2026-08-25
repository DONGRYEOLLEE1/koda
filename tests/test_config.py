from __future__ import annotations

import pytest
from pydantic import ValidationError

from koda.config import ModelSettings

_ENV_KEYS = [
    "PRIMARY", "LOCAL_API_KEY", "LOCAL_OPENAI_BASE_URL", "LOCAL_MODEL_NAME",
    "FALLBACK_ENABLED", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL_NAME",
    "THINKING_BUDGET_READER", "THINKING_BUDGET_EXPLORE",
    "MODEL", "DEEPSEEK_API_KEY", "REASONING_STYLE", "STRUCTURED_METHOD",
]

_FULL = {
    "primary": "local", "local_api_key": "nothing",
    "local_base_url": "http://h:1/v1", "local_model": "m.gguf",
    "fallback_enabled": True, "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1", "openai_model": "gpt-4o-mini",
}


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    # 셸 환경변수/저장소 .env 영향 없이 결정론적으로 검증
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_no_config_loads_deepseek_defaults():
    # 설정 파일·환경변수 없이도 뜬다 — 기본은 DeepSeek(OpenAI 호환), 키만 비어 있다
    s = ModelSettings(_env_file=None)
    assert s.primary == "openai" and s.openai_api_key == ""
    assert s.openai_base_url == "https://api.deepseek.com"
    assert s.fallback_enabled is True
    # 기본 엔드포인트와 짝: off 면 DeepSeek thinking 이 켜진 채라 구조화 출력(강제 tool_choice)이 400 난다
    assert s.reasoning_style == "effort"
    assert s.structured_method == "function_calling"   # DeepSeek 는 json_schema 를 400 으로 거절한다


def test_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("OPENAI_MODEL_NAME", "gpt-5")
    s = ModelSettings(_env_file=None)
    assert s.openai_api_key == "sk-env" and s.openai_model == "gpt-5"


def test_load_from_env_file_and_types(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "PRIMARY=local\n"
        "LOCAL_API_KEY=nothing\n"
        "LOCAL_OPENAI_BASE_URL=http://h:1/v1\n"
        "LOCAL_MODEL_NAME=m.gguf\n"
        "FALLBACK_ENABLED=false\n"
        "OPENAI_API_KEY=sk-xyz\n"
        "OPENAI_BASE_URL=https://api.openai.com/v1\n"
        "OPENAI_MODEL_NAME=gpt-4o-mini\n",
        encoding="utf-8",
    )
    s = ModelSettings(_env_file=str(env))
    assert s.local_base_url == "http://h:1/v1"   # alias(LOCAL_OPENAI_BASE_URL) 매핑
    assert s.fallback_enabled is False           # "false" → bool 타입 변환/검증
    assert s.openai_api_key == "sk-xyz"


def test_thinking_budgets_default_and_override(monkeypatch):
    # 예산은 비밀값이 아닌 튜닝 노브 — 소스 기본값 허용, .env/환경변수로 재정의
    s = ModelSettings(_env_file=None, **_FULL)
    assert s.thinking_budget_reader == 1024
    assert s.thinking_budget_explore == 512
    monkeypatch.setenv("THINKING_BUDGET_READER", "2048")
    monkeypatch.setenv("THINKING_BUDGET_EXPLORE", "128")
    s2 = ModelSettings(_env_file=None, **_FULL)
    assert s2.thinking_budget_reader == 2048
    assert s2.thinking_budget_explore == 128


def test_invalid_base_url_rejected():
    with pytest.raises(ValidationError):
        ModelSettings(_env_file=None, **{**_FULL, "local_base_url": "ftp://nope"})


def test_invalid_primary_rejected():
    with pytest.raises(ValidationError):
        ModelSettings(_env_file=None, **{**_FULL, "primary": "banana"})


def test_resolve_env_file_priority(tmp_path, monkeypatch):
    """설정 탐색 우선순위: 명시 인자 > CWD/.env > ~/.config/koda/.env > None (전역 폴백)."""
    from koda.config import _resolve_env_file
    monkeypatch.chdir(tmp_path)
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))       # Path.home() 는 POSIX 에서 HOME 사용
    # 1) 명시 인자 최우선
    assert _resolve_env_file("/explicit.env") == "/explicit.env"
    # 2) 아무것도 없으면 None → 환경변수 폴백 경로
    assert _resolve_env_file() is None
    # 3) 전역 설정만 있으면 전역 경로
    koda_dir = fake_home / ".config" / "koda"
    koda_dir.mkdir(parents=True)
    (koda_dir / ".env").write_text("PRIMARY=local", encoding="utf-8")
    assert _resolve_env_file() == str(koda_dir / ".env")
    # 4) CWD/.env 가 전역보다 우선 (개발 시 기존 동작 유지)
    (tmp_path / ".env").write_text("PRIMARY=local", encoding="utf-8")
    assert _resolve_env_file() == ".env"


# --- provider 프로필: 모델명만 바꿔 전환 ---

def test_model_name_infers_openai_profile(monkeypatch):
    monkeypatch.setenv("MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    s = ModelSettings(_env_file=None)
    assert s.openai_base_url == "https://api.openai.com/v1"
    assert s.reasoning_style == "effort" and s.structured_method == "json_schema"
    assert s.openai_api_key == "sk-oai" and s.provider_name == "openai"
    assert s.use_responses_api is True                       # 도구+reasoning 은 Responses API 에서만 된다


def test_deepseek_uses_provider_key_then_falls_back_to_openai_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    s = ModelSettings(_env_file=None)                       # 기본 모델 deepseek-v4-flash
    assert s.openai_api_key == "ds-key" and s.structured_method == "function_calling"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert ModelSettings(_env_file=None).openai_api_key == "sk-oai"   # 예전 방식(OPENAI_API_KEY 겸용) 호환


def test_explicit_values_beat_profile(monkeypatch):
    monkeypatch.setenv("MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.local/v1")
    monkeypatch.setenv("STRUCTURED_METHOD", "function_calling")
    monkeypatch.setenv("REASONING_STYLE", "off")
    s = ModelSettings(_env_file=None)
    assert s.openai_base_url == "https://proxy.local/v1"
    assert s.structured_method == "function_calling" and s.reasoning_style == "off"


def test_unknown_model_needs_no_profile_and_keeps_deepseek_base(monkeypatch):
    monkeypatch.setenv("MODEL", "my-finetune")
    s = ModelSettings(_env_file=None)
    assert s.provider_name == "custom" and s.openai_base_url == "https://api.deepseek.com"
    assert s.reasoning_style == "effort" and s.structured_method == "function_calling"
    assert s.use_responses_api is False


def test_legacy_openai_model_name_alias_still_works(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_NAME", "gpt-5.6-terra")
    assert ModelSettings(_env_file=None).openai_model == "gpt-5.6-terra"
