from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from langchain.tools import tool

from ..denygate import check_command
from ..workspace import Workspace
from .shell import _fmt_output

_no_sandbox_warned = False


def _warn_no_sandbox_once() -> None:
    global _no_sandbox_warned
    if not _no_sandbox_warned:
        _no_sandbox_warned = True
        sys.stderr.write("  · 조사 셸 OS 샌드박스(bwrap) 비활성 — denygate 재앙-플로어만 적용\n")


# PATH 가 통째로 비어 있는 환경에서만 쓰는 최후 폴백. 평시에는 사용자 PATH 를 그대로 계승한다.
_FALLBACK_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
# bwrap 안에 항상 들여야 하는 시스템 디렉터리 (PATH 계승분은 여기에 더해서 계산한다)
_BASE_RO_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib64")


def _readonly_path() -> str:
    """조사 셸이 볼 PATH. 사용자 환경을 그대로 계승한다."""
    return os.environ.get("PATH") or _FALLBACK_PATH


def _readonly_home(ws: Workspace) -> str:
    """조사 셸이 볼 HOME. 실제 홈을 계승하되, 홈을 정할 수 없는 환경에서는 워크스페이스로 물러선다."""
    try:
        return str(Path.home())
    except (RuntimeError, OSError):
        return str(ws.root)


def _path_dirs() -> list[str]:
    """PATH 에 실린 디렉터리 목록. bwrap 바인드 대상을 뽑는 데 쓴다."""
    return [d for d in _readonly_path().split(os.pathsep) if d]


def _ro_binds(paths) -> list[str]:
    """실재하는 디렉터리만 골라 --ro-bind 인자열로 만든다. 이미 들인 경로의 하위는 건너뛰고(중복 마운트
    방지), 심볼릭 링크는 정규화하지 않는다 — /bin 이 /usr/bin 의 링크인 배포판에서 /bin 자체가
    컨테이너에서 사라져 `#!/bin/sh` 스크립트가 깨지기 때문이다."""
    bound: list[str] = []
    args: list[str] = []
    for p in paths:
        if not p or not os.path.isdir(p):
            continue
        p = os.path.normpath(p)
        if any(p == b or p.startswith(b + os.sep) for b in bound):
            continue
        bound.append(p)
        args += ["--ro-bind", p, p]
    return args


def _readonly_bwrap_prefix(ws: Workspace) -> list[str] | None:
    """있으면 read-only 심층방어 샌드박스: 호스트 루트는 노출하지 않고 필요한 경로만 읽기 전용으로 들인다. 부재/off 면 None."""
    if os.environ.get("BH_SANDBOX") == "off" or not shutil.which("bwrap"):
        return None
    prefix = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
              "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    # PATH 의 디렉터리와 홈도 들인다 — env 에서 PATH 를 계승해도 여기서 바인드하지 않으면
    # /opt·~/.local/bin·nvm 경로의 바이너리는 여전히 command not found 다. 읽기 전용이라 쓰기는 막힌다.
    prefix += _ro_binds([*_BASE_RO_DIRS, *_path_dirs(), _readonly_home(ws), str(ws.root)])
    for f in ("/etc/passwd", "/etc/group", "/etc/nsswitch.conf", "/etc/ld.so.cache",
              "/etc/ld.so.conf", "/etc/localtime"):
        if os.path.exists(f):
            prefix += ["--ro-bind", f, f]
    if os.path.isdir("/etc/ld.so.conf.d"):
        prefix += ["--ro-bind", "/etc/ld.so.conf.d", "/etc/ld.so.conf.d"]
    prefix += ["--chdir", str(ws.root), "--"]
    return prefix


def _readonly_env(ws: Workspace) -> dict:
    """조사 셸의 env. PATH 와 HOME 은 사용자 환경을 그대로 계승한다.

    이 둘을 좁히면 조사 경로가 사용자와 다른 환경을 관측하고, 모델은 그 관측을 정직하게 해석해
    사실과 다른 결론을 보고한다 — 설치된 gh·uv·node 를 '없음' 으로, 로그인된 계정을 '로그인 안 됨'
    으로 답했다(#96). 축소는 안전 경계도 아니다: 절대경로로 부르면 그대로 실행되고, 실제 차단은
    denygate(자격증명 경로는 confirm 등급까지 하드 차단)와 bwrap 읽기 전용 바인드가 맡는다.
    """
    return {
        "PATH": _readonly_path(),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "HOME": _readonly_home(ws),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def make_readonly_shell_tools(ws: Workspace) -> list:
    """조사용 셸 도구 (investigator·planner). denygate 재앙-플로어 + 자유 실행."""

    @tool
    def run_readonly_command(command: str, timeout: int = 60) -> str:
        """조사용 셸 명령을 실행한다(`bash -c`, 파이프·글롭·순차 조합 가능).

        **파일을 하나씩 반복해서 읽지 마라 — 파이프·글롭으로 한 콜에 훑어라.** 예:
          - 구조: `find tests -type f | sort` / `ls -al tests` / `tree -L 2 src`
          - 일괄 발췌: `head -n 40 tests/*.py`
          - 검색: `grep -rn "def test_" tests | head -50`
        이 도구는 승인 없이 자동 실행되니 **파일을 바꾸거나 테스트를 실행하는 명령은 넣지 마라**
        (그건 편집 경로가 필요하다). 재앙적 명령(rm -rf·sudo·자격증명 접근 등)은 자동 차단된다.

        Args:
            command: 한 줄 셸 명령(파이프·글롭 허용). 예 `grep -rn TODO src | head`
            timeout: 초 단위 실행 상한
        """
        if not isinstance(command, str):
            return "오류: command 는 문자열이어야 한다."
        hit = check_command(command)  # 유한한 재앙 deny-list 만 하드 차단
        if hit is not None:
            return f"거부됨(안전 차단): {hit[1]}. 이 명령은 조사 경로에서 실행할 수 없다."
        prefix = _readonly_bwrap_prefix(ws)
        if prefix is None:
            _warn_no_sandbox_once()
            prefix = []
        try:
            proc = subprocess.run(
                prefix + ["bash", "-o", "pipefail", "-c", command], cwd=str(ws.root), capture_output=True, text=True,
                timeout=timeout, env=_readonly_env(ws), stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return f"오류: 시간 초과({timeout}s). 더 좁은 범위로 다시 시도하라."
        except Exception as e:  # 어떤 예외도 그래프로 흘리지 않는다(ToolNode re-raise 크래시 방지)
            return f"오류: 명령 실행 실패({type(e).__name__})."
        return _fmt_output(proc.returncode, proc.stdout, proc.stderr)

    return [run_readonly_command]
