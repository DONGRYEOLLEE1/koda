"""파일/폴더 생성·수정·삭제 도구. 위험 작업 — Permission middleware 가 실행 전 게이트한다.

용도: 소스코드/파일 변경. 되돌릴 수 없는 변경이 가능하므로 전부 승인 대상.
경로는 workspace.resolve 로 루트에 샌드박스된다(루트 밖이면 SandboxError).
"""
from __future__ import annotations

import shutil

from langchain.tools import tool

from ..workspace import Workspace

# 승인이 필요한 위험 도구 이름
DANGEROUS_TOOLS = {
    "write_file", "edit_file",
    "create_dir", "delete_path",
}


def make_editor_tools(ws: Workspace) -> list:
    """워크스페이스에 바인딩된 파일 변경 도구 목록 (위험)."""

    @tool
    def write_file(path: str, content: str) -> str:
        """파일을 생성하거나 전체를 content 로 덮어쓴다. path 는 루트 기준 상대경로."""
        p = ws.resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        return f"{'덮어씀' if existed else '생성함'}: {path} ({len(content)} chars)"

    @tool
    def edit_file(path: str, old: str, new: str) -> str:
        """파일에서 old 를 new 로 바꾼다. old 는 파일에 **정확히 1회** 나타나야 한다(부분 수정용)."""
        from .filesystem import not_found_help

        p = ws.resolve(path)
        try:
            text = p.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError):
            return not_found_help(ws, path)
        count = text.count(old)
        if count == 0:
            return f"실패: old 문자열을 찾을 수 없음: {path}"
        if count > 1:
            return f"실패: old 문자열이 {count}회 일치(유일해야 함): {path}"
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"수정함: {path}"

    @tool
    def create_dir(path: str) -> str:
        """디렉토리를 생성한다(부모 포함)."""
        ws.resolve(path).mkdir(parents=True, exist_ok=True)
        return f"디렉토리 생성: {path}"

    @tool
    def delete_path(path: str) -> str:
        """파일 또는 디렉토리를 삭제한다(되돌릴 수 없음)."""
        p = ws.resolve(path)
        if not p.exists():
            return f"없음: {path}"
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return f"삭제함: {path}"

    return [write_file, edit_file, create_dir, delete_path]
