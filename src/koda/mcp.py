from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path


def _normalize(spec: dict) -> dict:
    """설정 축약형에 transport 를 채운다 (url→streamable_http, command→stdio)."""
    spec = dict(spec)
    if "transport" not in spec:
        if "url" in spec:
            spec["transport"] = "streamable_http"
        elif "command" in spec:
            spec["transport"] = "stdio"
    return spec


def _global_dir() -> Path | None:
    """전역 MCP 폴더 ~/.config/koda/mcps — .env 탐색과 같은 위치. 홈 미정의 환경이면 None."""
    try:
        return Path.home() / ".config" / "koda" / "mcps"
    except (RuntimeError, OSError):
        return None


def mcp_dirs(workspace: str | Path, mcps_dir: str | None = None) -> list[Path]:
    """로드 순서: 전역(~/.config/koda/mcps) → 프로젝트(<workspace>/mcps 또는 --mcps-dir). 같은 서버명은 뒤(프로젝트)가 이긴다."""
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
            conns[name] = _normalize(spec)
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

    @property
    def transport(self) -> str:
        return {"streamable_http": "http", "stdio": "stdio"}.get(self.conn.get("transport", ""), self.conn.get("transport", "?"))

    @property
    def target(self) -> str:
        """표시용 대상. http 는 URL, stdio 는 명령줄."""
        if self.conn.get("url"):
            return str(self.conn["url"])
        return " ".join([str(self.conn.get("command", "")), *map(str, self.conn.get("args") or [])]).strip()

    @property
    def active_tools(self) -> list:
        return self.tools if self.enabled and not self.error else []


def mcp_servers(dirs) -> list[McpServer]:
    """폴더들을 순서대로 읽어 서버 목록을 만든다. 같은 이름은 뒤(프로젝트)가 이긴다."""
    if isinstance(dirs, (str, Path)):
        dirs = [dirs]
    g = _global_dir()
    servers: dict[str, McpServer] = {}
    for d in dirs:
        d = Path(d)
        source = "전역" if g and d.resolve() == g.resolve() else "프로젝트"
        for name, conn in _read_dir(d).items():
            servers[name] = McpServer(name, source, conn)
    return list(servers.values())


async def connect_server(server: McpServer, timeout: float = 20.0) -> McpServer:
    """서버 하나에 붙어 도구 목록을 받는다. 실패는 error 에 남기고 예외를 내지 않는다."""
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as e:  # 선택적 의존성
        raise RuntimeError("MCP 도구를 쓰려면 `uv add langchain-mcp-adapters` 가 필요합니다.") from e
    try:
        client = MultiServerMCPClient({server.name: server.conn})
        server.tools = await asyncio.wait_for(client.get_tools(), timeout=timeout)
        server.error = ""
    except Exception as e:  # 서버 하나가 죽어도 나머지는 계속
        server.tools, server.error = [], f"{type(e).__name__}: {e}"[:200]
    return server


async def connect_mcp(dirs, timeout: float = 20.0) -> list[McpServer]:
    """설정된 모든 서버에 붙는다(서버별 격리·타임아웃). TUI 의 /mcp 화면과 상태줄이 이 목록을 쓴다."""
    servers = mcp_servers(dirs)
    for s in servers:
        await connect_server(s, timeout)
    return servers


async def load_mcp_tools(dirs, timeout: float = 20.0, on_log=None) -> list:
    """MCP 설정 폴더(들)로 외부 도구를 로드한다. 서버별 격리 + 타임아웃 + graceful skip."""
    tools: list = []
    for s in await connect_mcp(dirs, timeout):
        tools += s.tools
        if on_log:
            on_log(f"MCP '{s.name}': 도구 {len(s.tools)}개" if not s.error else f"MCP '{s.name}' 로드 실패(건너뜀): {s.error.split(':')[0]}")
    return tools
