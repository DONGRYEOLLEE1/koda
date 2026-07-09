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


def load_mcp_configs(mcps_dir: str = "mcps") -> dict:
    """mcps/*.json|*.yaml 를 읽어 {서버명: connection} dict 로 병합한다."""
    d = Path(mcps_dir)
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


async def load_mcp_tools(mcps_dir: str = "mcps", timeout: float = 20.0, on_log=None) -> list:
    """mcps/ 설정으로 외부 MCP 도구를 로드한다. 서버별 격리 + 타임아웃 + graceful skip."""
    conns = load_mcp_configs(mcps_dir)
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
