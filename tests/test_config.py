"""핵심 모듈: config (.env + pydantic-settings, 소스에 기본값 없음 → 필수 + 타입검증)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from build_harness.config import ModelSettings

_ENV_KEYS = [
    "PRIMARY", "LOCAL_API_KEY", "LOCAL_OPENAI_BASE_URL", "LOCAL_MODEL_NAME",
    "FALLBACK_ENABLED", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL_NAME",
    "THINKING_BUDGET_READER", "THINKING_BUDGET_EXPLORE",
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


def test_missing_config_raises():
    # 소스에 기본값이 없으므로 .env/환경변수 없이는 실패한다
    with pytest.raises(ValidationError):
        ModelSettings(_env_file=None)


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
