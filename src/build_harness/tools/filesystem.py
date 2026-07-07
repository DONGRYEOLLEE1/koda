"""파일시스템 도구 (읽기 전용). 워크스페이스 루트에 스코프된다.

용도: 코드베이스 탐색·조회. 위험하지 않음(승인 불필요).
툴 이름은 `workspace_` 접두로 네임스페이스를 명확히 하고 MCP 등 외부 툴과 충돌을 방지한다.
"""
from __future__ import annotations

from langchain.tools import tool

from ..workspace import SandboxError, Workspace


def not_found_help(ws: Workspace, path: str) -> str:
    """파일 없음 오류 메시지 + 유사 파일 후보 + 복구 안내 (에이전트가 스스로 재시도하도록).

    유일 후보가 있으면 그 내용까지 함께 반환한다 — 로컬 모델이 "오류 → 재탐색" 후속 스텝에서
    빈 응답으로 죽는 것을 실측했기에, 한 번의 도구 결과로 판단에 필요한 정보를 전부 준다.
    (어느 파일을 쓸지·무엇을 할지는 여전히 LLM 이 판단한다.)
    """
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
        "workspace_list_files 또는 workspace_search_text 로 실제 경로를 확인한 뒤 다시 시도하라."
    )


def make_filesystem_tools(ws: Workspace) -> list:
    """워크스페이스에 바인딩된 읽기 전용 파일 도구 목록."""

    @tool
    def workspace_read_file(path: str) -> str:
        """워크스페이스 내 텍스트 파일을 읽는다. path는 루트 기준 상대경로."""
        try:
            return ws.read_file(path)
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return not_found_help(ws, path)
        except SandboxError as e:
            return f"오류: {e}"

    @tool
    def workspace_list_files(path: str = ".") -> list[str]:
        """워크스페이스(또는 하위 경로)의 파일 목록. .venv/.git/__pycache__ 등은 제외된다."""
        return ws.list_files(path)

    @tool
    def workspace_search_text(query: str, path: str = ".") -> list[str]:
        """워크스페이스에서 텍스트를 검색해 'file:line:내용' 목록을 반환한다."""
        return ws.search_text(query, path)

    return [workspace_read_file, workspace_list_files, workspace_search_text]
