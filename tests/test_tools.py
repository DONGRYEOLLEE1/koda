"""핵심 모듈: tools (langchain @tool, workspace_ 네임스페이스, 샌드박스, 편집 툴)."""
from __future__ import annotations

import pytest

from koda.tools import DANGEROUS_TOOLS, make_all_tools, make_editor_tools
from koda.tools.filesystem import make_filesystem_tools
from koda.tools.shell import make_shell_tools
from koda.workspace import SandboxError


def test_tool_names_are_namespaced(make_workspace):
    names = {t.name for t in make_all_tools(make_workspace({"a.txt": "x"}))}
    assert names == {
        "workspace_read_file", "workspace_search_text",
        "workspace_run_command",
        "workspace_write_file", "workspace_edit_file",
        "workspace_create_dir", "workspace_delete_path",
    }
    # 승인 게이트 대상: 셸 + 편집 도구
    assert DANGEROUS_TOOLS == {
        "workspace_run_command", "workspace_write_file", "workspace_edit_file",
        "workspace_create_dir", "workspace_delete_path",
    }


def test_read_and_search_tools(make_workspace):
    tools = {t.name: t for t in make_filesystem_tools(make_workspace({"x.txt": "hello\n"}))}
    assert tools["workspace_read_file"].invoke({"path": "x.txt"}) == "hello\n"
    hits = tools["workspace_search_text"].invoke({"query": "hello", "path": "."})
    assert any("x.txt" in h for h in hits)


def test_read_tool_sandbox_escape_blocked(make_workspace):
    # 샌드박스 위반은 실행되지 않고, 에이전트가 경로를 고칠 수 있게 오류 메시지로 반환된다
    tools = {t.name: t for t in make_filesystem_tools(make_workspace({"a.txt": "x"}))}
    out = tools["workspace_read_file"].invoke({"path": "../../etc/passwd"})
    assert "오류" in out and "워크스페이스 밖" in out


def test_read_missing_file_single_candidate_returns_content(make_workspace):
    # 파일명 오질의(agent.py ↔ agents.py) + 유일 후보 → 그 파일 내용까지 한 번에 반환
    # (오류 → 재탐색 후속 스텝에서 로컬 모델이 빈 응답으로 죽는 것을 실측, 스텝 자체를 제거)
    tools = {t.name: t for t in make_filesystem_tools(make_workspace({"agents.py": "REAL_CODE"}))}
    out = tools["workspace_read_file"].invoke({"path": "agent.py"})
    assert "없다" in out and "agents.py" in out and "REAL_CODE" in out


def test_read_missing_file_multi_candidates_lists_them(make_workspace):
    # 후보가 여럿이면 내용 대신 후보 목록 + 탐색 안내 (선택은 LLM 몫)
    tools = {t.name: t for t in make_filesystem_tools(
        make_workspace({"agents.py": "a", "agentz.py": "b"}))}
    out = tools["workspace_read_file"].invoke({"path": "agent.py"})
    assert "파일이 없음" in out and "agents.py" in out and "workspace_search_text" in out


def test_edit_missing_file_suggests_similar(make_workspace):
    tools = {t.name: t for t in make_editor_tools(make_workspace({"agents.py": "x = 1\n"}))}
    out = tools["workspace_edit_file"].invoke({"path": "agent.py", "old": "x = 1", "new": "x = 2"})
    assert "없다" in out and "agents.py" in out       # 유일 후보 → 내용 포함 안내
    assert "x = 1" in out                              # 실제 파일 내용까지 제공됨


def test_run_command_tool_executes(make_workspace):
    tools = {t.name: t for t in make_shell_tools(make_workspace())}
    out = tools["workspace_run_command"].invoke({"command": "echo hi"})
    assert "exit_code=0" in out and "hi" in out


def test_run_command_missing_binary_returns_error(make_workspace):
    # 없는 명령은 bash 가 exit 127 로 처리 — 예외로 실행 전체를 죽이지 않고 결과 문자열로 돌아온다
    tools = {t.name: t for t in make_shell_tools(make_workspace())}
    out = tools["workspace_run_command"].invoke({"command": "definitely-not-a-command-xyz"})
    assert "exit_code=127" in out or "not found" in out


def test_run_command_prefers_workspace_venv(make_workspace):
    # 워크스페이스에 자체 .venv 가 있으면 그 bin 이 PATH 최우선 — 부모(koda) 환경에서
    # 테스트가 돌아 거짓 성공 증거를 만드는 것을 방지한다
    ws = make_workspace({})
    shim = ws.root / ".venv" / "bin" / "python"
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/bin/sh\necho FROM_WORKSPACE_VENV\n", encoding="utf-8")
    shim.chmod(0o755)
    tools = {t.name: t for t in make_shell_tools(ws)}
    out = tools["workspace_run_command"].invoke({"command": "python --version"})
    assert "FROM_WORKSPACE_VENV" in out


def test_editor_write_creates_file(make_workspace):
    ws = make_workspace({})
    tools = {t.name: t for t in make_editor_tools(ws)}
    tools["workspace_write_file"].invoke({"path": "sub/new.txt", "content": "hi"})
    assert (ws.root / "sub/new.txt").read_text(encoding="utf-8") == "hi"


def test_editor_edit_unique_match(make_workspace):
    ws = make_workspace({"a.py": "x = 1\ny = 2\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    tools["workspace_edit_file"].invoke({"path": "a.py", "old": "x = 1", "new": "x = 42"})
    assert "x = 42" in (ws.root / "a.py").read_text(encoding="utf-8")


def test_editor_edit_rejects_nonunique(make_workspace):
    ws = make_workspace({"a.py": "v\nv\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["workspace_edit_file"].invoke({"path": "a.py", "old": "v", "new": "w"})
    assert "실패" in out                                    # 2회 일치 → 거부
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "v\nv\n"


def test_editor_delete_and_sandbox(make_workspace):
    ws = make_workspace({"gone.txt": "x"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    tools["workspace_delete_path"].invoke({"path": "gone.txt"})
    assert not (ws.root / "gone.txt").exists()
    with pytest.raises(SandboxError):
        tools["workspace_write_file"].invoke({"path": "../evil.txt", "content": "x"})


def test_bwrap_prefix_disabled_paths(monkeypatch, tmp_path):
    # ROADMAP §3.4: 미설치/BH_SANDBOX=off 면 빈 접두 — 기존 실행 경로와 동일 (비활성 smoke)
    import shutil as _shutil

    from koda.tools import shell
    from koda.workspace import Workspace

    ws = Workspace(tmp_path)
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    assert shell._bwrap_prefix(ws) == []

    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setenv("BH_SANDBOX", "off")
    assert shell._bwrap_prefix(ws) == []


def test_bwrap_prefix_shape_when_available(monkeypatch, tmp_path):
    import shutil as _shutil

    from koda.tools import shell
    from koda.workspace import Workspace

    ws = Workspace(tmp_path)
    monkeypatch.delenv("BH_SANDBOX", raising=False)
    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/bwrap")
    prefix = shell._bwrap_prefix(ws)
    assert prefix[0] == "bwrap" and prefix[-1] == "--"
    assert "--ro-bind" in prefix                       # 루트 read-only
    ws_idx = prefix.index("--bind")
    assert prefix[ws_idx + 1] == str(tmp_path)          # 워크스페이스만 쓰기 바인드
