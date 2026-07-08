"""읽기 전용 셸 명령 도구 (investigator·planner 용) — AGENT_ROSTER §5.

이 도구는 `SAFE_TOOLS`(승인 없이 자동 허용)라 **이 파일의 validator 가 유일·필수 보안 경계**다.
안전 불변식 = 쓰기 방지 + 읽기 유출 방지 둘 다. read-only 명령이라도 워크스페이스 밖 비밀
(/etc/shadow, ~/.ssh/id_rsa)을 읽어 유출할 수 있으므로 읽기 범위도 워크스페이스로 가둔다.

경계 구조 (사용자 확정 §5.2 정밀화):
- 1차(필수): 3-레이어 validator — (1) 명령명 allowlist (2) 명령별 옵션 whitelist (3) 명령별
  positional 경로 스키마 + realpath 워크스페이스 confine. **argv 전용**(셸 문자열 아님) → 파이프·
  리다이렉트·치환·명령 체이닝이 구조적으로 불가.
- 2차(있으면): bwrap read-only 샌드박스(호스트 루트 전체 노출 안 함 §5.1). 부재 시 validator-only
  로 강등(거부 아님).
- fail-closed = validator 위반(allowlist 밖·옵션 위반·경로 탈출)이면 **실행하지 않고 에러 문자열
  반환**. langgraph ToolNode 는 예외를 re-raise 해 그래프를 죽이므로(§5.2) 절대 raise 하지 않는다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from langchain.tools import tool

from ..workspace import Workspace

# (1) 명령명 allowlist. git·pytest·임의 스크립트 실행은 1차 제외(§5.4 → 필요하면 edit 경로).
# (2) 명령별 옵션 문법(whitelist). 여기 없는 옵션은 전부 거부 — 특히 find -exec/-delete/-fprint*,
#     grep -R(심링크 추적), find -L, ls -L, --config/--output 은 자동 거부된다.
#   short : 값 없는 단문자 플래그(클러스터 결합 가능, 예 ls -la)
#   sval  : 값을 받는 단문자 플래그(-n 20 또는 -n20)
#   numeric: head/tail 의 구식 숫자 플래그(-20) 허용
#   word  : 값 없는 통짜 옵션(find -print, tree -d 등)
#   wval  : 값을 받는 통짜 옵션(다음 토큰=값, 경로 아님)
#   long  : grep 의 --옵션, lval = 그중 값을 받는 것
#   first_pattern: 첫 positional 이 경로가 아니라 패턴(grep PATTERN [path...])
_GRAMMAR: dict[str, dict] = {
    "pwd":  {},
    "ls":   {"short": "lahtrSdpFi1R"},
    "wc":   {"short": "lwcm"},
    "cat":  {"short": "nbAETs"},
    "head": {"sval": "nc", "numeric": True},
    "tail": {"sval": "nc", "numeric": True},
    "grep": {"short": "rnilcvwxEFoHhsq", "sval": "ABCm",
             "long": {"--include", "--exclude", "--exclude-dir", "--color"},
             "lval": {"--include", "--exclude", "--exclude-dir"}, "first_pattern": True},
    "find": {"word": {"-print"},
             "wval": {"-name", "-iname", "-type", "-maxdepth", "-mindepth", "-path", "-ipath"}},
    "tree": {"word": {"-d", "-a", "-f", "--dirsfirst"}, "wval": {"-L", "-I"}},
}


def _bad_opt(cmd: str, tok: str) -> str:
    return f"허용되지 않은 옵션: {tok} ({cmd})"


def _check_opt(cmd: str, g: dict, tok: str) -> tuple[int, str | None]:
    """옵션 토큰 하나를 검증한다. 반환 (다음 토큰을 값으로 소비하면 1 아니면 0, 에러 or None)."""
    if g.get("numeric") and len(tok) > 1 and tok[1:].isdigit():  # head -20
        return 0, None
    if tok.startswith("--"):
        base = tok.split("=", 1)[0]
        if base in g.get("long", set()):
            return (1 if base in g.get("lval", set()) and "=" not in tok else 0), None
        return 0, _bad_opt(cmd, tok)
    if tok in g.get("word", set()):
        return 0, None
    if tok in g.get("wval", set()):
        return 1, None
    short, sval = g.get("short", ""), g.get("sval", "")
    if short or sval:  # 단문자 클러스터 (ls -la, grep -rn, head -n20 ...)
        chars = tok[1:]
        j = 0
        while j < len(chars):
            c = chars[j]
            if c in short:
                j += 1
                continue
            if c in sval:  # 값: 클러스터에 붙어있으면 소비, 아니면 다음 토큰
                return (0 if j + 1 < len(chars) else 1), None
            return 0, _bad_opt(cmd, tok)
        return 0, None
    return 0, _bad_opt(cmd, tok)


def _positional_paths(cmd: str, positional: list[str]) -> list[str]:
    """positional 토큰 중 **경로로 판정되는 것**만 반환(패턴/값은 realpath 검증 제외 §5.3)."""
    if cmd == "pwd":
        return []
    if _GRAMMAR[cmd].get("first_pattern"):  # grep: 첫 positional = PATTERN
        return positional[1:]
    return positional  # ls/tree/find/wc/cat/head/tail: positional 전부 경로


def _path_ok(ws: Workspace, tok: str) -> tuple[bool, str | None]:
    """경로 인자가 워크스페이스 안(realpath 기준)인지 검사. 절대경로·`..`·심링크 탈출을 여기서
    차단하고, `.koda`(koda 내부 저장소) 접근도 막는다(reader 와 동일 불변식)."""
    root = str(ws.root)  # Workspace.__init__ 이 이미 resolve() 함
    real = os.path.realpath(os.path.join(root, tok))  # tok 이 절대경로면 join 이 그대로 채택
    if real != root and not real.startswith(root + os.sep):
        return False, f"경로가 워크스페이스 밖입니다: {tok}"
    koda = os.path.join(root, ".koda")
    if real == koda or real.startswith(koda + os.sep):
        return False, "'.koda' 는 koda 내부 저장소다 — read_artifact 로 조회하라"
    return True, None


def _validate(argv: list[str], ws: Workspace) -> str | None:
    """3-레이어 validator. 위반 시 에러 문자열, 통과 시 None."""
    if not argv:
        return "빈 명령"
    if any("\x00" in a for a in argv):  # 널바이트는 realpath/subprocess 에서 예외 → 사전 거부(§5.2)
        return "인자에 널바이트(\\x00)를 포함할 수 없다"
    cmd = argv[0]
    if cmd not in _GRAMMAR:  # (1) 명령명 allowlist
        return f"허용되지 않은 명령: {cmd!r}. 허용: {', '.join(sorted(_GRAMMAR))}"
    g = _GRAMMAR[cmd]
    positional: list[str] = []
    i, rest = 0, argv[1:]
    while i < len(rest):  # (2) 옵션 whitelist + positional 수집
        tok = rest[i]
        if tok == "-" or not tok.startswith("-"):
            positional.append(tok)
            i += 1
            continue
        consume, err = _check_opt(cmd, g, tok)
        if err:
            return err
        i += 1 + consume
    for p in _positional_paths(cmd, positional):  # (3) 경로 realpath confine
        ok, why = _path_ok(ws, p)
        if not ok:
            return why
    return None


_no_sandbox_warned = False


def _warn_no_sandbox_once() -> None:
    global _no_sandbox_warned
    if not _no_sandbox_warned:
        _no_sandbox_warned = True
        sys.stderr.write("  · read-only 명령 OS 샌드박스(bwrap) 비활성 — validator 만으로 격리\n")


def _readonly_bwrap_prefix(ws: Workspace) -> list[str] | None:
    """read-only 심층방어 샌드박스(§5.1). 호스트 루트 전체(`--ro-bind / /`)를 노출하지 않고,
    실행에 필요한 시스템 디렉터리만 개별 ro-bind + 워크스페이스만 ro-bind. 부재/off 면 None."""
    if os.environ.get("BH_SANDBOX") == "off" or not shutil.which("bwrap"):
        return None
    prefix = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
              "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for d in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        if os.path.isdir(d):
            prefix += ["--ro-bind", d, d]
    for f in ("/etc/passwd", "/etc/group", "/etc/nsswitch.conf", "/etc/ld.so.cache",
              "/etc/ld.so.conf", "/etc/localtime"):  # /etc 전체가 아니라 필요한 파일만
        if os.path.exists(f):
            prefix += ["--ro-bind", f, f]
    if os.path.isdir("/etc/ld.so.conf.d"):
        prefix += ["--ro-bind", "/etc/ld.so.conf.d", "/etc/ld.so.conf.d"]
    prefix += ["--ro-bind", str(ws.root), str(ws.root), "--chdir", str(ws.root), "--"]
    return prefix


def _readonly_env() -> dict:
    """최소 env — 부모 프로세스의 시크릿(토큰 등)을 자식 명령에 넘기지 않는다."""
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "HOME": "/tmp",  # ~ 가 실제 홈으로 풀리지 않게 (경로 validator 와 이중 방어)
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def make_readonly_shell_tools(ws: Workspace) -> list:
    """읽기 전용 셸 명령 도구 (investigator·planner). validator 가 유일 보안 경계."""

    @tool
    def workspace_run_readonly_command(argv: list[str], timeout: int = 60) -> str:
        """읽기 전용 셸 명령을 실행한다(argv 리스트, 예: ["ls","-al"], ["tree","-L","2","src"]).

        허용 명령: pwd, ls, tree, find, wc, cat, grep, head, tail (읽기 전용).
        먼저 `ls`·`tree -L 2` 로 구조를 파악한 뒤 대상 폴더만 좁혀라 — 맹목적 전체 재귀(`find`
        무제한)는 지양하고, 큰 출력은 명령 자체 옵션으로 줄여라(`find -maxdepth`, `head -n`,
        `grep -m`). **파이프·리다이렉트·명령 체이닝은 쓸 수 없다**(argv 리스트 전용, 셸 문자열
        아님). 경로는 워크스페이스 밖으로 못 나간다 — 위반하면 오류 문자열이 돌아오니 교정하라.
        파일을 실제로 바꾸거나 테스트 등 명령을 실행하려면 이 도구로는 안 되고 편집 경로가 필요하다.

        Args:
            argv: 명령과 인자 리스트 (셸 문자열 아님). 예 ["grep","-rn","TODO","src"]
            timeout: 초 단위 실행 상한
        """
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            return '오류: argv 는 문자열 리스트여야 한다 (예: ["ls","-al"]).'
        err = _validate(argv, ws)  # fail-closed: 위반이면 실행하지 않고 문자열로 반환
        if err:
            return f"거부됨(read-only 정책): {err}"
        run_argv = list(argv)
        if run_argv[0] == "grep":  # 재귀 grep 이 내부 저장소로 다이빙하지 않게(reader 와 동일)
            run_argv[1:1] = ["--exclude-dir=.koda", "--exclude-dir=.git", "--exclude-dir=.venv"]
        elif run_argv[0] == "find":  # 재귀 find 가 .koda 내용(세션·artifact 이름)을 열거하지 않게 — grep 과 parity
            for d in (".koda", ".git", ".venv"):
                run_argv += ["-not", "-path", f"*/{d}/*"]
        prefix = _readonly_bwrap_prefix(ws)
        if prefix is None:  # bwrap 부재 → validator-only 로 강등(거부 아님, §5.2 정밀화)
            _warn_no_sandbox_once()
            prefix = []
        try:
            proc = subprocess.run(
                prefix + run_argv, cwd=str(ws.root), capture_output=True, text=True,
                timeout=timeout, env=_readonly_env(), stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:  # 명령 바이너리 부재 — 실행 전체를 죽이지 않게 문자열로
            return f"오류: 명령을 찾을 수 없음: {argv[0]!r}."
        except subprocess.TimeoutExpired:
            return f"오류: 시간 초과({timeout}s). 더 좁은 범위로 다시 시도하라."
        except Exception as e:  # 어떤 예외도 그래프로 흘리지 않는다 — ToolNode re-raise 크래시 방지(§5.2)
            return f"오류: 명령 실행 실패({type(e).__name__})."
        return (
            f"exit_code={proc.returncode}\n"
            f"stdout:\n{proc.stdout[-6000:]}\n"
            f"stderr:\n{proc.stderr[-1000:]}"
        )

    return [workspace_run_readonly_command]
