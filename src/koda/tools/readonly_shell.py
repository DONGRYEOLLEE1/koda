"""조사용 셸 도구 (investigator·planner). LLM 이 안전한 명령을 낸다고 가정하고 자유 실행하되,
denygate 재앙-플로어만 하드 차단한다 — TOOL_CALLING_REFACTOR §4(분류기 폐기).

"이 셸 문자열이 진짜 read-only 인가"를 정적으로 증명하려던 분류기는 원리적으로 불건전·무한이라
(agy 우회 다수) 폐기했다. 건전한(sound) 보안 경계는 **승인(사람)** 또는 **샌드박스(OS)** 뿐이고,
정적 파서는 둘 중 어느 것도 아니다. 그래서 여기서는:
- **유한한 재앙 deny-list(`denygate`)만 집행** — rm -rf·sudo·자격증명(.ssh/id_rsa/.env) 접근·
  워크스페이스 밖 쓰기·원격스크립트 실행 등. 이건 작고 유지 가능한 sound 한 하드 차단이다.
- 그 외는 **LLM 을 신뢰해 `bash -c` 로 자유 실행**(파이프·글롭·순차 조합).
SAFE_TOOLS(자동 실행)이므로 파일을 바꾸는 명령은 넣지 말라고 프롬프트로 지시하되, 실수의 재앙만
denygate 가 막는다. 신뢰 불가 저장소를 열 땐 bwrap read-only 샌드박스(§4-B) 권고(있으면 자동 적용).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

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


def _readonly_bwrap_prefix(ws: Workspace) -> list[str] | None:
    """있으면 read-only 심층방어 샌드박스(§4-B): 워크스페이스만 ro-bind + 필요한 시스템 디렉터리.
    호스트 루트 전체는 노출하지 않는다. 부재/off 면 None."""
    if os.environ.get("BH_SANDBOX") == "off" or not shutil.which("bwrap"):
        return None
    prefix = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
              "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for d in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        if os.path.isdir(d):
            prefix += ["--ro-bind", d, d]
    for f in ("/etc/passwd", "/etc/group", "/etc/nsswitch.conf", "/etc/ld.so.cache",
              "/etc/ld.so.conf", "/etc/localtime"):
        if os.path.exists(f):
            prefix += ["--ro-bind", f, f]
    if os.path.isdir("/etc/ld.so.conf.d"):
        prefix += ["--ro-bind", "/etc/ld.so.conf.d", "/etc/ld.so.conf.d"]
    prefix += ["--ro-bind", str(ws.root), str(ws.root), "--chdir", str(ws.root), "--"]
    return prefix


def _readonly_env(ws: Workspace) -> dict:
    """최소 env. HOME 을 워크스페이스로 고정해 `~` 확장이 실제 홈으로 새지 않게 한다."""
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "HOME": str(ws.root),
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
        hit = check_command(command)  # 유한한 재앙 deny-list 만 하드 차단(§4)
        if hit is not None:
            return f"거부됨(안전 차단): {hit[1]}. 이 명령은 조사 경로에서 실행할 수 없다."
        prefix = _readonly_bwrap_prefix(ws)
        if prefix is None:
            _warn_no_sandbox_once()
            prefix = []
        try:
            proc = subprocess.run(
                prefix + ["bash", "-c", command], cwd=str(ws.root), capture_output=True, text=True,
                timeout=timeout, env=_readonly_env(ws), stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return f"오류: 시간 초과({timeout}s). 더 좁은 범위로 다시 시도하라."
        except Exception as e:  # 어떤 예외도 그래프로 흘리지 않는다(ToolNode re-raise 크래시 방지)
            return f"오류: 명령 실행 실패({type(e).__name__})."
        return _fmt_output(proc.returncode, proc.stdout, proc.stderr)

    return [run_readonly_command]
