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


def test_env_files_layered(tmp_path, monkeypatch):
    """설정 탐색: 명시 인자는 단독, 아니면 (전역, CWD) 계층으로 겹쳐 읽는다."""
    from koda.config import _env_files
    monkeypatch.chdir(tmp_path)
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))       # Path.home() 는 POSIX 에서 HOME 사용
    # 1) 명시 인자는 전역과 섞지 않는다
    assert _env_files("/explicit.env") == ("/explicit.env",)
    # 2) 아무것도 없으면 빈 튜플 → 환경변수 폴백 경로
    assert _env_files() == ()
    # 3) 전역 설정만 있으면 전역 하나
    koda_dir = fake_home / ".koda"
    koda_dir.mkdir(parents=True)
    (koda_dir / ".env").write_text("PRIMARY=local", encoding="utf-8")
    assert _env_files() == (str(koda_dir / ".env"),)
    # 4) 둘 다 있으면 CWD 가 뒤 = 겹칠 때 CWD 가 이긴다
    (tmp_path / ".env").write_text("PRIMARY=local", encoding="utf-8")
    assert _env_files() == (str(koda_dir / ".env"), ".env")


def test_cwd_env_does_not_hide_global_keys(tmp_path, monkeypatch):
    """CWD/.env 에 없는 키는 전역 파일에서 채운다. 무관한 .env 하나가 langfuse 추적을 끄던 회귀."""
    from koda.config import load_langfuse_settings, load_model_settings
    for k in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    koda_dir = fake_home / ".koda"
    koda_dir.mkdir(parents=True)
    (koda_dir / ".env").write_text(
        "LANGFUSE_PUBLIC_KEY=pk-global\nLANGFUSE_SECRET_KEY=sk-global\nPRIMARY=local\n", encoding="utf-8")
    # langfuse 키가 없는 남의 .env (다른 프로젝트가 쓰는 파일)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-project\n", encoding="utf-8")
    assert load_langfuse_settings().enabled is True
    s = load_model_settings()
    assert s.openai_api_key == "sk-project"   # 겹치는 키는 CWD 가 이긴다
    assert s.primary == "local"               # CWD 에 없는 키는 전역이 채운다
    # CWD 에 빈 값으로 선언하면 전역 값을 덮는다 (키를 지우는 수단)
    (tmp_path / ".env").write_text("LANGFUSE_PUBLIC_KEY=\n", encoding="utf-8")
    assert load_langfuse_settings().enabled is False


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


def test_switch_model_across_providers_and_local():
    from koda.config import ModelSettings, switch_model
    base = ModelSettings(_env_file=None, openai_model="deepseek-v4-flash", deepseek_api_key="dk", local_base_url="http://127.0.0.1:4000/v1", local_model="qwen3.8-27b")
    assert base.openai_api_key == "dk" and base.openai_base_url == "https://api.deepseek.com"

    same = switch_model(base, "deepseek-v4-pro")                       # 같은 provider: 키·URL 유지
    assert same.openai_model == "deepseek-v4-pro" and same.openai_api_key == "dk" and same.openai_base_url == "https://api.deepseek.com"

    oa = switch_model(base, "gpt-5.6", api_key="ok")                   # provider 전환: 프로필 재적용, 새 키
    assert (oa.primary, oa.openai_base_url, oa.openai_api_key, oa.use_responses_api, oa.structured_method) == \
        ("openai", "https://api.openai.com/v1", "ok", True, "json_schema")
    assert switch_model(base, "gpt-5.6").openai_api_key == ""          # 겸용 복사된 DeepSeek 키는 OpenAI 에 쓰지 않는다

    loc = switch_model(oa, "qwen3.9-30b", provider="local", base_url="http://llm-gateway.example.com:4000/v1")
    assert (loc.primary, loc.local_model, loc.local_base_url) == ("local", "qwen3.9-30b", "http://llm-gateway.example.com:4000/v1")
    assert switch_model(loc, "qwen3.8-27b").local_model == "qwen3.8-27b"   # local 에서 유추 불가 이름은 local 유지

    back = switch_model(loc, "deepseek-v4-flash", api_key="dk2")
    assert (back.primary, back.openai_api_key, back.openai_base_url) == ("openai", "dk2", "https://api.deepseek.com")


def test_koda_home_prefers_new_path_and_falls_back(tmp_path, monkeypatch):
    """전역 설정 폴더는 ~/.koda 다. 아직 없고 예전 위치(~/.config/koda)만 있으면 그쪽을 읽어 기존 설정이 끊기지 않는다."""
    from koda.config import koda_home
    monkeypatch.setenv("HOME", str(tmp_path))
    assert koda_home() == tmp_path / ".koda"          # 둘 다 없으면 새 경로
    legacy = tmp_path / ".config" / "koda"
    legacy.mkdir(parents=True)
    assert koda_home() == legacy                       # 예전 위치만 있으면 그쪽
    (tmp_path / ".koda").mkdir()
    assert koda_home() == tmp_path / ".koda"          # 새 경로가 생기면 새 경로가 이긴다


def test_budget_scale_boundaries():
    """파생 상한의 배율. 기본 예산에서 1.0 이고, 미설정(0)은 기본 예산으로 보며, 아주 작은 예산에서도 하한이 있어 상한이 0 으로 붕괴하지 않는다."""
    from koda.config import DEFAULT_CONTEXT_BUDGET, budget_scale
    assert budget_scale(DEFAULT_CONTEXT_BUDGET) == 1.0
    assert budget_scale(0) == 1.0
    assert budget_scale(1_000_000) == 1_000_000 / DEFAULT_CONTEXT_BUDGET
    assert budget_scale(1_000) == 0.25


def test_context_budget_env_var_overrides_default(monkeypatch):
    """1M 모델은 CONTEXT_BUDGET_TOKENS 만 export 하면 된다 — 환경변수가 .env 와 기본값을 이긴다."""
    from koda.config import ModelSettings
    monkeypatch.setenv("CONTEXT_BUDGET_TOKENS", "1000000")
    assert ModelSettings(_env_file=None).context_budget_tokens == 1_000_000
