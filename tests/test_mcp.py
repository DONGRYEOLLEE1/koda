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


def test_mcp_servers_label_source_and_project_wins(tmp_path, monkeypatch):
    import koda.mcp as M
    g, proj = tmp_path / "global", tmp_path / "proj"
    for d in (g, proj):
        d.mkdir()
    (g / "a.json").write_text(json.dumps({"mcpServers": {"ctx": {"url": "http://g/ctx"}, "only_g": {"command": "x", "args": ["--y"]}}}))
    (proj / "b.json").write_text(json.dumps({"mcpServers": {"ctx": {"url": "http://p/ctx"}}}))
    monkeypatch.setattr(M, "_global_dir", lambda: g)
    servers = {s.name: s for s in M.mcp_servers([g, proj])}
    assert servers["ctx"].source == "프로젝트" and servers["ctx"].target == "http://p/ctx"     # 같은 이름은 프로젝트가 이긴다
    assert servers["only_g"].source == "전역" and servers["only_g"].transport == "stdio" and servers["only_g"].target == "x --y"
    assert "cwd" not in servers["only_g"].conn
    with_cwd = {s.name: s for s in M.mcp_servers([g, proj], cwd=tmp_path / "ws")}
    assert with_cwd["only_g"].conn["cwd"] == str(tmp_path / "ws") and "cwd" not in with_cwd["ctx"].conn   # stdio 만, http 는 무관


def _fake_adapters(monkeypatch, log: list):
    """세션 기반 가짜 어댑터. 세션이 열린 태스크와 닫힌 태스크, 도구 코루틴이 돈 루프를 기록한다."""
    import asyncio
    import sys
    import types
    from contextlib import asynccontextmanager

    from langchain_core.tools import StructuredTool

    class _Client:
        def __init__(self, conns):
            self._name = next(iter(conns))

        @asynccontextmanager
        async def session(self, name):
            if name == "bad":
                raise ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionError("refused")])
            log.append(("enter", name, asyncio.current_task()))
            try:
                yield types.SimpleNamespace(name=name)
            finally:
                log.append(("exit", name, asyncio.current_task()))

    async def load_mcp_tools(session, server_name=None):
        async def echo(text: str) -> str:
            log.append(("call", server_name, asyncio.get_running_loop()))
            return f"{server_name}:{text}"
        return [StructuredTool.from_function(coroutine=echo, name=f"{server_name}_echo", description="echo")]

    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", types.SimpleNamespace(MultiServerMCPClient=_Client))
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.tools", types.SimpleNamespace(load_mcp_tools=load_mcp_tools))


def test_connect_mcp_isolates_failures_and_active_tools(tmp_path, monkeypatch):
    import asyncio

    import koda.mcp as M
    (tmp_path / "s.json").write_text(json.dumps({"mcpServers": {"ok": {"url": "http://ok"}, "bad": {"url": "http://bad"}}}))
    log: list = []
    _fake_adapters(monkeypatch, log)
    servers = asyncio.run(M.connect_mcp(tmp_path))
    by = {s.name: s for s in servers}
    assert [t.name for t in by["ok"].tools] == ["ok_echo"] and not by["ok"].error
    assert by["bad"].tools == [] and by["bad"].error.startswith("ConnectionError")   # 하나가 죽어도 나머지는 산다. ExceptionGroup 은 원인으로 풀어 보인다
    by["ok"].enabled = False
    assert by["ok"].active_tools == [] and by["bad"].active_tools == []              # 꺼진 서버·실패 서버는 도구 0
    asyncio.run(M.disconnect_server(by["ok"]))
    logs = []
    assert [t.name for t in asyncio.run(M.load_mcp_tools(tmp_path, on_log=logs.append))] == ["ok_echo"]
    assert any("실패" in m for m in logs)
    M.close_mcp()


def test_session_persists_across_turn_loops_and_closes_in_same_task(tmp_path, monkeypatch):
    # 세션은 host 루프의 한 태스크가 열고 같은 태스크가 닫는다. 도구 호출은 어느 턴 루프에서 와도 그 세션(host 루프)에서 실행된다.
    import asyncio

    import koda.mcp as M
    (tmp_path / "s.json").write_text(json.dumps({"mcpServers": {"pw": {"command": "x"}}}))
    log: list = []
    _fake_adapters(monkeypatch, log)
    [server] = asyncio.run(M.connect_mcp(tmp_path))
    tool = server.tools[0]
    r1 = asyncio.run(tool.ainvoke({"text": "a"}))          # 턴 1 (새 루프)
    r2 = asyncio.run(tool.ainvoke({"text": "b"}))          # 턴 2 (또 새 루프)
    assert (r1, r2) == ("pw:a", "pw:b")
    calls = [e for e in log if e[0] == "call"]
    assert len(calls) == 2 and all(e[2] is M._host.loop for e in calls)          # 세션 루프에서 실행
    assert len([e for e in log if e[0] == "enter"]) == 1                          # 호출마다 세션을 새로 열지 않는다
    server.enabled = False                                   # 해제는 꺼진 서버에만 적용된다(켜져 있으면 의도 우선)
    asyncio.run(M.disconnect_server(server))
    enter, exit_ = [e for e in log if e[0] == "enter"][0], [e for e in log if e[0] == "exit"][0]
    assert enter[2] is exit_[2] and server.tools == [] and server not in M._host.servers   # 같은 태스크에서 닫힘


def test_toggle_workers_overlapping_end_in_last_intent(tmp_path, monkeypatch):
    # /mcp 의 d 를 연달아 누르면 해제·재연결 워커가 겹친다. 어느 쪽이 host 루프에 먼저 닿든 마지막 의도(enabled)로 끝난다.
    import asyncio

    import koda.mcp as M
    (tmp_path / "s.json").write_text(json.dumps({"mcpServers": {"pw": {"command": "x"}}}))
    log: list = []
    _fake_adapters(monkeypatch, log)
    [server] = asyncio.run(M.connect_mcp(tmp_path))

    async def off_then_on():                       # 끄기 → 켜기, 재연결이 먼저 실행돼도 켜진 채로 끝난다
        server.enabled = False
        d = asyncio.create_task(M.disconnect_server(server))
        server.enabled = True
        c = asyncio.create_task(M.connect_server(server))
        await asyncio.gather(c, d)

    asyncio.run(off_then_on())
    assert [t.name for t in server.tools] == ["pw_echo"] and server in M._host.servers
    assert len([e for e in log if e[0] == "enter"]) - len([e for e in log if e[0] == "exit"]) == 1   # 살아 있는 세션은 하나

    async def on_then_off():                       # 켜기 → 끄기 순서면 끊긴 채로 끝난다
        server.enabled = True
        c = asyncio.create_task(M.connect_server(server))
        server.enabled = False
        d = asyncio.create_task(M.disconnect_server(server))
        await asyncio.gather(c, d)

    asyncio.run(on_then_off())
    assert server.tools == [] and server not in M._host.servers
    assert len([e for e in log if e[0] == "enter"]) == len([e for e in log if e[0] == "exit"])
