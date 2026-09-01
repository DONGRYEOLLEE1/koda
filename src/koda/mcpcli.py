from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path
from urllib.parse import urlparse

from .mcp import (
    close_mcp,
    config_path,
    connect_server,
    find_config,
    load_mcp_configs,
    mcp_dirs,
    mcp_servers,
    remove_config,
    write_config,
)


def _err(msg: str) -> None:
    import sys

    sys.stderr.write(msg + "\n")


def _name_from_url(url: str) -> str:
    """URL 에서 서버 이름을 뽑는다. mcp.exa.ai → exa, api.githubcopilot.com → githubcopilot."""
    host = (urlparse(url).hostname or "").split(":")[0]
    parts = [p for p in host.split(".") if p not in ("mcp", "api", "www", "com", "net", "org", "io", "ai", "dev", "app", "co")]
    return (parts[-1] if parts else host) or "server"


_LAUNCHERS = {"npx", "uvx", "npm", "pnpm", "bunx", "yarn", "uv", "docker", "python", "python3", "node", "sh", "bash", "run", "exec", "-y"}
_GENERIC = ("mcp-server-", "mcp-", "server-")   # 패키지명의 관용 접두. 이것만 떼고 접미는 건드리지 않는다


def _name_from_command(command: str) -> str:
    """stdio 명령에서 서버 이름을 뽑는다. 런처·플래그를 건너뛰고 패키지명(또는 브리지 대상 URL)에서 고른다."""
    parts = shlex.split(command)
    for tok in parts:                       # mcp-remote 브리지처럼 URL 이 있으면 그쪽이 더 정확하다
        if tok.startswith(("http://", "https://")):
            return _name_from_url(tok)
    for tok in parts:
        if tok.startswith("-") or tok.lower() in _LAUNCHERS:
            continue
        if tok.startswith("@"):             # @scope/name@ver
            scope, _, rest = tok[1:].partition("/")
            pkg = rest.split("@")[0] or scope
        else:
            pkg = Path(tok.split("@")[0]).stem
            scope = ""
        name = pkg
        for g in _GENERIC:
            if name.startswith(g):
                name = name[len(g):]
        if name in ("", "mcp", "server"):   # 이름이 통째로 관용어면 스코프가 더 알아보기 쉽다
            name = scope or pkg
        return name
    return "server"


def _spec_from_json(text: str, name: str | None = None) -> tuple[str, dict] | None:
    try:
        data = json.loads(text)
    except ValueError as e:
        _err(f"JSON 을 읽지 못했습니다: {e}")
        return None
    servers = (data or {}).get("mcpServers") or data
    if not isinstance(servers, dict) or not servers:
        _err("JSON 에 mcpServers 항목이 없습니다.")
        return None
    if len(servers) > 1 and not name:
        _err(f"서버가 여럿입니다({', '.join(servers)}). 이름을 지정하거나 하나씩 추가하세요.")
        return None
    found, spec = next(iter(servers.items()))
    return (name or found), spec


def spec_from_text(text: str) -> tuple[str, dict] | None:
    """붙여넣은 한 줄을 (이름, 스펙) 으로 바꾼다. JSON 스니펫·원격 URL·실행 명령을 모두 받는다(TUI 의 /mcp a 가 쓴다)."""
    text = text.strip()
    if not text:
        return None
    if text.startswith("{"):
        return _spec_from_json(text)
    if text.startswith(("http://", "https://")):
        return _name_from_url(text), {"url": text}
    return _name_from_command(text), {"command": text}


def _spec_from_args(args) -> tuple[str, dict] | None:
    """명령줄을 서버 스펙으로 바꾼다. --json 은 통째 붙여넣기, --url 은 원격, `--` 뒤는 stdio 명령."""
    if args.json:
        return _spec_from_json(args.json, args.name)
    spec: dict = {}
    if args.url:
        spec["url"] = args.url
        name = args.name or _name_from_url(args.url)
    elif args.command:
        spec["command"] = shlex.join(args.command) if len(args.command) > 1 else args.command[0]
        name = args.name or _name_from_command(spec["command"])
    else:
        _err("추가할 대상이 없습니다. --url, --json 또는 `-- <명령>` 중 하나를 주세요.")
        return None
    for h in args.header or []:
        key, _, value = h.partition(":")
        if not value:
            _err(f"헤더 형식이 잘못됐습니다(‘이름: 값’): {h}")
            return None
        spec.setdefault("headers", {})[key.strip()] = value.strip()
    for e in args.env or []:
        key, _, value = e.partition("=")
        if not key:
            _err(f"환경변수 형식이 잘못됐습니다(KEY=VALUE): {e}")
            return None
        spec.setdefault("env", {})[key.strip()] = value
    return name, spec


def _probe(name: str, spec: dict, workspace: str, timeout: float) -> tuple[bool, str]:
    """저장 전에 실제로 붙여 본다. (성공 여부, 설명)."""
    tmp = Path(workspace) / ".koda" / "mcp-probe"
    tmp.mkdir(parents=True, exist_ok=True)
    probe = tmp / f"{name}.json"
    probe.write_text(json.dumps({"mcpServers": {name: spec}}, ensure_ascii=False), encoding="utf-8")
    try:
        server = mcp_servers([tmp], cwd=workspace)[0]
        if server.error:
            return False, server.error
        asyncio.run(connect_server(server, timeout))
        return (not server.error), (server.error or f"도구 {len(server.tools)}개")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        close_mcp()
        probe.unlink(missing_ok=True)
        try:
            tmp.rmdir()
        except OSError:
            pass


def cmd_add(args) -> int:
    parsed = _spec_from_args(args)
    if parsed is None:
        return 2
    name, spec = parsed
    if not args.no_probe:
        ok, detail = _probe(name, spec, args.workspace, args.timeout)
        print(f"연결 확인: {'성공' if ok else '실패'} · {detail}")
        if not ok and not args.force:
            _err("저장하지 않았습니다. 그래도 저장하려면 --force 를 주세요.")
            return 1
    path = config_path(name, args.scope, args.workspace, args.mcps_dir)
    write_config(path, name, spec)
    print(f"저장됨: {name} → {path}")
    return 0


def cmd_list(args) -> int:
    servers = mcp_servers(mcp_dirs(args.workspace, args.mcps_dir), cwd=args.workspace)
    if not servers:
        print("등록된 MCP 서버가 없습니다. `koda mcp add --help` 를 보세요.")
        return 0
    for s in servers:
        state = "꺼짐" if not s.enabled else ("설정 오류" if s.error else "켜짐")
        print(f"{s.name:<18} {state:<8} {s.source:<5} {s.transport:<6} {s.target[:60]}"
              + (f"  ({s.error[:40]})" if s.error else ""))
    return 0


def cmd_remove(args) -> int:
    path = remove_config(args.name, args.workspace, args.mcps_dir)
    if path is None:
        _err(f"등록되지 않은 서버입니다: {args.name}")
        return 1
    print(f"삭제됨: {args.name} ({path})")
    return 0


def _set_enabled(args, enabled: bool) -> int:
    conns = load_mcp_configs(mcp_dirs(args.workspace, args.mcps_dir))
    if args.name not in conns:
        _err(f"등록되지 않은 서버입니다: {args.name}")
        return 1
    if args.here:   # 이 워크스페이스에서만: 프로젝트에 enabled 한 줄짜리 오버라이드를 둔다
        path = config_path(args.name, "project", args.workspace, args.mcps_dir)
        write_config(path, args.name, {"enabled": enabled})
    else:           # 정의된 파일을 고친다(전역 정의면 모든 워크스페이스에 적용)
        path = find_config(args.name, args.workspace, args.mcps_dir)
        data = json.loads(path.read_text(encoding="utf-8"))
        spec = dict(data["mcpServers"][args.name])
        spec["enabled"] = enabled
        write_config(path, args.name, spec)
    print(f"{'켬' if enabled else '끔'}: {args.name} ({path})")
    return 0


def cmd_enable(args) -> int:
    return _set_enabled(args, True)


def cmd_disable(args) -> int:
    return _set_enabled(args, False)


def register(sub) -> None:
    """`koda mcp ...` 서브커맨드 등록. 문법은 claude·codex 와 맞춰 문서의 명령을 그대로 옮길 수 있게 한다."""
    p = sub.add_parser("mcp", help="MCP 서버 등록·삭제·확인")
    ops = p.add_subparsers(dest="mcp_cmd", required=True)

    def common(sp):
        sp.add_argument("--workspace", default=".")
        sp.add_argument("--mcps-dir", default=None)
        return sp

    a = common(ops.add_parser("add", help="서버 추가 (--url / --json / `-- <명령>`)"))
    a.add_argument("name", nargs="?", help="서버 이름 (생략하면 URL·명령에서 추론)")
    a.add_argument("--url", help="원격 MCP URL")
    a.add_argument("--json", help='설정 스니펫 통째로: \'{"mcpServers":{...}}\'')
    a.add_argument("--header", action="append", metavar="'이름: 값'", help="원격 서버 헤더 (반복 가능)")
    a.add_argument("--env", action="append", metavar="KEY=VALUE", help="stdio 서버 환경변수 (반복 가능)")
    a.add_argument("--scope", choices=("user", "project"), default="user", help="user=~/.koda/mcps (기본), project=<workspace>/mcps")
    a.add_argument("--no-probe", action="store_true", help="저장 전 연결 확인 생략")
    a.add_argument("--force", action="store_true", help="연결 확인에 실패해도 저장")
    a.add_argument("--timeout", type=float, default=30.0)
    # stdio 명령은 `--` 뒤 전부다. 위치인자로 두면 name 과 서로 먹으므로 main() 이 argv 에서 잘라 넣는다.
    a.set_defaults(func=cmd_add, command=[])

    common(ops.add_parser("list", help="등록된 서버 보기")).set_defaults(func=cmd_list)

    r = common(ops.add_parser("remove", help="서버 삭제"))
    r.add_argument("name")
    r.set_defaults(func=cmd_remove)

    for op, fn, help_text in (("enable", cmd_enable, "서버 켜기"), ("disable", cmd_disable, "서버 끄기")):
        sp = common(ops.add_parser(op, help=help_text))
        sp.add_argument("name")
        sp.add_argument("--here", action="store_true", help="이 워크스페이스에서만 적용")
        sp.set_defaults(func=fn)
