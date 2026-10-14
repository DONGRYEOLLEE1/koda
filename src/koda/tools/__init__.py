from ..workspace import Workspace
from .edit import DANGEROUS_TOOLS as _EDIT_DANGER
from .edit import make_editor_tools
from .filesystem import make_filesystem_tools
from .readonly_shell import make_readonly_shell_tools
from .shell import DANGEROUS_TOOLS as _SHELL_DANGER
from .shell import make_shell_tools

# 승인 게이트 대상 도구 이름 (내장 위험 도구)
DANGEROUS_TOOLS = _SHELL_DANGER | _EDIT_DANGER

# 이 목록 밖은 모두 게이트 대상(MCP 포함) — 경계는 denygate(유한 재앙 리스트) + (가용 시) bwrap.
SAFE_TOOLS = {
    "read_file", "search_text", "list_files", "run_readonly_command",
    "read_artifact", "analyze_image", "explore", "skill",
    # Screen evidence tools: they only read the workspace and write under .koda/renders/.
    "parse_screen", "render_page", "screen_diff",
}

__all__ = [
    "make_filesystem_tools", "make_shell_tools", "make_editor_tools",
    "make_readonly_shell_tools", "make_all_tools", "make_readonly_tools",
    "make_inspect_tools", "DANGEROUS_TOOLS", "SAFE_TOOLS",
]


def make_readonly_tools(ws: Workspace) -> list:
    """읽기 전용 파일 도구 + read-only 셸 (reader·explore 에이전트용) — 분기 오판에도 폭주 재발 방지."""
    return make_filesystem_tools(ws) + make_readonly_shell_tools(ws)


def make_inspect_tools(ws: Workspace) -> list:
    """읽기 전용 파일 도구 + read-only 셸 명령 (investigator·planner 에이전트용)."""
    return make_filesystem_tools(ws) + make_readonly_shell_tools(ws)


def make_all_tools(ws: Workspace) -> list:
    """읽기 + 셸 + 편집 도구 전체 (코더 에이전트용)."""
    return make_filesystem_tools(ws) + make_shell_tools(ws) + make_editor_tools(ws)
