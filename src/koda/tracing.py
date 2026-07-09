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
        # 종료 후엔 특정 trace URL 을 못 얻어 langfuse UI 주소를 대신 반환한다.
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
