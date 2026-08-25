from __future__ import annotations

import json

from koda.mcp import load_mcp_configs, mcp_dirs


def _write(d, name, obj):
    (d / name).write_text(json.dumps(obj), encoding="utf-8")


def test_missing_dir_returns_empty(tmp_path):
    assert load_mcp_configs(str(tmp_path / "nope")) == {}


def test_parses_and_normalizes_transport(tmp_path):
    _write(tmp_path, "exa.json", {"mcpServers": {"exa": {"url": "https://mcp.exa.ai/mcp"}}})
    _write(tmp_path, "local.json", {"mcpServers": {"fs": {"command": "python", "args": ["s.py"]}}})
    conns = load_mcp_configs(str(tmp_path))
    assert conns["exa"] == {"url": "https://mcp.exa.ai/mcp", "transport": "streamable_http"}
    assert conns["fs"]["transport"] == "stdio"


def test_non_dict_spec_skipped_others_survive(tmp_path):
    # 비-dict 스펙(문자열 등) 하나가 전체 서버를 드롭하지 않는다
    _write(tmp_path, "bad.json", {"mcpServers": {"bad": "https://x"}})
    _write(tmp_path, "good.json", {"mcpServers": {"good": {"url": "https://g"}}})
    conns = load_mcp_configs(str(tmp_path))
    assert "bad" not in conns
    assert conns["good"]["transport"] == "streamable_http"


def test_dirs_are_global_then_workspace_not_cwd(tmp_path, monkeypatch):
    # 전역(~/.config/koda/mcps) 다음 프로젝트(<workspace>/mcps). CWD 는 무관하다.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    ws = tmp_path / "proj"
    assert mcp_dirs(ws) == [tmp_path / "home" / ".config" / "koda" / "mcps", (ws / "mcps").resolve()]
    assert mcp_dirs(ws, str(tmp_path / "custom"))[-1] == (tmp_path / "custom").resolve()


def test_project_overrides_global_by_server_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    g = tmp_path / "home" / ".config" / "koda" / "mcps"; g.mkdir(parents=True)
    ws = tmp_path / "proj"; (ws / "mcps").mkdir(parents=True)
    _write(g, "exa.json", {"mcpServers": {"exa": {"url": "https://global"}}})
    _write(g, "docs.json", {"mcpServers": {"docs": {"url": "https://docs"}}})
    _write(ws / "mcps", "exa.json", {"mcpServers": {"exa": {"url": "https://project"}}})
    conns = load_mcp_configs(mcp_dirs(ws))
    assert conns["exa"]["url"] == "https://project"     # 프로젝트가 전역을 덮는다
    assert conns["docs"]["url"] == "https://docs"       # 전역 전용 서버는 남는다
