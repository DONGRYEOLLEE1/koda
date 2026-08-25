from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_env_file(env_file: str | None = None) -> str | None:
    """설정 파일 탐색: 명시값 > CWD/.env > ~/.config/koda/.env > None(환경변수 폴백). 존재하는 파일만 반환한다."""
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

    # 설정 파일 없이도 뜬다 — 기본은 DeepSeek(OpenAI 호환) API 이고 키만 환경변수로 넘기면 된다.
    # 로컬 llama.cpp 를 주로 쓰면 PRIMARY=local 로 바꾸고, 그때 DeepSeek 는 폴백이 된다.
    primary: str = Field(default="openai", alias="PRIMARY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.deepseek.com", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="deepseek-v4-flash", alias="OPENAI_MODEL_NAME")
    local_api_key: str = Field(default="nothing", alias="LOCAL_API_KEY")
    local_base_url: str = Field(default="http://localhost:8080/v1", alias="LOCAL_OPENAI_BASE_URL")
    local_model: str = Field(default="local", alias="LOCAL_MODEL_NAME")
    fallback_enabled: bool = Field(default=True, alias="FALLBACK_ENABLED")

    # reasoning 제어 방식 — 엔드포인트가 실제로 존중하는 파라미터 계열을 고른다. 알 수 없는 필드를
    # 보내면 정식 OpenAI API 는 400 으로 거절하므로, 기본값은 아무것도 보내지 않는 'off' 다.
    #   off    : 미전송 (DeepSeek·gpt-4o 등 reasoning 파라미터 미지원 엔드포인트)
    #   qwen   : llama.cpp `chat_template_kwargs.enable_thinking` + `thinking_budget_tokens`
    #   effort : `reasoning_effort` (vLLM·OpenAI gpt-5 계열)
    reasoning_style: str = Field(default="off", alias="REASONING_STYLE")
    # effort 스타일에서 reasoning 을 켤 때 쓸 등급. 끌 때는 항상 "none" 을 보낸다.
    reasoning_effort: str = Field(default="medium", alias="REASONING_EFFORT")

    # 구조화 출력 방식 고정 (json_schema|function_calling). 빈 값이면 강제력 순으로 탐색하는데,
    # 미지원 방식은 프로세스마다 첫 턴에 400 을 한 번 맞는다. 엔드포인트가 정해져 있으면 지정해 그 왕복을 없앤다.
    structured_method: str = Field(default="", alias="STRUCTURED_METHOD")

    # 에이전트별 reasoning 예산 (qwen 스타일의 `thinking_budget_tokens`).
    thinking_budget_reader: int = Field(default=1024, alias="THINKING_BUDGET_READER")
    thinking_budget_explore: int = Field(default=512, alias="THINKING_BUDGET_EXPLORE")

    # 나가는 요청 프롬프트 토큰 상한 — HistoryTrimMiddleware 가 이 값으로 턴 누적을 절단해 컨텍스트 초과(400)를 막는다. 서버 n_ctx 조정 시 함께 조정.
    context_budget_tokens: int = Field(default=64000, alias="CONTEXT_BUDGET_TOKENS")

    # 자동 승인할 읽기 전용 MCP 도구 이름(쉼표 구분, opt-in). 쓰기성·SSRF 가능 MCP 는 넣지 마라 — 자동 실행되어 유출 위험이 된다.
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

    @field_validator("reasoning_style")
    @classmethod
    def _valid_reasoning_style(cls, v: str) -> str:
        if v not in ("off", "qwen", "effort"):
            raise ValueError("reasoning_style 은 'off', 'qwen', 'effort' 중 하나여야 합니다")
        return v

    @field_validator("structured_method")
    @classmethod
    def _valid_structured_method(cls, v: str) -> str:
        if v and v not in ("json_schema", "function_calling"):
            raise ValueError("structured_method 는 'json_schema' 또는 'function_calling' 이어야 합니다(빈 값=자동 탐색)")
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

    host: str = Field(default="http://localhost:28900", alias="LANGFUSE_HOST")
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
