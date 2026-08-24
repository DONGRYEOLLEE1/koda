from __future__ import annotations

import httpx
from langchain.chat_models import init_chat_model

from .config import ModelSettings


def _health(base_url: str, timeout: float = 3.0) -> bool:
    """로컬 엔드포인트가 실제로 200 을 주는지 확인한다(404/500 은 unhealthy)."""
    try:
        resp = httpx.get(base_url.rstrip("/") + "/models", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def select_provider(settings: ModelSettings, health=_health) -> tuple[str, str]:
    """규칙 코드는 판단하지 않고 헬스체크로 provider 만 고른다."""
    if settings.primary == "local" and health(settings.local_base_url):
        return "local", "local healthy"
    if settings.fallback_enabled and settings.openai_api_key:
        return "openai", "local unavailable -> openai fallback"
    return "local", "local unhealthy, no fallback"


def build_model(settings: ModelSettings):
    """(chat_model, provider, reason). 로컬/OpenAI 모두 OpenAI 호환 → base_url/api_key/model 만 교체."""
    provider, reason = select_provider(settings)
    if provider == "openai":
        base, key, model = settings.openai_base_url, settings.openai_api_key, settings.openai_model
    else:
        base, key, model = settings.local_base_url, settings.local_api_key, settings.local_model
    chat = init_chat_model(
        model,
        model_provider="openai",
        base_url=base,
        api_key=key or "nothing",
        temperature=0,
        # 폭주 차단용 넉넉한 상한 — 짧은 캡은 정상 추론까지 잘라 빈 판정을 냈으므로 반드시 넉넉하게 유지한다.
        max_tokens=4096,
        # 스트리밍 생성도 토큰 사용량을 보고하게 한다 — 없으면 astream 호출의 usage 가 langfuse 에 과소집계된다.
        stream_usage=True,
    )
    return chat, provider, reason


def reasoning_params(settings: ModelSettings, enabled: bool, budget: int = 0) -> dict:
    """엔드포인트가 존중하는 reasoning 파라미터만 조립한다. 미지원(off)이면 빈 dict — 알 수 없는 필드를 실어 보내 400 으로 거절당하지 않게 한다."""
    if settings.reasoning_style == "qwen":  # llama.cpp 는 chat_template 인자로 사고를 켜고 끈다
        extra: dict = {"chat_template_kwargs": {"enable_thinking": enabled}}
        if enabled and budget:
            extra["thinking_budget_tokens"] = budget  # 최상위 요청 파라미터 (chat_template_kwargs 밖!)
        return {"extra_body": extra}
    if settings.reasoning_style == "effort":  # vLLM·OpenAI gpt-5 계열은 등급으로 제어한다
        # langchain_openai 의 정식 필드라 extra_body 우회가 필요 없다(Responses API 로도 변환된다).
        return {"reasoning_effort": settings.reasoning_effort if enabled else "none"}
    return {}


def _derive(model, settings: ModelSettings, enabled: bool, budget: int = 0):
    """reasoning 설정만 바꾼 새 인스턴스. bind() 가 아니라 재생성인 이유는 extra_body 가 bind_tools() 를 통과할 때 소실되기 때문이다."""
    params = reasoning_params(settings, enabled, budget)
    if not params:
        return model  # 미지원 엔드포인트 — 바꿀 파라미터가 없으니 새 인스턴스를 만들지 않는다
    api_key = model.openai_api_key
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()
    return init_chat_model(
        model.model_name,
        model_provider="openai",
        base_url=model.openai_api_base,
        api_key=api_key or "nothing",
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        stream_usage=True,
        **params,
    )


def _thinking(model, settings: ModelSettings, budget_tokens: int):
    """reasoning 활성 인스턴스 + 에이전트별 예산. llama.cpp 서버가 --reasoning-budget 플래그로 뜨면 요청별 예산이 무시되니 플래그 없이(-1) 띄워야 한다."""
    return _derive(model, settings, enabled=True, budget=budget_tokens)


def _no_think(model, settings: ModelSettings):
    """reasoning 비활성 인스턴스. coder 의 퇴행성 무한 사고(빈 생성)를 차단한다."""
    return _derive(model, settings, enabled=False)
