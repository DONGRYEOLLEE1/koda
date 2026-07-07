"""LLM 초기화: langchain `init_chat_model`. 로컬 llama.cpp(OpenAI 호환) 우선 + OpenAI 폴백.

openai SDK 직접 호출 대신 langchain chat model 을 쓴다. provider 선택은 로컬 헬스체크로 한다.
"""
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
        # 폭주 차단용 넉넉한 상한: 추론 모델의 퇴행성 무한 사고(실측 300s+ 무출력)를 유한하게
        # 끊는다. 정상 출력은 최대 ~1.9k 토큰(실측)이라 잘리지 않는다. (짧은 캡 512는 정상
        # 추론까지 잘라 빈 판정→오탐을 냈으므로 금지 — 반드시 넉넉하게)
        max_tokens=4096,
        # 스트리밍 생성도 토큰 사용량을 보고하게 한다(stream_options.include_usage).
        # 없으면 에이전트(astream) 호출들이 usage 미보고 → langfuse 합계가 크게 과소집계된다(실측).
        stream_usage=True,
    )
    return chat, provider, reason


def _thinking(model, budget_tokens: int):
    """Qwen thinking 활성 + **에이전트별 reasoning 예산** — 새 인스턴스 생성(_no_think 와 동일
    사유: bind() 의 extra_body 는 bind_tools() 를 통과하며 소실된다).

    `thinking_budget_tokens` 는 llama.cpp 최상위 요청 파라미터다(PR #20297, b8287+).
    reasoning 이 예산에 도달하면 서버 샘플러가 think 블록을 강제 종료해 본문 생성으로
    전환시킨다 — "reasoning 만 생성하다 output 0" 폭주를 요청 단위로 차단한다.

    주의(b9430 실측): 서버가 `--reasoning-budget` CLI 플래그와 함께 떠 있으면 CLI 값이
    요청 값에 우선해 이 파라미터가 무시된다. 요청별 차등 예산을 쓰려면 서버를 플래그
    없이(-1) 띄워야 한다. (최신 master 는 우선순위가 반대 — 요청 값 우선)
    """
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
        extra_body={"chat_template_kwargs": {"enable_thinking": True},
                    "thinking_budget_tokens": budget_tokens},
        stream_usage=True,
    )


def _no_think(model):
    """Qwen thinking 비활성 — bind() 대신 init_chat_model 로 새 인스턴스를 생성한다.

    model.bind(extra_body={"chat_template_kwargs": {"enable_thinking": False}}) 는
    bind_tools() 를 통과하면 extra_body 가 소실된다. _ChatModelBinding 래퍼의 kwargs 에만
    저장되고, bind_tools() 내부에서 BaseChatModel.bind() 가 self(ChatOpenAI) 로 직접
    새 바인딩을 만들기 때문이다. init_chat_model 로 ChatOpenAI.extra_body 필드에 직접
    설정해야 bind_tools() 체인에서도 보존된다. (trace: llm.py _no_think 검증 2026-07-06)

    coder 가 대형 편집 tool-call 을 구성하는 지점에서 퇴행성 무한 사고(실측: 무출력 300s+,
    캡 하에선 4096 토큰 전부 사고로 소진 → 빈 생성)에 빠지는 것을 원천 차단한다.
    실측: 동일 판정 11.0s/760tok → 0.3s/11tok.
    적용: router·coder·verifier(판정, §2.1 json_schema 와 세트) + 빈 응답 재시도 강등(§1.3).
    reader 1차 시도는 분석 품질을 위해 thinking 유지.
    """
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
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        stream_usage=True,
    )
