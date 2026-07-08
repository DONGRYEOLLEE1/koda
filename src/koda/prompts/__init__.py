"""외부 프롬프트 로더. 프롬프트는 이 폴더의 `.md` 파일로 관리한다(코드에 인라인 금지).

용도별 파일: agent.md(실행 에이전트), verifier.md(검증 서브에이전트).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """prompts/<name>.md 를 읽어 반환한다."""
    path = _DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"프롬프트 파일 없음: {path}")
    return path.read_text(encoding="utf-8").strip()
