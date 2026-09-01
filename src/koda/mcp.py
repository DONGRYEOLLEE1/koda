from __future__ import annotations

import asyncio
import atexit
import json
import os
import re
import shlex
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .config import _env_files, koda_home

# 설정 값의 ${VAR} 는 .env·환경변수로 치환한다. API 키를 JSON 에 평문으로 두지 않기 위한 것.
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# 표시용 마스킹: URL 쿼리의 키·토큰 값은 /mcp 화면에 그대로 내보내지 않는다.
_SECRET_PARAM = re.compile(r"(?i)([?&][\w.\-]*(?:key|token|secret|password|auth)[\w.\-]*=)([^&\s]+)")


def _env_values() -> dict:
    """치환에 쓸 값. 계층 .env 파일을 깔고 실제 환경변수가 덮는다(설정 로딩과 같은 우선순위)."""
    from dotenv import dotenv_values

    values: dict = {}
    for f in _env_files():
        values.update({k: v for k, v in dotenv_values(f).items() if v is not None})
    values.update(os.environ)
    return values


def _expand(spec, values: dict):
    """문자열 값의 ${VAR} 를 치환한다. 없는 변수는 그대로 두어 미설정을 드러낸다."""
    if isinstance(spec, str):
        return _ENV_REF.sub(lambda m: values.get(m.group(1), m.group(0)), spec)
    if isinstance(spec, dict):
        return {k: _expand(v, values) for k, v in spec.items()}
    if isinstance(spec, list):
        return [_expand(v, values) for v in spec]
    return spec


def _unresolved(conn: dict) -> list[str]:
    return sorted(set(_ENV_REF.findall(json.dumps(conn, ensure_ascii=False))))


def _normalize(spec: dict) -> dict:
    """설정 축약형을 어댑터가 받는 형태로 채운다. url→streamable_http, command→stdio 이며, 널리 쓰이는 `"command": "npx -y pkg url"` 한 줄 표기는 command 와 args 로 쪼갠다(어댑터가 stdio 에 args 를 요구한다)."""
    spec = dict(spec)
    if "transport" not in spec:
        if "url" in spec:
            spec["transport"] = "streamable_http"
        elif "command" in spec:
            spec["transport"] = "stdio"
    if spec.get("transport") == "stdio" and isinstance(spec.get("command"), str):
        parts = shlex.split(spec["command"])
        if not spec.get("args") and len(parts) > 1:
            spec["command"], spec["args"] = parts[0], parts[1:]
        spec.setdefault("args", [])
    return spec


def _global_dir() -> Path | None:
    """전역 MCP 폴더 ~/.koda/mcps. .env 탐색과 같은 위치를 쓴다."""
    home = koda_home()
    return home / "mcps" if home else None


def mcp_dirs(workspace: str | Path, mcps_dir: str | None = None) -> list[Path]:
    """로드 순서: 전역(~/.koda/mcps) → 프로젝트(<workspace>/mcps 또는 --mcps-dir). 같은 서버명은 뒤(프로젝트)가 이긴다."""
    project = (Path(mcps_dir) if mcps_dir else Path(workspace) / "mcps").resolve()
    g = _global_dir()
    return [g, project] if g and g.resolve() != project else [project]


def load_mcp_configs(dirs) -> dict:
    """폴더들의 *.json|*.yaml 을 순서대로 읽어 {서버명: connection} 으로 병합한다. 단일 경로도 받는다."""
    if isinstance(dirs, (str, Path)):
        dirs = [dirs]
    conns: dict = {}
    for d in dirs:
        conns.update(_read_dir(Path(d)))
    return conns


def _read_dir(d: Path) -> dict:
    if not d.is_dir():
        return {}
    values = _env_values()
    conns: dict = {}
    files = sorted(d.glob("*.json")) + sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml"))
    for f in files:
        try:
            if f.suffix == ".json":
                data = json.loads(f.read_text(encoding="utf-8"))
            else:
                import yaml  # yaml 파일을 쓸 때만 필요(선택적)
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for name, spec in ((data or {}).get("mcpServers") or {}).items():
            if not isinstance(spec, dict):
                continue  # 잘못된 스펙(문자열 등)은 건너뛴다 — 다른 서버 로드에 영향 없음
            conns[name] = _normalize(_expand(spec, values))
    return conns


@dataclass
class McpServer:
    """설정 파일에서 읽은 서버 하나와 연결 결과. enabled 는 세션 안에서만 유지되는 사용자 토글이다."""

    name: str
    source: str            # "전역" | "프로젝트"
    conn: dict
    tools: list = field(default_factory=list)
    error: str = ""
    enabled: bool = True
    _stop: asyncio.Event | None = field(default=None, repr=False)   # 세션을 붙든 태스크의 종료 신호
    _task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def transport(self) -> str:
        return {"streamable_http": "http", "stdio": "stdio"}.get(self.conn.get("transport", ""), self.conn.get("transport", "?"))

    @property
    def target(self) -> str:
        """표시용 대상. http 는 URL, stdio 는 명령줄. URL 쿼리에 실린 키 값은 가린다."""
        if self.conn.get("url"):
            return _SECRET_PARAM.sub(r"\1***", str(self.conn["url"]))
        return " ".join([str(self.conn.get("command", "")), *map(str, self.conn.get("args") or [])]).strip()

    @property
    def active_tools(self) -> list:
        return self.tools if self.enabled and not self.error else []


def mcp_servers(dirs, cwd: str | Path | None = None) -> list[McpServer]:
    """폴더들을 순서대로 읽어 서버 목록을 만든다. 같은 이름은 뒤(프로젝트)가 이긴다. stdio 서버의 작업 디렉터리는 설정에 없으면 cwd(워크스페이스)로 둔다."""
    if isinstance(dirs, (str, Path)):
        dirs = [dirs]
    g = _global_dir()
    servers: dict[str, McpServer] = {}
    for d in dirs:
        d = Path(d)
        source = "전역" if g and d.resolve() == g.resolve() else "프로젝트"
        for name, conn in _read_dir(d).items():
            if cwd and conn.get("transport") == "stdio" and not conn.get("cwd"):
                conn = {**conn, "cwd": str(cwd)}   # 서버가 koda 의 CWD 가 아니라 워크스페이스에서 파일을 쓰게 한다
            missing = _unresolved(conn)
            error = f"환경변수 미설정: {', '.join(missing)}" if missing else ""
            servers[name] = McpServer(name, source, conn, error=error)
    return list(servers.values())


class _Host:
    """MCP 세션을 붙들어 두는 전용 이벤트 루프 스레드. koda 는 턴마다 asyncio.run 을 새로 돌리므로 세션은 여기서 열어 두고 도구 호출만 넘겨 받는다. Playwright 처럼 서버 프로세스에 상태(열린 페이지)를 두는 서버가 호출 사이에 초기화되지 않게 한다."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self.servers: list[McpServer] = []
        self._locks: dict[str, asyncio.Lock] = {}   # 서버별 연결·해제 직렬화 (host 루프에서만 만든다)

    def lock(self, server: McpServer) -> asyncio.Lock:
        return self._locks.setdefault(server.name, asyncio.Lock())

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                threading.Thread(target=self._loop.run_forever, name="koda-mcp", daemon=True).start()
                atexit.register(self.close_all)
        return self._loop

    async def run(self, coro):
        """다른 루프에서 host 루프의 코루틴을 기다린다."""
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self.loop))

    def close_all(self) -> None:
        if self._loop is None:
            return
        for s in list(self.servers):
            try:
                asyncio.run_coroutine_threadsafe(_close(s, force=True), self._loop).result(timeout=5)
            except Exception:
                pass


_host = _Host()


def _hop(tool):
    """도구 코루틴을 host 루프로 넘긴다. 세션 스트림이 그 루프에 묶여 있어 턴의 루프에서 직접 기다리면 안 된다."""
    orig = tool.coroutine

    async def call(**kw):
        return await _host.run(orig(**kw))

    tool.coroutine = call
    return tool


async def _serve(server: McpServer, ready: asyncio.Future, stop: asyncio.Event, timeout: float) -> None:
    """세션을 열고 stop 까지 붙든다. anyio cancel scope 는 연 태스크에서 닫아야 하므로 열기와 닫기가 한 태스크에 있다."""
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.tools import load_mcp_tools as adapter_tools
    try:
        async with MultiServerMCPClient({server.name: server.conn}).session(server.name) as session:
            tools = await asyncio.wait_for(adapter_tools(session, server_name=server.name), timeout)
            ready.set_result([_hop(t) for t in tools])
            await stop.wait()
    except BaseException as e:
        if not ready.done():
            ready.set_exception(e if isinstance(e, Exception) else RuntimeError(type(e).__name__))


async def _open(server: McpServer, timeout: float) -> None:
    """세션을 (다시) 열고 server.tools 를 채운다. 같은 서버의 열기·닫기는 락으로 순서를 지킨다."""
    if server.error.startswith("환경변수 미설정"):
        return   # 설정이 완성되지 않은 서버는 연결을 시도하지 않는다
    async with _host.lock(server):
        await _close_unlocked(server)
        loop = asyncio.get_running_loop()
        ready, stop = loop.create_future(), asyncio.Event()
        task = loop.create_task(_serve(server, ready, stop, timeout))
        try:
            server.tools = await asyncio.wait_for(asyncio.shield(ready), timeout)
        except BaseException:
            stop.set()
            task.cancel()
            raise
        server._stop, server._task = stop, task
        if server not in _host.servers:
            _host.servers.append(server)


async def _close(server: McpServer, force: bool = False) -> None:
    """해제. 락을 기다리는 사이 사용자가 다시 켰으면(enabled) 닫지 않는다. 열기·닫기 워커가 겹쳐 들어와도 마지막 의도가 남는다."""
    async with _host.lock(server):
        if server.enabled and not force:
            return
        await _close_unlocked(server)


async def _close_unlocked(server: McpServer) -> None:
    stop, task = server._stop, server._task
    server._stop = server._task = None
    server.tools = []
    if stop is not None:
        stop.set()
    if task is not None:
        try:
            await asyncio.wait_for(task, 5)
        except Exception:
            pass
    if server in _host.servers:
        _host.servers.remove(server)


def _describe(e: BaseException) -> str:
    """ExceptionGroup(anyio TaskGroup)은 안쪽 원인으로 풀어 보인다."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return f"{type(e).__name__}: {e}"[:200]


async def connect_server(server: McpServer, timeout: float = 20.0) -> McpServer:
    """서버 하나에 붙어 세션을 열어 두고 도구 목록을 받는다. 실패는 error 에 남기고 예외를 내지 않는다."""
    try:
        import langchain_mcp_adapters  # noqa: F401  선택적 의존성 확인
    except ImportError as e:
        raise RuntimeError("MCP 도구를 쓰려면 `uv add langchain-mcp-adapters` 가 필요합니다.") from e
    try:
        await _host.run(_open(server, timeout))
        server.error = ""
    except Exception as e:  # 서버 하나가 죽어도 나머지는 계속
        server.error = _describe(e)
    return server


async def disconnect_server(server: McpServer) -> McpServer:
    """세션을 닫는다(stdio 면 서버 프로세스도 끝난다). /mcp 에서 끌 때 쓴다."""
    await _host.run(_close(server))
    return server


def close_mcp() -> None:
    """열어 둔 MCP 세션을 모두 닫는다. 프로세스가 끝나기 전(실행기 스레드가 살아 있을 때) 부른다."""
    _host.close_all()


async def connect_mcp(dirs, timeout: float = 20.0, cwd: str | Path | None = None) -> list[McpServer]:
    """설정된 모든 서버에 붙는다(서버별 격리·타임아웃). TUI 의 /mcp 화면과 상태줄이 이 목록을 쓴다."""
    servers = mcp_servers(dirs, cwd)
    for s in servers:
        await connect_server(s, timeout)
    return servers


async def load_mcp_tools(dirs, timeout: float = 20.0, on_log=None, cwd: str | Path | None = None) -> list:
    """MCP 설정 폴더(들)로 외부 도구를 로드한다. 서버별 격리 + 타임아웃 + graceful skip."""
    tools: list = []
    for s in await connect_mcp(dirs, timeout, cwd):
        tools += s.tools
        if on_log:
            on_log(f"MCP '{s.name}': 도구 {len(s.tools)}개" if not s.error else f"MCP '{s.name}' 로드 실패(건너뜀): {s.error.split(':')[0]}")
    return tools
