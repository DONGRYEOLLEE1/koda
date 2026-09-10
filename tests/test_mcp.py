from __future__ import annotations

import json

import pytest

from koda.mcp import connect_mcp, load_mcp_configs, mcp_dirs, mcp_servers


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
    # 전역(~/.koda/mcps) 다음 프로젝트(<workspace>/mcps). CWD 는 무관하다.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    ws = tmp_path / "proj"
    assert mcp_dirs(ws) == [tmp_path / "home" / ".koda" / "mcps", (ws / "mcps").resolve()]
    assert mcp_dirs(ws, str(tmp_path / "custom"))[-1] == (tmp_path / "custom").resolve()


def test_project_overrides_global_by_server_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    g = tmp_path / "home" / ".koda" / "mcps"; g.mkdir(parents=True)
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


def test_env_ref_is_expanded_from_environment_and_env_file(tmp_path, monkeypatch):
    """설정의 ${VAR} 는 환경변수와 계층 .env 로 채운다. API 키를 JSON 에 평문으로 두지 않기 위한 것."""
    from koda.mcp import mcp_servers

    home = tmp_path / "home"
    g = home / ".koda" / "mcps"; g.mkdir(parents=True)
    (home / ".koda" / ".env").write_text("EXA_KEY=from-env-file\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("EXA_KEY", raising=False)
    monkeypatch.setenv("HF_TOKEN", "from-environ")
    monkeypatch.chdir(tmp_path)
    _write(g, "exa.json", {"mcpServers": {"exa": {"url": "https://mcp.exa.ai/mcp?exaApiKey=${EXA_KEY}"}}})
    _write(g, "hf.json", {"mcpServers": {"hf": {"url": "https://hf/mcp",
                                                "headers": {"Authorization": "Bearer ${HF_TOKEN}"}}}})
    servers = {s.name: s for s in mcp_servers([g])}
    assert servers["exa"].conn["url"].endswith("exaApiKey=from-env-file")     # .env 파일에서
    assert servers["hf"].conn["headers"]["Authorization"] == "Bearer from-environ"   # 환경변수에서
    assert not servers["exa"].error and not servers["hf"].error
    # 표시용 값에는 키가 드러나지 않는다
    assert servers["exa"].target == "https://mcp.exa.ai/mcp?exaApiKey=***"


def test_unresolved_env_ref_marks_server_and_skips_connect(tmp_path, monkeypatch):
    """참조한 변수가 없으면 연결을 시도하지 않고 무엇이 빠졌는지 알린다."""
    import asyncio

    from koda.mcp import _open, mcp_servers

    home = tmp_path / "home"
    g = home / ".koda" / "mcps"; g.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MISSING_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    _write(g, "x.json", {"mcpServers": {"x": {"url": "https://x/mcp?k=${MISSING_KEY}"}}})
    server = mcp_servers([g])[0]
    assert server.error == "환경변수 미설정: MISSING_KEY"
    assert server.active_tools == []
    asyncio.run(_open(server, 1.0))          # 연결을 건너뛴다(예외·대기 없음)
    assert server.tools == []


def test_stdio_command_string_is_split_into_args(tmp_path):
    """널리 쓰이는 한 줄 표기(`"command": "npx -y pkg url"`)를 command 와 args 로 쪼갠다. 어댑터가 stdio 에 args 를 요구한다."""
    _write(tmp_path, "a.json", {"mcpServers": {"oneline": {
        "command": "npx -y mcp-remote https://mcp.tavily.com/mcp/?tavilyApiKey=K"}}})
    _write(tmp_path, "b.json", {"mcpServers": {"split": {"command": "npx", "args": ["-y", "pkg"]}}})
    _write(tmp_path, "c.json", {"mcpServers": {"bare": {"command": "my-server"}}})
    _write(tmp_path, "d.json", {"mcpServers": {"quoted": {"command": 'sh -c "echo hi"'}}})
    conns = load_mcp_configs(str(tmp_path))
    assert conns["oneline"]["command"] == "npx"
    assert conns["oneline"]["args"] == ["-y", "mcp-remote", "https://mcp.tavily.com/mcp/?tavilyApiKey=K"]
    assert conns["split"]["args"] == ["-y", "pkg"]          # 이미 나뉜 설정은 그대로
    assert conns["bare"]["args"] == []                       # 인자 없는 명령도 args 를 채운다
    assert conns["quoted"]["args"] == ["-c", "echo hi"]      # 따옴표를 존중한다


# --- 설정 쓰기·병합: koda mcp 서브커맨드와 /mcp 화면이 함께 쓰는 계약 ---

def test_partial_override_keeps_definition(tmp_path, monkeypatch):
    """프로젝트가 enabled 한 줄만 둬도 전역의 url·transport 를 잃지 않는다(키 단위 병합)."""
    from koda.mcp import mcp_servers

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    g = tmp_path / "home" / ".koda" / "mcps"; g.mkdir(parents=True)
    ws = tmp_path / "proj"; (ws / "mcps").mkdir(parents=True)
    _write(g, "exa.json", {"mcpServers": {"exa": {"url": "https://mcp.exa.ai/mcp"}}})
    _write(ws / "mcps", "exa.json", {"mcpServers": {"exa": {"enabled": False}}})
    s = mcp_servers(mcp_dirs(ws))[0]
    assert s.name == "exa" and s.conn["url"] == "https://mcp.exa.ai/mcp"
    assert s.transport == "http" and s.enabled is False
    assert s.source == "전역"                       # 정의를 준 곳이 출처다
    assert "enabled" not in s.conn                  # 어댑터 인자로는 넘기지 않는다


def test_disabled_server_is_not_connected(tmp_path, monkeypatch):
    import asyncio

    import koda.mcp as M

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    d = tmp_path / "home" / ".koda" / "mcps"; d.mkdir(parents=True)
    _write(d, "a.json", {"mcpServers": {"on": {"url": "https://a"}, "off": {"url": "https://b", "enabled": False}}})
    tried = []

    async def fake_connect(server, timeout=20.0):
        tried.append(server.name)

    monkeypatch.setattr(M, "connect_server", fake_connect)
    names = [s.name for s in asyncio.run(M.connect_mcp([d]))]
    assert sorted(names) == ["off", "on"] and tried == ["on"]


def test_write_find_remove_config(tmp_path, monkeypatch):
    from koda.mcp import config_path, find_config, remove_config, write_config

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ws = tmp_path / "proj"; ws.mkdir()
    user = config_path("exa", "user", ws)
    proj = config_path("local", "project", ws)
    assert user == tmp_path / "home" / ".koda" / "mcps" / "exa.json"
    assert proj == (ws / "mcps" / "local.json").resolve()
    write_config(user, "exa", {"url": "https://mcp.exa.ai/mcp"})
    write_config(proj, "local", {"command": "npx thing"})
    assert find_config("exa", ws) == user and find_config("local", ws) == proj
    assert find_config("nope", ws) is None
    assert remove_config("exa", ws) == user and not user.exists()   # 남는 서버가 없으면 파일도 지운다
    assert remove_config("exa", ws) is None


def test_stdio_child_stderr_goes_to_log_not_terminal(tmp_path, monkeypatch):
    """stdio 서버의 stderr 는 로그 파일로 간다. fd 2 는 건드리지 않는다(Textual 이 그 fd 에 화면을 그린다)."""
    import asyncio
    import os
    import sys

    from koda.mcp import child_log_path, close_mcp, connect_mcp

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    noisy = tmp_path / "noisy.py"
    noisy.write_text("import sys; print('서버가 쏟는 로그', file=sys.stderr, flush=True)\n"
                     "import time; time.sleep(2)\n", encoding="utf-8")
    d = tmp_path / "mcps"; d.mkdir()
    _write(d, "noisy.json", {"mcpServers": {"noisy": {"command": f"{sys.executable} {noisy}"}}})
    ws = tmp_path / "proj"; ws.mkdir()

    fd2_before = os.fstat(2)
    try:
        asyncio.run(connect_mcp([d], timeout=3.0, cwd=ws))
    finally:
        close_mcp()
    assert os.path.samestat(os.fstat(2), fd2_before)          # 앱의 stderr 는 그대로다
    log = child_log_path(ws)
    assert log.is_file() and "서버가 쏟는 로그" in log.read_text(encoding="utf-8")


def test_child_log_path_and_rotation(tmp_path):
    from koda.mcp import _CHILD_LOG_MAX, child_log_path

    assert child_log_path(tmp_path) == tmp_path / ".koda" / "logs" / "mcp.log"
    assert _CHILD_LOG_MAX > 0                                  # 열 때 이 크기를 넘으면 비운다


def test_broken_config_file_is_surfaced_not_swallowed(tmp_path):
    # 쉼표 하나에 서버가 사라지는데 아무 표시가 없었다 (#78). 파일명 그대로 error 항목에 올린다.
    (tmp_path / "good.json").write_text(
        '{"mcpServers": {"good": {"url": "https://ok.example/mcp"}}}', encoding="utf-8")
    (tmp_path / "bad.json").write_text(
        '{"mcpServers": {"broken": {"url": "https://y.example/mcp"}, }}', encoding="utf-8")
    servers = {s.name: s for s in mcp_servers([tmp_path])}
    assert servers["good"].error == ""              # 정상 파일은 영향받지 않는다
    assert "형식 오류" in servers["bad.json"].error   # 서버명을 알 수 없으니 고칠 파일을 가리킨다
    assert servers["bad.json"].active_tools == []


def test_config_without_mcp_servers_key_is_reported(tmp_path):
    # mcpServers 래퍼를 빠뜨린 파일도 조용히 사라지면 안 된다
    (tmp_path / "nowrapper.json").write_text('{"url": "https://x.example/mcp"}', encoding="utf-8")
    servers = {s.name: s for s in mcp_servers([tmp_path])}
    assert "형식 오류" in servers["nowrapper.json"].error


def test_config_error_survives_connect(tmp_path):
    # 연결을 시도하면 성공 경로가 error 를 지워 도구 0개인 정상 서버처럼 보였다.
    # 설정 단계 오류는 연결로 해소되지 않으므로 시도하지 않고 그대로 남긴다.
    import asyncio
    (tmp_path / "needsvar.json").write_text(
        '{"mcpServers": {"needsvar": {"url": "https://x.example/mcp?key=${NO_SUCH_VAR_XYZ}"}}}',
        encoding="utf-8")
    (tmp_path / "bad.json").write_text('{"mcpServers": {"b": {}, }}', encoding="utf-8")
    servers = {s.name: s for s in asyncio.run(connect_mcp([tmp_path], timeout=2.0))}
    assert servers["needsvar"].error.startswith("환경변수 미설정")
    assert servers["bad.json"].error.startswith("형식 오류")


def test_load_mcp_configs_keeps_dict_signature(tmp_path):
    # 깨진 파일을 함께 다루게 됐어도 이 함수의 반환은 서버 설정 dict 그대로다(mcpcli 가 쓴다)
    (tmp_path / "good.json").write_text(
        '{"mcpServers": {"good": {"url": "https://ok.example/mcp"}}}', encoding="utf-8")
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    conns = load_mcp_configs(str(tmp_path))
    assert list(conns) == ["good"]
