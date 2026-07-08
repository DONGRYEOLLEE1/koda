"""Langfuse 기반 추적. 로컬 파일 저장 없이 langfuse 로 보낸다.

CallbackHandler 를 에이전트 실행 config 에 넘기면, 각 에이전트/툴/모델 호출의 입출력과
**단계별 token 사용량**이 자동으로 langfuse trace 로 기록된다. 키가 없거나 langfuse 가
닿지 않으면 추적을 조용히 끄고 실행은 계속한다.
"""
from __future__ import annotations

import os

from .config import load_langfuse_settings


class Tracer:
    def __init__(self, handler=None, client=None, host: str = ""):
        self._handler = handler
        self._client = client
        self._host = host

    @property
    def enabled(self) -> bool:
        return self._handler is not None

    @property
    def callbacks(self) -> list:
        return [self._handler] if self._handler else []

    def flush(self) -> None:
        if self._client:
            try:
                self._client.flush()
            except Exception:
                pass

    def trace_url(self):
        # 실행 종료 후에는 active span 컨텍스트가 없어 특정 trace URL 을 얻을 수 없으므로
        # langfuse UI 주소를 반환한다(경고 회피).
        return self._host if self._client else None


def get_tracer(env_file: str | None = None) -> Tracer:
    settings = load_langfuse_settings(env_file)
    if not settings.enabled:
        return Tracer()
    os.environ["LANGFUSE_HOST"] = settings.host
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.public_key
    os.environ["LANGFUSE_SECRET_KEY"] = settings.secret_key
    try:
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler

        client = get_client()
        if not client.auth_check():
            return Tracer()
        return Tracer(CallbackHandler(), client, host=settings.host)
    except Exception:
        return Tracer()
