"""모델 설정: `.env` + 환경변수를 pydantic-settings 로 로드하고 타입까지 검증한다.

값은 소스에 두지 않는다 — 모든 필드는 **필수**이며 `.env`(gitignore) 또는 환경변수에서만 온다.
`.env.example` 이 템플릿. 우선순위: 생성자 인자 > 환경변수 > .env. 누락 시 ValidationError.
로컬 우선(llama.cpp) + OpenAI 폴백 (PLAN §3.0-3).
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_env_file(env_file: str | None = None) -> str | None:
    """설정 파일 탐색: 명시값 > CWD/.env > ~/.config/koda/.env > None(환경변수).

    "어떤 환경에서든 설치→koda 실행"을 위한 전역 설정 폴백 (plan/KODA_MIGRATION §4.2).
    존재하는 파일만 반환 — 없으면 None 을 줘 pydantic 이 환경변수로 폴백(없으면 기존대로
    ValidationError). CWD/.env 는 개발 시 기존 동작을 그대로 유지한다.
    """
    if env_file:
        return env_file
    cwd = Path(".env")
    if cwd.is_file():
        return str(cwd)
    try:
        glob = Path.home() / ".config" / "koda" / ".env"
    except (RuntimeError, OSError):  # 홈 디렉토리 미정의 환경 → 환경변수 경로로
        return None
    return str(glob) if glob.is_file() else None


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

    # 나가는 요청 프롬프트의 토큰 상한(HistoryTrimMiddleware 가 이 값으로 턴 누적을 결정론
    # 절단해 컨텍스트 창 초과 400 을 막는다). llama.cpp n_ctx=132k 운용에서 실효 예산을 64k 로
    # 둔다(창의 절반 수준 — char 기반 토큰 추정이 한/중/일·도구호출 혼합에서 실제를 최대 ~1.9x
    # 과소평가하는 오차를 흡수하고, tools_reserve·system 토큰은 이 안에서 차감). 서버 창을
    # 조정하면 이 값도 함께 조정.
    context_budget_tokens: int = Field(default=64000, alias="CONTEXT_BUDGET_TOKENS")

    # 자동 승인할 읽기 전용 MCP 도구 이름(쉼표 구분). 기본 빈 값 = 현행(모든 MCP 가 HITL). opt-in.
    # 쓰기성·내부망 도달 가능(SSRF) MCP 이름은 넣지 마라 — 자동 실행되어 유출 위험이 된다.
    mcp_auto_approve: str = Field(default="", alias="KODA_MCP_AUTO_APPROVE")

    @property
    def mcp_auto_approve_set(self) -> frozenset[str]:
        return frozenset(x.strip() for x in self.mcp_auto_approve.split(",") if x.strip())

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
    """CLI/런타임 진입점. env_file 미지정 시 CWD/.env → ~/.config/koda/.env 순으로 찾는다."""
    resolved = _resolve_env_file(env_file)
    if resolved:
        return ModelSettings(_env_file=resolved)  # type: ignore[call-arg]
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
    resolved = _resolve_env_file(env_file)
    if resolved:
        return LangfuseSettings(_env_file=resolved)  # type: ignore[call-arg]
    return LangfuseSettings()
