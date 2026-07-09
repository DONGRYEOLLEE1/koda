from __future__ import annotations

from langchain.tools import tool

from ..workspace import SandboxError, Workspace

_MAX_LINES = 400        # 범위 미지정 시 한 응답 최대 줄 수 (hard cap)
_MAX_CHARS = 120_000    # 한 응답 최대 문자 수 (초과 시 뒤에서 줄 단위로 절단)
_MAX_HITS = 200         # search_text 반환 히트 하드 상한 (인자로도 못 넘김)


def _format_numbered(lines: list[str], start: int) -> str:
    """1-기반 줄번호를 붙여 렌더한다. start = 첫 줄의 실제 줄번호."""
    width = len(str(start + len(lines) - 1)) if lines else 1
    return "\n".join(f"{start + i:>{width}}| {ln}" for i, ln in enumerate(lines))


def not_found_help(ws: Workspace, path: str) -> str:
    """파일 없음 오류 + 유사 파일 후보 + 복구 안내. 유일 후보면 내용까지 반환해 재탐색 스텝에서 빈 응답으로 죽는 것을 막는다."""
    similar = ws.suggest_similar(path)
    if len(similar) == 1:
        try:
            content = ws.read_file(similar[0])
            return (
                f"오류: '{path}' 파일은 없다. 이름이 가장 비슷한 파일은 '{similar[0]}' 하나뿐이다. "
                f"사용자가 파일명을 잘못 썼을 가능성이 높다. 아래 '{similar[0]}' 내용을 보고 판단하라.\n\n"
                f"--- {similar[0]} ---\n{content}"
            )
        except OSError:
            pass
    hint = f" 이름이 비슷한 파일: {', '.join(similar)}." if similar else ""
    return (
        f"오류: 파일이 없음: {path}.{hint} "
        "search_text 로 실제 경로를 확인한 뒤 다시 시도하라."
    )


def make_filesystem_tools(ws: Workspace) -> list:
    """워크스페이스에 바인딩된 읽기 전용 파일 도구 목록."""

    @tool
    def read_file(path: str, offset: int = 1, limit: int | None = None) -> str:
        """워크스페이스 내 텍스트 파일을 줄번호를 붙여 읽는다. path 는 루트 기준 상대경로.

        큰 파일은 offset(1-기반 시작 줄)·limit(최대 줄 수)로 필요한 범위만 발췌하라. 범위를 안
        주면 앞부분만(최대 400줄/120k자) 반환한다 — 더 보려면 안내된 offset 으로 재호출한다.
        """
        try:
            data = ws.resolve(path).read_bytes()
        except SandboxError as e:
            return f"오류: {e}"
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return not_found_help(ws, path)
        if b"\x00" in data[:8192]:
            return "오류: 바이너리/비텍스트 파일로 보인다 — 이 도구로 읽지 마라(텍스트 파일만)."
        lines = data.decode("utf-8", errors="replace").splitlines()
        total = len(lines)
        if total == 0:
            return f"(빈 파일: {path}, 0줄)"
        start = max(1, offset)
        if start > total:
            return f"오류: offset {offset} 이 파일 총 {total}줄을 넘는다."
        end = total if limit is None else min(total, start - 1 + max(1, limit))
        end = min(end, start - 1 + _MAX_LINES)                 # 줄 hard cap
        window = lines[start - 1:end]
        while len(window) > 1 and sum(len(ln) for ln in window) > _MAX_CHARS:
            window.pop()                                       # 문자 hard cap: 뒤에서 줄여냄
            end -= 1
        body = _format_numbered(window, start)
        if end < total:
            body += f"\n…(총 {total}줄 중 {start}–{end} 표시. 더 보려면 offset={end + 1} 로 재호출)"
        return body

    @tool
    def search_text(query: str, path: str = ".", max_hits: int = 80, glob: str | None = None) -> str:
        """워크스페이스에서 텍스트를 검색해 'file:line:내용' 을 반환한다.

        고빈도 쿼리는 max_hits(기본 80, 하드 상한 200)에서 잘리고 생략 개수를 안내한다.
        glob(예: '*.py')로 검색 대상 파일을 좁힐 수 있다.
        """
        cap = min(max(1, max_hits), _MAX_HITS)
        hits = ws.search_text(query, path, glob=glob)
        total = len(hits)
        meta = f"query='{query}' path='{path}'" + (f" glob='{glob}'" if glob else "")
        if total == 0:
            return f"[0 히트] {meta}"
        shown = hits[:cap]
        note = (f"\n…({total - cap} 히트 생략 — path/glob 을 좁히거나 query 를 구체화)"
                if total > cap else "")
        return f"[1-{len(shown)}/총 {total} 히트] {meta}\n" + "\n".join(shown) + note

    @tool
    def list_files(path: str = ".", max_depth: int = 3, max_entries: int = 200) -> str:
        """워크스페이스 디렉터리의 파일 목록을 반환한다(.git/.venv/.koda 등 내부 디렉터리 제외).

        max_depth: 하위 디렉터리 깊이 상한. max_entries 초과 시 절단하고 안내한다.
        """
        try:
            files = ws.list_files(path, max_depth=max_depth)
        except SandboxError as e:
            return f"오류: {e}"
        if not files:
            return f"(비어 있거나 없는 경로: {path})"
        total = len(files)
        body = "\n".join(files[:max_entries])
        if total > max_entries:
            body += f"\n…({total}개 중 {max_entries}개 표시 — path 를 좁히거나 max_depth 를 줄여라)"
        return body

    return [read_file, search_text, list_files]
