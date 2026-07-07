"""핵심 모듈: tracing (langfuse 배선; 키 없으면 조용히 비활성, 실행은 계속)."""
from __future__ import annotations

from build_harness.tracing import Tracer, get_tracer

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
