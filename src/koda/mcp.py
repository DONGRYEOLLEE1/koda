from __future__ import annotations

import asyncio
import json
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


async def load_mcp_tools(dirs, timeout: float = 20.0, on_log=None) -> list:
    """MCP 설정 폴더(들)로 외부 도구를 로드한다. 서버별 격리 + 타임아웃 + graceful skip."""
    conns = load_mcp_configs(dirs)
    if not conns:
        return []
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as e:  # 선택적 의존성
        raise RuntimeError("MCP 도구를 쓰려면 `uv add langchain-mcp-adapters` 가 필요합니다.") from e

    tools: list = []
    for name, conn in conns.items():
        try:
            client = MultiServerMCPClient({name: conn})
            got = await asyncio.wait_for(client.get_tools(), timeout=timeout)
            tools += got
            if on_log:
                on_log(f"MCP '{name}': 도구 {len(got)}개")
        except Exception as e:  # 서버 하나가 죽어도 나머지는 계속
            if on_log:
                on_log(f"MCP '{name}' 로드 실패(건너뜀): {type(e).__name__}")
    return tools
