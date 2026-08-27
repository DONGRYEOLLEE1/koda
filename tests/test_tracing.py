from __future__ import annotations

from koda.tracing import Tracer, get_tracer

_KEYS = ["LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]


def test_tracer_noop_object():
    t = Tracer()
    assert t.enabled is False
    assert t.callbacks == []
    t.flush()                    # no-op, 예외 없음
    assert t.trace_url() is None


def test_get_tracer_disabled_without_keys(monkeypatch, tmp_path):
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("LANGFUSE_PUBLIC_KEY=\nLANGFUSE_SECRET_KEY=\n", encoding="utf-8")
    tracer = get_tracer(env_file=str(env))
    assert tracer.enabled is False   # 키 없으면 추적 비활성
    assert tracer.callbacks == []


def test_noop_tracer_turn_is_harmless():
    t = Tracer()
    with t.turn("제목", "질의") as turn:
        turn.done("답변")                  # 추적이 꺼져 있으면 아무것도 기록하지 않는다
    assert t.trace_url() is None


def test_turn_sends_root_first_then_upserts_trace_io(monkeypatch):
    """root span 을 자식보다 먼저 보내고(임시 trace 레코드 방지), 턴이 끝나면 이름·입출력을 전부 실어 확정한다."""
    from contextlib import contextmanager
    log: list = []

    class _Span:
        def set_trace_io(self, **kw):
            log.append(("trace_io", kw))

        def end(self):
            log.append(("end",))

    class _Client:
        @contextmanager
        def start_as_current_observation(self, **kw):
            log.append(("start", kw["name"], kw["input"], kw["end_on_exit"]))
            yield _Span()

        def flush(self):
            log.append(("flush",))

        def get_current_trace_id(self):
            return "t" * 32

        def get_trace_url(self, trace_id=None):
            return f"http://lf/traces/{trace_id}"

    t = Tracer(handler=object(), client=_Client(), host="http://lf", auth=("pk", "sk"))
    monkeypatch.setattr(t, "_upsert_trace", lambda *a: log.append(("upsert", a[0], a[2], a[3], a[4])))
    with t.turn("제목", "질의") as turn:
        log.append(("graph",))
        turn.done("답변")
    assert log[:5] == [("start", "제목", "질의", False), ("trace_io", {"input": "질의"}), ("end",), ("flush",), ("graph",)]
    assert log[5:] == [("flush",), ("upsert", "t" * 32, "제목", "질의", "답변")]
    assert t.trace_url() == "http://lf/traces/" + "t" * 32
