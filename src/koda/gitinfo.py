from __future__ import annotations

import subprocess
from pathlib import Path


def _git(root: str | Path, *args: str) -> str:
    """git 명령 한 줄 출력. 저장소가 아니거나 실패하면 빈 문자열."""
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def git_ref(root: str | Path) -> tuple[str, str]:
    """(브랜치, HEAD 짧은 sha). git 저장소가 아니면 ("", ""), detached HEAD 면 브랜치가 빈 값이다."""
    sha = _git(root, "rev-parse", "--short", "HEAD")
    if not sha:
        return "", ""
    return _git(root, "symbolic-ref", "--short", "-q", "HEAD"), sha
