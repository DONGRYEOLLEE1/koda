from __future__ import annotations

import json

import pytest

from koda.cli import main
from koda.mcpcli import _name_from_command, _name_from_url, spec_from_text


@pytest.mark.parametrize("command,expected", [
    ("npx @playwright/mcp@latest", "playwright"),
    ("npx -y @modelcontextprotocol/server-github", "github"),
    ("npx -y @modelcontextprotocol/server-filesystem /srv/data", "filesystem"),
    ("uvx mcp-server-fetch", "fetch"),
    ("npx -y mcp-remote https://mcp.linear.app/sse", "linear"),   # 브리지는 대상 URL 로 이름을 짓는다
    ("docker run -i --rm mcp/postgres", "postgres"),
    ("node /path/to/my-server.js", "my-server"),
])
def test_name_from_command(command, expected):
    assert _name_from_command(command) == expected


@pytest.mark.parametrize("url,expected", [
    ("https://mcp.exa.ai/mcp", "exa"),
    ("https://mcp.context7.com/mcp", "context7"),
    ("https://huggingface.co/mcp", "huggingface"),
    ("https://mcp.tavily.com/mcp/?tavilyApiKey=K", "tavily"),
])
def test_name_from_url(url, expected):
    assert _name_from_url(url) == expected


def test_spec_from_text_accepts_snippet_url_and_command():
    assert spec_from_text('{"mcpServers":{"exa":{"url":"https://x/mcp"}}}') == ("exa", {"url": "https://x/mcp"})
    assert spec_from_text("https://mcp.exa.ai/mcp") == ("exa", {"url": "https://mcp.exa.ai/mcp"})
    assert spec_from_text("npx @playwright/mcp@latest") == ("playwright", {"command": "npx @playwright/mcp@latest"})
    assert spec_from_text("   ") is None
    assert spec_from_text("{망가진") is None


def _run(monkeypatch, home, argv):
    monkeypatch.setenv("HOME", str(home))
    return main(argv)


def test_add_list_remove_roundtrip(tmp_path, monkeypatch, capsys):
    home, ws = tmp_path / "home", tmp_path / "proj"
    ws.mkdir()
    assert _run(monkeypatch, home, ["mcp", "add", "--url", "https://mcp.exa.ai/mcp",
                                    "--no-probe", "--workspace", str(ws)]) == 0
    saved = json.loads((home / ".koda" / "mcps" / "exa.json").read_text(encoding="utf-8"))
    assert saved == {"mcpServers": {"exa": {"url": "https://mcp.exa.ai/mcp"}}}   # 이름은 URL 에서 추론

    assert _run(monkeypatch, home, ["mcp", "add", "hf", "--url", "https://huggingface.co/mcp",
                                    "--header", "Authorization: Bearer ${HF_TOKEN}",
                                    "--no-probe", "--workspace", str(ws)]) == 0
    hf = json.loads((home / ".koda" / "mcps" / "hf.json").read_text(encoding="utf-8"))
    assert hf["mcpServers"]["hf"]["headers"] == {"Authorization": "Bearer ${HF_TOKEN}"}

    assert _run(monkeypatch, home, ["mcp", "list", "--workspace", str(ws)]) == 0
    out = capsys.readouterr().out
    assert "exa" in out and "hf" in out and "설정 오류" in out      # HF_TOKEN 미설정이 드러난다

    assert _run(monkeypatch, home, ["mcp", "remove", "exa", "--workspace", str(ws)]) == 0
    assert not (home / ".koda" / "mcps" / "exa.json").exists()
    assert _run(monkeypatch, home, ["mcp", "remove", "exa", "--workspace", str(ws)]) == 1


def test_add_stdio_after_double_dash(tmp_path, monkeypatch):
    home, ws = tmp_path / "home", tmp_path / "proj"
    ws.mkdir()
    assert _run(monkeypatch, home, ["mcp", "add", "--no-probe", "--workspace", str(ws),
                                    "--", "npx", "-y", "@modelcontextprotocol/server-github"]) == 0
    saved = json.loads((home / ".koda" / "mcps" / "github.json").read_text(encoding="utf-8"))
    assert saved["mcpServers"]["github"]["command"] == "npx -y @modelcontextprotocol/server-github"


def test_disable_scope_global_vs_here(tmp_path, monkeypatch):
    from koda.mcp import mcp_dirs, mcp_servers

    home = tmp_path / "home"
    ws1, ws2 = tmp_path / "p1", tmp_path / "p2"
    ws1.mkdir(); ws2.mkdir()
    _run(monkeypatch, home, ["mcp", "add", "pw", "--no-probe", "--workspace", str(ws1), "--", "npx", "pkg"])

    def enabled(ws):
        return {s.name: s.enabled for s in mcp_servers(mcp_dirs(ws))}["pw"]

    assert _run(monkeypatch, home, ["mcp", "disable", "pw", "--workspace", str(ws1)]) == 0
    assert enabled(ws1) is False and enabled(ws2) is False        # 전역 정의를 껐으니 어디서나 꺼짐

    assert _run(monkeypatch, home, ["mcp", "enable", "pw", "--workspace", str(ws1)]) == 0
    assert _run(monkeypatch, home, ["mcp", "disable", "pw", "--here", "--workspace", str(ws1)]) == 0
    assert enabled(ws1) is False and enabled(ws2) is True         # 이 워크스페이스만 꺼짐
    override = json.loads((ws1 / "mcps" / "pw.json").read_text(encoding="utf-8"))
    assert override == {"mcpServers": {"pw": {"enabled": False}}}  # 정의를 복사하지 않는다

    assert _run(monkeypatch, home, ["mcp", "disable", "없는서버", "--workspace", str(ws1)]) == 1
