from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
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


# 모델명으로 유추하는 provider 프로필 — 모델만 바꿔도 base_url·파라미터 계열이 따라온다. 명시한 환경변수가 항상 우선.
# 구조화 출력: OpenAI 는 json_schema 가 reasoning 여부와 무관하게 되고 function_calling 은 reasoning 활성 시 400,
# DeepSeek 는 정반대(json_schema 거절, function_calling 만). 실측 근거는 이슈 참조.
PROVIDER_PROFILES = {
    # OpenAI Chat Completions 는 도구 + reasoning_effort≠none 을 400 으로 거절한다(실측) — Responses API 로 보낸다.
    "openai": {"base_url": "https://api.openai.com/v1", "reasoning_style": "effort", "structured_method": "json_schema",
               "use_responses_api": True},
    "deepseek": {"base_url": "https://api.deepseek.com", "reasoning_style": "effort", "structured_method": "function_calling"},
}


def infer_provider(model: str) -> str | None:
    """모델명 접두로 provider 를 유추한다. 모르는 이름이면 None(자체 호스팅 등 → base_url 명시 필요)."""
    m = (model or "").lower()
    if m.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4")):
        return "openai"
    if m.startswith("deepseek"):
        return "deepseek"
    return None


class ModelSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,  # 필드명/alias 둘 다로 생성 가능(테스트 편의)
    )

    # 설정 파일 없이도 뜬다 — MODEL 과 그 provider 의 키만 환경변수로 넘기면 나머지는 프로필에서 채운다.
    # 로컬 llama.cpp 를 주로 쓰면 PRIMARY=local 로 바꾸고, 그때 API 는 폴백이 된다.
    primary: str = Field(default="openai", alias="PRIMARY")
    openai_model: str = Field(default="deepseek-v4-flash",
                              validation_alias=AliasChoices("MODEL", "OPENAI_MODEL_NAME"))
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")  # 비면 프로필, 미유추 모델은 DeepSeek
    # OpenAI Responses API 사용 여부. 비면 프로필(OpenAI 만 True). 응답 content 가 블록 리스트로 온다.
    use_responses_api: bool | None = Field(default=None, alias="OPENAI_USE_RESPONSES_API")
    local_api_key: str = Field(default="nothing", alias="LOCAL_API_KEY")
    local_base_url: str = Field(default="http://localhost:8080/v1", alias="LOCAL_OPENAI_BASE_URL")
    local_model: str = Field(default="local", alias="LOCAL_MODEL_NAME")
    fallback_enabled: bool = Field(default=True, alias="FALLBACK_ENABLED")

    # reasoning 제어 방식 — 비면 프로필(OpenAI·DeepSeek 모두 effort). DeepSeek 는 아무것도 안 보내면 thinking 이
    # 켜진 채라 구조화 출력의 강제 tool_choice 를 400 으로 거절하므로 no_think 경로가 reasoning_effort=none 을 명시한다.
    #   effort : `reasoning_effort` (DeepSeek·vLLM·OpenAI gpt-5 계열)
    #   off    : 미전송 — reasoning 파라미터를 모르는 모델(gpt-4o 등)은 이 값이어야 400 이 안 난다
    #   qwen   : llama.cpp `chat_template_kwargs.enable_thinking` + `thinking_budget_tokens`
    reasoning_style: str = Field(default="", alias="REASONING_STYLE")
    # effort 스타일에서 reasoning 을 켤 때 쓸 등급. 끌 때는 항상 "none" 을 보낸다.
    reasoning_effort: str = Field(default="medium", alias="REASONING_EFFORT")

    # 구조화 출력 방식 (json_schema|function_calling|auto=강제력 순 탐색). 비면 프로필. llama.cpp 는 json_schema 를 지정한다.
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
        if v and not v.startswith(("http://", "https://")):  # 빈 값은 프로필에서 채운다
            raise ValueError("base_url 은 http:// 또는 https:// 로 시작해야 합니다")
        return v

    def model_post_init(self, __context) -> None:
        """비워 둔 값을 모델명 프로필로 채운다. 명시한 값은 그대로 둔다."""
        provider = infer_provider(self.openai_model)
        prof = PROVIDER_PROFILES.get(provider or "", {})
        if not self.openai_base_url:
            self.openai_base_url = prof.get("base_url", PROVIDER_PROFILES["deepseek"]["base_url"])
        if not self.reasoning_style:
            self.reasoning_style = prof.get("reasoning_style", "effort")
        if not self.structured_method:
            self.structured_method = prof.get("structured_method", "function_calling")
        if self.use_responses_api is None:
            self.use_responses_api = bool(prof.get("use_responses_api", False))
        if provider == "deepseek" and self.deepseek_api_key:  # provider 별 키가 있으면 우선, 없으면 OPENAI_API_KEY 겸용
            self.openai_api_key = self.deepseek_api_key

    @property
    def provider_name(self) -> str:
        return infer_provider(self.openai_model) or "custom"

    @field_validator("primary")
    @classmethod
    def _valid_primary(cls, v: str) -> str:
        if v not in ("local", "openai"):
            raise ValueError("primary 는 'local' 또는 'openai' 여야 합니다")
        return v

    @field_validator("reasoning_style")
    @classmethod
    def _valid_reasoning_style(cls, v: str) -> str:
        if v and v not in ("off", "qwen", "effort"):
            raise ValueError("reasoning_style 은 'off', 'qwen', 'effort' 중 하나여야 합니다")
        return v

    @field_validator("structured_method")
    @classmethod
    def _valid_structured_method(cls, v: str) -> str:
        if v and v not in ("json_schema", "function_calling", "auto"):
            raise ValueError("structured_method 는 'json_schema'·'function_calling'·'auto' 중 하나여야 합니다")
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
