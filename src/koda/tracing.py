from __future__ import annotations

import base64
import json
import os
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from .config import load_langfuse_settings


class Turn:
    """한 턴의 trace 핸들. done(answer) 가 최종 답변을 기록하면 turn 종료 시 trace 출력으로 실린다."""

    def __init__(self) -> None:
        self.answer = ""

    def done(self, answer: str) -> None:
        self.answer = answer


class Tracer:
    def __init__(self, handler=None, client=None, host: str = "", auth: tuple[str, str] = ("", "")):
        self._handler = handler
        self._client = client
        self._host = host
        self._auth = auth
        self._url = None

    @property
    def enabled(self) -> bool:
        return self._handler is not None

    @property
    def callbacks(self) -> list:
        return [self._handler] if self._handler else []

    @contextmanager
    def turn(self, name: str, task: str):
        """한 턴을 trace 하나로 감싼다. 이름은 세션 제목, 입력은 질의 원문이며 그래프 실행은 그 아래에 매달린다."""
        if not self._client:
            yield Turn()
            return
        started = datetime.now(timezone.utc)
        with self._client.start_as_current_observation(name=name, as_type="span", input=task, end_on_exit=False) as span:
            span.set_trace_io(input=task)
            # root 를 자식보다 먼저 서버에 보낸다. 자식이 먼저 닿으면 langfuse 워커가 이름 없는 임시 trace 레코드를
            # 만들고, 그 병합이 root 의 병합보다 늦게 끝나면 이름·입출력이 빈 값으로 덮인다.
            span.end()
            self._client.flush()
            trace_id = self._client.get_current_trace_id()
            self._url = self._client.get_trace_url(trace_id=trace_id)
            turn = Turn()
            yield turn
        self._client.flush()
        self._upsert_trace(trace_id, started, name, task, turn.answer)

    def _upsert_trace(self, trace_id: str, started: datetime, name: str, task: str, answer: str) -> None:
        """trace 의 이름·입력·출력을 ingestion API 로 확정한다. 처리 순서와 무관하도록 값을 전부 싣는다."""
        event = {"id": uuid4().hex, "type": "trace-create", "timestamp": datetime.now(timezone.utc).isoformat(),
                 "body": {"id": trace_id, "timestamp": started.isoformat(), "name": name, "input": task, "output": answer}}
        token = base64.b64encode(":".join(self._auth).encode()).decode()
        req = urllib.request.Request(
            f"{self._host.rstrip('/')}/api/public/ingestion", data=json.dumps({"batch": [event]}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except Exception:
            pass  # 추적 실패가 턴을 실패시키지는 않는다

    def flush(self) -> None:
        if self._client:
            try:
                self._client.flush()
            except Exception:
                pass

    def trace_url(self):
        # 턴을 감싼 뒤에는 그 trace 의 URL, 아니면 langfuse UI 주소.
        return self._url or (self._host if self._client else None)


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
        return Tracer(CallbackHandler(), client, host=settings.host, auth=(settings.public_key, settings.secret_key))
    except Exception:
        return Tracer()
