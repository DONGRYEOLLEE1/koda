from __future__ import annotations

import atexit
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from langchain.tools import tool

from ..workspace import Workspace

# 실행 전 승인이 필요한 도구 이름 (Permission middleware 가 참조)
DANGEROUS_TOOLS = {"run_command"}

# background 로 띄운 프로세스 그룹들 — koda 정상 종료 시 정리한다. bwrap 이 있으면 --die-with-parent
# 가 정리하지만, bwrap 미설치 환경에선 start_new_session 으로 분리된 프로세스가 남으므로 여기서 죽인다.
_bg_pgids: set[int] = set()


def _reap_background() -> None:
    for pgid in _bg_pgids:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass


atexit.register(_reap_background)

_no_sandbox_warned = False


def _warn_no_sandbox_once():
    global _no_sandbox_warned
    if not _no_sandbox_warned:
        _no_sandbox_warned = True
        import sys
        sys.stderr.write("  · OS 샌드박스(bubblewrap) 비활성 — 워크스페이스 밖 쓰기는 denygate 패턴 방어만 적용됨\n")


def _bwrap_prefix(ws: Workspace) -> list[str]:
    """bubblewrap 가용 시 OS 격리 argv 접두. 루트는 read-only·워크스페이스만 쓰기·~/.cache 는 쓰기 허용. 미설치/BH_SANDBOX=off 면 빈 접두."""
    if os.environ.get("BH_SANDBOX") == "off" or not shutil.which("bwrap"):
        _warn_no_sandbox_once()  # 결정론 보장의 실제 경계가 bwrap 설치 여부임을 알린다
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
    """워크스페이스에 자체 .venv 가 있으면 그 환경을 우선한다 — 없으면 python 이 부모(koda) venv 로 해석돼 거짓 성공 증거가 된다."""
    env = os.environ.copy()
    venv_bin = ws.root / ".venv" / "bin"
    if venv_bin.is_dir():
        env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
        env["VIRTUAL_ENV"] = str(ws.root / ".venv")
    return env


def _fmt_output(returncode: int, stdout: str, stderr: str,
                head: int = 40, tail: int = 40, err: int = 20) -> str:
    """긴 출력을 head+tail 로 발췌하고 생략 줄수를 명시한다 — tail-only 절단의 불투명성을 방지한다."""
    def cut(text: str, h: int, t: int) -> str:
        lines = text.splitlines()
        if len(lines) <= h + t:
            return text
        return "\n".join(lines[:h] + [f"…({len(lines) - h - t}줄 생략)…"] + lines[-t:])
    return (
        f"exit_code={returncode}\n"
        f"stdout:\n{cut(stdout, head, tail)}\n"
        f"stderr:\n{cut(stderr, err, err)}"
    )


def _run_background(ws: Workspace, command: str) -> str:
    """장수명 프로세스(서버·watch)를 detach 실행하고 즉시 반환한다. 출력 파이프를 잡지 않아야
    블록되지 않으므로 stdin 은 끊고 stdout/stderr 는 로그파일로 보낸다. start_new_session 으로
    koda 프로세스 그룹에서 분리하고, pgid 를 기록해 koda 종료 시 정리한다(atexit)."""
    log_dir = ws.root / ".koda" / "run"
    log_dir.mkdir(parents=True, exist_ok=True)
    fd, log_path = tempfile.mkstemp(prefix="cmd-", suffix=".log", dir=str(log_dir))
    os.chmod(log_path, 0o600)  # 로그엔 redaction 전 원문(잠재 시크릿)이 남을 수 있어 소유자 전용으로 제한
    logf = os.fdopen(fd, "wb")
    try:
        proc = subprocess.Popen(
            _bwrap_prefix(ws) + ["bash", "-c", command], cwd=str(ws.root),
            stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True, env=_workspace_env(ws),
        )
    finally:
        logf.close()  # 부모 fd 는 닫는다 — 자식이 dup 복제본을 물고 있어 로그는 계속 쓰인다
    _bg_pgids.add(proc.pid)  # start_new_session 이라 pid == pgid (세션 리더)
    rel = os.path.relpath(log_path, ws.root)
    return (
        f"백그라운드 실행 시작: PID={proc.pid}, 로그={rel}\n"
        f"반환을 기다리지 않는다(koda 종료 시 함께 정리된다). 기동 확인은 포트를 직접 두드려라"
        f"(예: `ss -tlnp | grep :PORT`, `curl -s -o /dev/null -w '%{{http_code}}' URL`). "
        f"실패로 의심되면 `cat {rel}` 로 로그를 확인하라."
    )


def make_shell_tools(ws: Workspace) -> list:
    """워크스페이스에 바인딩된 셸 도구 목록 (위험)."""

    @tool
    def run_command(command: str, timeout: int = 60, background: bool = False) -> str:
        """워크스페이스에서 셸 명령을 실행한다(`bash -c`, 파이프·리다이렉트 사용 가능).

        테스트·린트·타입체크·빌드·진단 실행과 파일 구조 파악(`ls`·`find`·`tree -L 2`)에 쓴다.
        **질의에 맞춘 최소 출력** 명령을 짜라 — 큰 덤프 대신 파이프·플래그로 좁혀라(예:
        `pytest -q 2>&1 | tail -40`, `grep -rn X src | head -50`, `ls -al | head`,
        `find . -maxdepth 2 -name '*.py'`). 위험 작업이므로 실행 전 사용자 승인이 필요하다.

        **끝나지 않는 장수명 프로세스(개발 서버 `uvicorn`·`npm run dev`, `--watch`, 데몬)는 반드시
        `background=True` 로 실행하라.** foreground(기본)로 서버를 띄우면 프로세스가 종료되지 않아
        timeout 까지 블록되고 실패로 오인된다. background 는 즉시 PID·로그경로를 돌려주며, 기동
        여부는 포트를 직접 확인하라(`ss -tlnp`, `curl`). 명령이 끝나는 작업은 background 를 쓰지 마라.
        """
        try:
            if background:
                return _run_background(ws, command)
            proc = subprocess.run(
                _bwrap_prefix(ws) + ["bash", "-c", command], cwd=str(ws.root),
                capture_output=True, text=True, timeout=timeout, env=_workspace_env(ws),
            )
        except FileNotFoundError:  # bash/bwrap 자체 부재 — 실행 전체를 죽이지 않게 오류 문자열로
            return (
                "오류: 셸(bash)을 실행할 수 없다. 환경 구성 문제일 수 있으니 다른 방법을 "
                "시도하거나 해당 단계를 생략하고 보고하라."
            )
        except subprocess.TimeoutExpired:
            return (
                f"오류: 시간 초과({timeout}s): {command}. 끝나지 않는 서버·watch 라면 "
                f"background=True 로 다시 실행하라. 아니면 더 작은 범위로 좁혀라."
            )
        except Exception as e:  # 예외를 그래프로 흘리지 않는다 — ToolNode re-raise 크래시 방지
            return f"오류: 명령 실행 실패({type(e).__name__})."
        return _fmt_output(proc.returncode, proc.stdout, proc.stderr)

    return [run_command]
