from __future__ import annotations

from koda.tools import vision
from koda.tools.vision import make_vision_tools
from koda.workspace import Workspace


class _NoModel:
    def with_structured_output(self, *args, **kwargs):  # pragma: no cover - 호출되면 실패 경로가 깨진다
        raise AssertionError("model must not be called")


def _tool(ws: Workspace, props_url: str = "http://llama.test/props"):
    return {t.name: t for t in make_vision_tools(ws, _NoModel(), props_url)}["analyze_image"]


def test_rejects_disallowed_extension(make_workspace):
    tool = _tool(make_workspace({"note.txt": "not an image"}))

    out = tool.invoke({"path": "note.txt", "question": ""})

    assert "오류" in out


def test_rejects_file_over_size_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 4)
    (tmp_path / "big.png").write_bytes(b"12345")
    tool = _tool(Workspace(tmp_path))

    out = tool.invoke({"path": "big.png"})

    assert "오류" in out


def test_sandbox_escape_returns_error(make_workspace):
    tool = _tool(make_workspace({}))

    out = tool.invoke({"path": "../x.png"})

    assert "오류" in out


def test_probe_false_cached_returns_mmproj_guidance(monkeypatch, tmp_path):
    vision._VISION_SUPPORT_CACHE.clear()
    calls = 0

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"modalities": {"vision": False}}

    def fake_get(url, timeout):
        nonlocal calls
        calls += 1
        assert url == "http://llama.test/props"
        assert timeout == 3
        return _Resp()

    monkeypatch.setattr(vision.httpx, "get", fake_get)
    (tmp_path / "tiny.png").write_bytes(b"png")
    tool = _tool(Workspace(tmp_path))

    first = tool.invoke({"path": "tiny.png"})
    second = tool.invoke({"path": "tiny.png"})

    assert "오류" in first and "mmproj" in first
    assert second == first
    assert calls == 1


def test_probe_none_on_non_llamacpp_server_attempts_analysis(monkeypatch, tmp_path):
    # LiteLLM·vLLM·OpenAI 처럼 /props 가 없는 서버면 llama.cpp 안내 대신 실제 요청으로 판단한다.
    vision._VISION_SUPPORT_CACHE.clear()

    def fake_get(url, timeout):
        raise vision.httpx.HTTPError("404")

    monkeypatch.setattr(vision.httpx, "get", fake_get)
    monkeypatch.setattr(vision, "structured", lambda model, schema, messages, **kw: schema(summary="화면 캡처", details=["로고"]))
    (tmp_path / "shot.png").write_bytes(b"png")
    out = _tool(Workspace(tmp_path), "http://gateway.test/props").invoke({"path": "shot.png"})
    assert "mmproj" not in out and "화면 캡처" in out

    # 요청이 실패하면 llama.cpp 를 지목하지 않는 일반 오류로 돌아온다
    def boom(model, schema, messages, **kw):
        raise RuntimeError("image input not supported")

    monkeypatch.setattr(vision, "structured", boom)
    out = _tool(Workspace(tmp_path), "http://gateway.test/props").invoke({"path": "shot.png"})
    assert "오류" in out and "llama.cpp" not in out and "RuntimeError" in out
