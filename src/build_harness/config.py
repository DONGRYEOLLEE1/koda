"""모델 설정: `.env` + 환경변수를 pydantic-settings 로 로드하고 타입까지 검증한다.

값은 소스에 두지 않는다 — 모든 필드는 **필수**이며 `.env`(gitignore) 또는 환경변수에서만 온다.
`.env.example` 이 템플릿. 우선순위: 생성자 인자 > 환경변수 > .env. 누락 시 ValidationError.
로컬 우선(llama.cpp) + OpenAI 폴백 (PLAN §3.0-3).
"""
from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,  # 필드명/alias 둘 다로 생성 가능(테스트 편의)
    )

    # 기본값 없음 → .env / 환경변수 필수 (소스에 값 노출 금지)
    primary: str = Field(alias="PRIMARY")
    local_api_key: str = Field(alias="LOCAL_API_KEY")
    local_base_url: str = Field(alias="LOCAL_OPENAI_BASE_URL")
    local_model: str = Field(alias="LOCAL_MODEL_NAME")
    fallback_enabled: bool = Field(alias="FALLBACK_ENABLED")
    openai_api_key: str = Field(alias="OPENAI_API_KEY")
    openai_base_url: str = Field(alias="OPENAI_BASE_URL")
    openai_model: str = Field(alias="OPENAI_MODEL_NAME")

    # 에이전트별 reasoning 예산 (llama.cpp `thinking_budget_tokens`, local 전용).
    thinking_budget_reader: int = Field(default=1024, alias="THINKING_BUDGET_READER")
    thinking_budget_explore: int = Field(default=512, alias="THINKING_BUDGET_EXPLORE")

    @field_validator("local_base_url", "openai_base_url")
    @classmethod
    def _must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url 은 http:// 또는 https:// 로 시작해야 합니다")
        return v

    @field_validator("primary")
    @classmethod
    def _valid_primary(cls, v: str) -> str:
        if v not in ("local", "openai"):
            raise ValueError("primary 는 'local' 또는 'openai' 여야 합니다")
        return v


def load_model_settings(env_file: str | None = None) -> ModelSettings:
    """CLI/런타임 진입점. env_file 을 주면 그 파일을, 아니면 기본 `.env` 를 읽는다."""
    if env_file:
        return ModelSettings(_env_file=env_file)  # type: ignore[call-arg]
    return ModelSettings()


class LangfuseSettings(BaseSettings):
    """Langfuse 추적 설정. 키가 없으면 추적을 끈다(로컬 저장 없음)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True,
    )

    host: str = Field(default="http://localhost:3002", alias="LANGFUSE_HOST")
    public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")

    @property
    def enabled(self) -> bool:
        return bool(self.public_key and self.secret_key)


def load_langfuse_settings(env_file: str | None = None) -> LangfuseSettings:
    if env_file:
        return LangfuseSettings(_env_file=env_file)  # type: ignore[call-arg]
    return LangfuseSettings()
