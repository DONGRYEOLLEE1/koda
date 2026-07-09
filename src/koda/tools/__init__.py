"""도구 모음. 용도/기능별로 파일 분리:
- filesystem.py     : read_file / search_text / list_files (읽기 전용)
- readonly_shell.py : run_readonly_command (읽기 전용 셸 명령, allowlist+샌드박스)
- shell.py          : run_command (위험, 승인 필요)
- edit.py           : write_file / edit_file / create_dir / delete_path (위험, 승인 필요)
"""
from ..workspace import Workspace
from .edit import DANGEROUS_TOOLS as _EDIT_DANGER
from .edit import make_editor_tools
from .filesystem import make_filesystem_tools
from .readonly_shell import make_readonly_shell_tools
from .shell import DANGEROUS_TOOLS as _SHELL_DANGER
from .shell import make_shell_tools

# 승인 게이트 대상 도구 이름 (내장 위험 도구)
DANGEROUS_TOOLS = _SHELL_DANGER | _EDIT_DANGER

# 승인 없이 자동 허용되는 안전(읽기 전용) 도구. Permission middleware 는 이 목록에 없는
# 모든 도구를 위험으로 간주해 게이트한다 → MCP/외부 주입 도구도 기본적으로 승인 대상.
# run_readonly_command 는 SAFE(자동 실행)지만 셸 문자열이다 — 정적 분류기는 폐기됐고(ef8775d),
# 경계는 denygate(유한 재앙 리스트) + (가용 시) bwrap + LLM 신뢰다.
SAFE_TOOLS = {
    "read_file", "search_text", "list_files", "run_readonly_command",
    "read_artifact", "analyze_image", "explore", "skill",
}

__all__ = [
    "make_filesystem_tools", "make_shell_tools", "make_editor_tools",
    "make_readonly_shell_tools", "make_all_tools", "make_readonly_tools",
    "make_inspect_tools", "DANGEROUS_TOOLS", "SAFE_TOOLS",
]


def make_readonly_tools(ws: Workspace) -> list:
    """읽기 전용 파일 도구 + read-only 셸 (reader·explore 에이전트용).

    reader 도 일괄 셸을 갖게 해 '폴더 훑기' 가 read 경로로 오분류돼도 40콜 폭주가 재발하지
    않게 한다(분기 면역). 쓰기 불변식은 denygate + (가용 시) bwrap 이 유지한다.
    """
    return make_filesystem_tools(ws) + make_readonly_shell_tools(ws)


def make_inspect_tools(ws: Workspace) -> list:
    """읽기 전용 파일 도구 + read-only 셸 명령 (investigator·planner 에이전트용)."""
    return make_filesystem_tools(ws) + make_readonly_shell_tools(ws)


def make_all_tools(ws: Workspace) -> list:
    """읽기 + 셸 + 편집 도구 전체 (코더 에이전트용)."""
    return make_filesystem_tools(ws) + make_shell_tools(ws) + make_editor_tools(ws)
