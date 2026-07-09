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
