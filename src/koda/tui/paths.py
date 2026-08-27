from __future__ import annotations

import os
from pathlib import Path

from ..workspace import Workspace, _is_ignored_dir

MAX_INDEX = 20_000


def index_paths(ws: Workspace, limit: int = MAX_INDEX) -> list[str]:
    """워크스페이스의 파일·폴더 상대 경로(폴더는 끝에 /). 무시 규칙은 Workspace 와 같고 얕은 경로가 앞에 온다."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ws.root):
        dirnames[:] = sorted(d for d in dirnames if not _is_ignored_dir(d))
        rel = Path(dirpath).relative_to(ws.root)
        prefix = "" if rel == Path(".") else f"{rel.as_posix()}/"
        out += [f"{prefix}{d}/" for d in dirnames]
        out += [f"{prefix}{f}" for f in sorted(filenames)]
        if len(out) >= limit:
            break
    return sorted(out[:limit], key=lambda p: (p.count("/") - p.endswith("/"), not p.endswith("/"), p.lower()))


def _subsequence(q: str, s: str) -> bool:
    it = iter(s)
    return all(any(c == x for x in it) for c in q)


def match_paths(query: str, paths: list[str], limit: int = 5) -> list[str]:
    """`@` 뒤 입력과 맞는 경로 최대 limit 건. 우선순위: 경로 접두 > 파일명 접두 > 부분 문자열 > 순서 부분열. 대소문자 무시."""
    q = query.lower()
    if not q:
        return paths[:limit]
    scored = []
    for p in paths:
        pl = p.lower()
        if pl == q:            # 이미 다 입력한 경로는 후보가 아니다(폴더를 고른 직후엔 그 하위가 보여야 한다)
            continue
        name = pl.rstrip("/").rsplit("/", 1)[-1]
        if pl.startswith(q):
            rank = 0
        elif name.startswith(q):
            rank = 1
        elif q in pl:
            rank = 2
        elif _subsequence(q, pl):
            rank = 3
        else:
            continue
        scored.append((rank, len(p), p))
    return [p for _, _, p in sorted(scored)[:limit]]
