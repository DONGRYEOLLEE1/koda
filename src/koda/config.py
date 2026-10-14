from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 실효 컨텍스트 예산 기본값. koda 의 구조적 천장(시스템·도구 스키마 약 17k + 압축 후 이력 약 12k + 턴 내부 도구 출력 약 93k)에 맞춘 값이다.
DEFAULT_CONTEXT_BUDGET = 122_000

# 모델이 한 번에 생성할 수 있는 출력 토큰(llm.py 가 요청에 싣는 max_tokens). 프롬프트 상한을 잡을 때
# 이만큼은 비워 둬야 한다 — 프롬프트가 컨텍스트를 다 채우면 응답을 생성할 자리가 남지 않는다.
MAX_OUTPUT_TOKENS = 4096


def budget_scale(context_budget: int) -> float:
    """기본 예산 대비 배율. 압축 임계·도구 출력 상한 같은 파생 상한이 이 배율로 함께 커진다."""
    return max(0.25, (context_budget or DEFAULT_CONTEXT_BUDGET) / DEFAULT_CONTEXT_BUDGET)


KODA_HOME = ".koda"   # 홈의 전역 설정 폴더. 워크스페이스의 .koda 와 같은 이름을 쓴다


def koda_home() -> Path | None:
    """전역 설정 폴더 ~/.koda. 아직 없고 예전 위치(~/.config/koda)만 있으면 그쪽을 쓴다. 홈 미정의 환경이면 None."""
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        return None
    new = home / KODA_HOME
    legacy = home / ".config" / "koda"
    return legacy if not new.is_dir() and legacy.is_dir() else new


def _env_files(env_file: str | None = None) -> tuple[str, ...]:
    """설정 파일 계층: ~/.koda/.env 를 깔고 CWD/.env 가 덮는다(뒤 파일이 우선). 명시값이 있으면 그것만 쓴다."""
    if env_file:
        return (env_file,)
    files = []
    home = koda_home()
    if home and (glob := home / ".env").is_file():
        files.append(str(glob))
    if Path(".env").is_file():
        files.append(".env")
    return tuple(files)


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


# 프로필이 채우는 파생 값. provider 가 바뀌면 비워서 새 프로필로 다시 채운다.
_PROFILE_FIELDS = {"openai_base_url": "", "reasoning_style": "", "structured_method": "", "use_responses_api": None}

# 필드 → 그 값을 줄 수 있는 환경변수 이름. 값이 파일에서 왔는지 환경변수에서 왔는지 가르는 데 쓴다.
_ALIASES = {
    "openai_model": ("MODEL", "OPENAI_MODEL_NAME"),
    "openai_base_url": ("OPENAI_BASE_URL",),
    "reasoning_style": ("REASONING_STYLE",),
    "structured_method": ("STRUCTURED_METHOD",),
    "use_responses_api": ("OPENAI_USE_RESPONSES_API",),
}


def _env_given(*names: str) -> bool:
    """이 설정 키가 프로세스 환경변수로 직접 주어졌는가. .env 파일에서만 온 값과 구분한다."""
    return any(n in os.environ for n in names)


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

    # Separate model for image calls (parse_screen, analyze_image). Empty VISION_MODEL means the fast
    # model takes them too; empty URL and key default to the primary provider's. VISION_STRUCTURED_METHOD
    # pins the structured-output method for that model alone.
    vision_model: str = Field(default="", alias="VISION_MODEL")
    vision_base_url: str = Field(default="", alias="VISION_BASE_URL")
    vision_api_key: str = Field(default="", alias="VISION_API_KEY")
    vision_structured_method: str = Field(default="", alias="VISION_STRUCTURED_METHOD")

    # 에이전트별 reasoning 예산 (qwen 스타일의 `thinking_budget_tokens`).
    thinking_budget_reader: int = Field(default=1024, alias="THINKING_BUDGET_READER")
    thinking_budget_explore: int = Field(default=512, alias="THINKING_BUDGET_EXPLORE")

    # 모델이 받는 컨텍스트 예산. HistoryTrimMiddleware 의 프롬프트 상한이자 압축 임계·도구 출력 상한의 기준값이라, 1M 모델은 이 값만 올리면 파생 상한이 함께 커진다.
    context_budget_tokens: int = Field(default=DEFAULT_CONTEXT_BUDGET, alias="CONTEXT_BUDGET_TOKENS")

    # 자동 승인할 읽기 전용 MCP 도구 이름(쉼표 구분, opt-in). 쓰기성·SSRF 가능 MCP 는 넣지 마라 — 자동 실행되어 유출 위험이 된다.
    mcp_auto_approve: str = Field(default="", alias="KODA_MCP_AUTO_APPROVE")

    @property
    def mcp_auto_approve_set(self) -> frozenset[str]:
        return frozenset(x.strip() for x in self.mcp_auto_approve.split(",") if x.strip())

    @field_validator("local_base_url", "openai_base_url", "vision_base_url")
    @classmethod
    def _must_be_http(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):  # 빈 값은 프로필에서 채운다
            raise ValueError("base_url 은 http:// 또는 https:// 로 시작해야 합니다")
        return v

    def _profile_is_stale(self, provider: str | None) -> bool:
        """파일에 남은 파생 값이 지금 모델과 어긋나는가.

        `MODEL` 은 환경변수가 `.env` 를 이기는데 `OPENAI_BASE_URL` 은 파일 값이 그대로 남아, 모델과
        엔드포인트가 어긋난 채 결합되던 문제를 가른다(#93). 넷을 모두 만족할 때만 참이다: 모델을
        환경변수로 줬고, base_url 은 파일에서만 왔고, 그 값이 **알려진 provider 프로필의 주소**이며,
        지금 모델의 프로필 주소와 다르다. 어느 프로필에도 없는 주소는 자체 호스팅이므로 존중한다.
        """
        if not _env_given(*_ALIASES["openai_model"]) or _env_given(*_ALIASES["openai_base_url"]):
            return False
        known = {p["base_url"] for p in PROVIDER_PROFILES.values()}
        current = (self.openai_base_url or "").rstrip("/")
        if current not in {k.rstrip("/") for k in known}:
            return False
        target = PROVIDER_PROFILES.get(provider or "", {}).get("base_url", "")
        return current != target.rstrip("/")

    def model_post_init(self, __context) -> None:
        """비워 둔 값을 모델명 프로필로 채운다. 명시한 값은 그대로 둔다."""
        provider = infer_provider(self.openai_model)
        prof = PROVIDER_PROFILES.get(provider or "", {})
        if self._profile_is_stale(provider):
            # 이전 provider 의 잔재를 비워 아래 채우기가 새 프로필로 다시 채우게 한다. base_url 만
            # 갈아치우면 structured_method 는 옛 값이 남아, DeepSeek 주소에 DeepSeek 이 거절하는
            # json_schema 를 보내는 식으로 더 나쁜 조합이 된다. 환경변수로 명시한 값은 건드리지 않는다.
            for field, empty in _PROFILE_FIELDS.items():
                if not _env_given(*_ALIASES[field]):
                    setattr(self, field, empty)
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

    @field_validator("structured_method", "vision_structured_method")
    @classmethod
    def _valid_structured_method(cls, v: str) -> str:
        if v and v not in ("json_schema", "function_calling", "auto"):
            raise ValueError("structured_method 는 'json_schema'·'function_calling'·'auto' 중 하나여야 합니다")
        return v




def switch_model(settings: ModelSettings, model: str, provider: str = "", base_url: str = "", api_key: str = "") -> ModelSettings:
    """세션 중 모델 전환. provider 는 local|openai|deepseek 이며 비우면 모델명 접두로 유추하고, 유추가 안 되면 현재 provider 를 유지한다. .env 를 다시 읽지 않는다."""
    values = settings.model_dump()
    current = "local" if settings.primary == "local" else (settings.provider_name if settings.provider_name in ("openai", "deepseek") else "custom")
    target = provider or infer_provider(model) or current
    if target not in ("local", "openai", "deepseek", "custom"):
        raise ValueError("provider 는 local·openai·deepseek 중 하나여야 합니다")
    if target == "local":
        values.update(primary="local", local_model=model)
        if base_url:
            values["local_base_url"] = base_url
        if api_key:
            values["local_api_key"] = api_key
    else:
        values.update(primary="openai", openai_model=model)
        if target != current:                       # provider 가 바뀌면 base_url·reasoning·구조화 방식은 새 프로필을 따른다
            values.update(_PROFILE_FIELDS)
            if not api_key and values["openai_api_key"] == values["deepseek_api_key"]:
                values["openai_api_key"] = ""       # DeepSeek 키가 겸용으로 복사돼 있던 것. 다른 provider 엔 못 쓴다
        if base_url:
            values["openai_base_url"] = base_url
        if api_key:
            values["deepseek_api_key" if target == "deepseek" else "openai_api_key"] = api_key
            if target == "deepseek":
                values["openai_api_key"] = api_key
    return ModelSettings(_env_file=None, **values)  # type: ignore[call-arg]


def load_model_settings(env_file: str | None = None) -> ModelSettings:
    """CLI/런타임 진입점. env_file 미지정 시 ~/.koda/.env 와 CWD/.env 를 겹쳐 읽는다(환경변수 > CWD > 전역)."""
    files = _env_files(env_file)
    if files:
        return ModelSettings(_env_file=files)  # type: ignore[call-arg]
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
    files = _env_files(env_file)
    if files:
        return LangfuseSettings(_env_file=files)  # type: ignore[call-arg]
    return LangfuseSettings()
