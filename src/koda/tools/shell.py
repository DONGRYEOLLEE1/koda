"""셸 실행 도구. 위험 작업 — Permission middleware 가 실행 전 게이트한다.

용도: 테스트 실행·빌드 등 명령 실행. 되돌릴 수 없는 변경이 가능하므로 승인 대상.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from langchain.tools import tool

from ..workspace import Workspace

# 실행 전 승인이 필요한 도구 이름 (Permission middleware 가 참조)
DANGEROUS_TOOLS = {"workspace_run_command"}

_no_sandbox_warned = False


def _warn_no_sandbox_once():
    global _no_sandbox_warned
    if not _no_sandbox_warned:
        _no_sandbox_warned = True
        import sys
        sys.stderr.write("  · OS 샌드박스(bubblewrap) 비활성 — 워크스페이스 밖 쓰기는 denygate 패턴 방어만 적용됨\n")


def _bwrap_prefix(ws: Workspace) -> list[str]:
    """bubblewrap 가용 시 명령을 OS 격리로 감싸는 argv 접두 (ROADMAP §3.4).

    프로필: 루트 전체 read-only, 워크스페이스만 쓰기, /tmp 는 tmpfs, 새 세션.
    ~/.cache 는 쓰기 허용(uv/pip 캐시 — 의존성 설치 흐름을 깨지 않기 위함), 네트워크 허용.
    denygate(§2.5)가 정책 게이트, 이것은 그 아래의 OS 경계다.
    미설치 또는 BH_SANDBOX=off 면 빈 접두(비활성 경로 — 기존과 동일하게 동작).
    """
    if os.environ.get("BH_SANDBOX") == "off" or not shutil.which("bwrap"):
        _warn_no_sandbox_once()  # 결정론 보장의 실제 경계가 bwrap 설치 여부임을 알린다 (리뷰 M1)
        return []
    prefix = [
        "bwrap",
        "--ro-bind", "/", "/",
        "--bind", str(ws.root), str(ws.root),
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--die-with-parent",
        "--new-session",
    ]
    cache = Path.home() / ".cache"
    if cache.is_dir():
        prefix += ["--bind", str(cache), str(cache)]
    return prefix + ["--"]


def _workspace_env(ws: Workspace) -> dict:
    """워크스페이스에 자체 .venv 가 있으면 그 환경을 우선하는 env 를 만든다.

    없으면 상속 env 그대로. 이게 없으면 `python` 이 koda(부모) venv 로 해석되어, 워크스페이스 의존성과 무관한 환경에서 테스트가 돌아 **거짓 성공 증거**가 된다(실측: workspace 는 langchain 0.3.0 인데 부모 venv 1.x 로 import 테스트가 통과해 검증을 오염).
    """
    env = os.environ.copy()
    venv_bin = ws.root / ".venv" / "bin"
    if venv_bin.is_dir():
        env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
        env["VIRTUAL_ENV"] = str(ws.root / ".venv")
    return env


def make_shell_tools(ws: Workspace) -> list:
    """워크스페이스에 바인딩된 셸 도구 목록 (위험)."""

    @tool
    def workspace_run_command(argv: list[str], timeout: int = 60) -> str:
        """워크스페이스에서 명령을 실행한다(argv: 인자 리스트, 예: ["pytest","-q"]).

        위험 작업이므로 실행 전 사용자 승인이 필요하다.
        """
        try:
            proc = subprocess.run(
                _bwrap_prefix(ws) + argv, cwd=str(ws.root), capture_output=True, text=True,
                timeout=timeout, env=_workspace_env(ws),
            )
        except FileNotFoundError:  # 없는 명령이 실행 전체를 죽이지 않게 — 에이전트가 대안을 시도하도록
            return (
                f"오류: 명령을 찾을 수 없음: {argv[0]!r}. 이 환경에 설치되어 있지 않다. "
                "다른 방법을 시도하라 (예: pip 대신 ['python','-m','pip',...], 또는 해당 단계 생략 후 보고)."
            )
        except subprocess.TimeoutExpired:
            return f"오류: 시간 초과({timeout}s): {' '.join(argv)}. 더 작은 범위의 명령으로 다시 시도하라."
        return (
            f"exit_code={proc.returncode}\n"
            f"stdout:\n{proc.stdout[-4000:]}\n"
            f"stderr:\n{proc.stderr[-2000:]}"
        )

    return [workspace_run_command]
