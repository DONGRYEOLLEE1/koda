from __future__ import annotations

import pytest

from koda.tools import DANGEROUS_TOOLS, SAFE_TOOLS, make_all_tools, make_editor_tools
from koda.tools.filesystem import make_filesystem_tools
from koda.tools.shell import make_shell_tools
from koda.workspace import SandboxError


def test_tool_names_are_namespaced(make_workspace):
    names = {t.name for t in make_all_tools(make_workspace({"a.txt": "x"}))}
    assert names == {
        "read_file", "search_text", "list_files",
        "run_command",
        "write_file", "edit_file", "multi_edit", "patch_file",
        "create_dir", "delete_path",
    }
    # 승인 게이트 대상: 셸 + 편집 도구
    assert DANGEROUS_TOOLS == {
        "run_command", "write_file", "edit_file", "multi_edit", "patch_file",
        "create_dir", "delete_path",
    }


def test_screen_tools_are_safe_and_not_dangerous():
    # The screen tools only read the workspace and write under .koda/renders/, so they pass the approval gate.
    assert {"parse_screen", "render_page", "screen_diff"} <= SAFE_TOOLS
    assert not {"parse_screen", "render_page", "screen_diff"} & DANGEROUS_TOOLS


def test_read_and_search_tools(make_workspace):
    tools = {t.name: t for t in make_filesystem_tools(make_workspace({"x.txt": "hello\n"}))}
    assert tools["read_file"].invoke({"path": "x.txt"}) == "1| hello"   # 줄번호 부여
    out = tools["search_text"].invoke({"query": "hello", "path": "."})
    assert "x.txt:1:hello" in out and "총 1 히트" in out


def test_search_text_caps_hits(make_workspace):
    src = "\n".join("match here" for _ in range(50)) + "\n"
    tools = {t.name: t for t in make_filesystem_tools(make_workspace({"m.txt": src}))}
    out = tools["search_text"].invoke({"query": "match", "max_hits": 10})
    assert out.count("m.txt:") == 10                  # max_hits 만큼만 표시
    assert "총 50 히트" in out and "40 히트 생략" in out


def test_search_text_glob_filter(make_workspace):
    tools = {t.name: t for t in make_filesystem_tools(
        make_workspace({"a.py": "TARGET\n", "b.txt": "TARGET\n"}))}
    out = tools["search_text"].invoke({"query": "TARGET", "glob": "*.py"})
    assert "a.py:1:TARGET" in out and "b.txt" not in out   # glob 으로 .txt 제외


def test_read_file_offset_limit_range(make_workspace):
    src = "\n".join(f"line{i}" for i in range(1, 21)) + "\n"       # 20줄
    tools = {t.name: t for t in make_filesystem_tools(make_workspace({"big.py": src}))}
    out = tools["read_file"].invoke({"path": "big.py", "offset": 5, "limit": 3})
    assert out.startswith("5| line5\n6| line6\n7| line7")          # 지정 구간만
    assert "총 20줄 중 5–7" in out                                 # 더 있음 안내


def test_read_file_hard_cap_notes_more(make_workspace):
    src = "\n".join(f"L{i}" for i in range(1, 501)) + "\n"         # 500줄
    tools = {t.name: t for t in make_filesystem_tools(make_workspace({"huge.py": src}))}
    out = tools["read_file"].invoke({"path": "huge.py"})
    assert "1| L1" in out
    assert "401| L401" not in out                                 # 400줄 hard cap
    assert "offset=401" in out                                    # 이어 읽기 안내


def test_read_file_rejects_binary(make_workspace):
    ws = make_workspace({})
    (ws.root / "bin.dat").write_bytes(b"PK\x03\x04\x00\x00binary")
    tools = {t.name: t for t in make_filesystem_tools(ws)}
    assert "바이너리" in tools["read_file"].invoke({"path": "bin.dat"})


def test_list_files_tool_and_truncation(make_workspace):
    files = {f"f{i}.txt": "x" for i in range(5)}
    tools = {t.name: t for t in make_filesystem_tools(make_workspace(files))}
    out = tools["list_files"].invoke({"path": "."})
    assert "f0.txt" in out and "f4.txt" in out
    assert "5개 중 2개 표시" in tools["list_files"].invoke({"path": ".", "max_entries": 2})


def test_read_tool_sandbox_escape_blocked(make_workspace):
    # 샌드박스 위반은 실행되지 않고, 에이전트가 경로를 고칠 수 있게 오류 메시지로 반환된다
    tools = {t.name: t for t in make_filesystem_tools(make_workspace({"a.txt": "x"}))}
    out = tools["read_file"].invoke({"path": "../../etc/passwd"})
    assert "오류" in out and "워크스페이스 밖" in out


def test_read_missing_file_single_candidate_returns_content(make_workspace):
    # 파일명 오질의(agent.py ↔ agents.py) + 유일 후보 → 그 파일 내용까지 한 번에 반환한다(재탐색에서 빈 응답으로 죽는 것을 막는다).
    tools = {t.name: t for t in make_filesystem_tools(make_workspace({"agents.py": "REAL_CODE"}))}
    out = tools["read_file"].invoke({"path": "agent.py"})
    assert "없다" in out and "agents.py" in out and "REAL_CODE" in out


def test_run_command_tool_executes(make_workspace):
    tools = {t.name: t for t in make_shell_tools(make_workspace())}
    out = tools["run_command"].invoke({"command": "echo hi"})
    assert "exit_code=0" in out and "hi" in out


def test_run_command_prefers_workspace_venv(make_workspace):
    # 워크스페이스에 자체 .venv 가 있으면 그 bin 이 PATH 최우선 — 부모(koda) 환경에서 테스트가 돌아 거짓 성공 증거를 만드는 것을 방지한다.
    ws = make_workspace({})
    shim = ws.root / ".venv" / "bin" / "python"
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/bin/sh\necho FROM_WORKSPACE_VENV\n", encoding="utf-8")
    shim.chmod(0o755)
    tools = {t.name: t for t in make_shell_tools(ws)}
    out = tools["run_command"].invoke({"command": "python --version"})
    assert "FROM_WORKSPACE_VENV" in out


def test_reader_tools_include_readonly_shell(make_workspace):
    # 분기 면역: reader 도 read-only 셸을 가져 '폴더 훑기' read 오분류 시 40콜 폭주 방지
    from koda.tools import make_readonly_tools
    names = {t.name for t in make_readonly_tools(make_workspace({}))}
    assert "run_readonly_command" in names
    assert {"read_file", "search_text", "list_files"} <= names


def test_run_command_head_tail_excerpt(make_workspace):
    # 긴 출력은 head+tail 로 발췌하고 생략을 명시한다(tail-only 절단 불투명 방지)
    tools = {t.name: t for t in make_shell_tools(make_workspace())}
    out = tools["run_command"].invoke({"command": "seq 1 200"})
    assert "1\n2\n3\n" in out          # head
    assert "199\n200" in out           # tail
    assert "생략" in out               # 절단 명시


def test_run_command_background_returns_immediately(make_workspace):
    # 장수명 프로세스(서버)를 background 로 띄우면 timeout 까지 블록하지 않고 즉시 PID·로그를 돌려준다
    # (foreground 로 서버를 띄워 60초 타임아웃까지 블록하던 실측 병목의 수정, issue #19)
    import os as _os
    import re as _re
    import signal as _signal
    import time as _time
    ws = make_workspace()
    tools = {t.name: t for t in make_shell_tools(ws)}
    t0 = _time.monotonic()
    out = tools["run_command"].invoke({"command": "sleep 30", "background": True, "timeout": 60})
    elapsed = _time.monotonic() - t0
    assert elapsed < 3.0                                   # 30초 sleep 인데도 즉시 반환(비블로킹)
    assert "백그라운드" in out and "PID=" in out
    assert ".koda/run/" in out                             # 로그 경로 안내
    assert (ws.root / ".koda" / "run").is_dir()            # 로그 디렉터리 생성
    pid = int(_re.search(r"PID=(\d+)", out).group(1))
    try:  # 정리 — start_new_session 로 세션 리더이므로 프로세스 그룹째 종료
        _os.killpg(pid, _signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def test_editor_write_creates_file(make_workspace):
    ws = make_workspace({})
    tools = {t.name: t for t in make_editor_tools(ws)}
    tools["write_file"].invoke({"path": "sub/new.txt", "content": "hi"})
    assert (ws.root / "sub/new.txt").read_text(encoding="utf-8") == "hi"


def test_editor_edit_unique_match(make_workspace):
    ws = make_workspace({"a.py": "x = 1\ny = 2\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    tools["edit_file"].invoke({"path": "a.py", "old": "x = 1", "new": "x = 42"})
    assert "x = 42" in (ws.root / "a.py").read_text(encoding="utf-8")


def test_editor_edit_rejects_nonunique(make_workspace):
    ws = make_workspace({"a.py": "v\nv\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["edit_file"].invoke({"path": "a.py", "old": "v", "new": "w"})
    assert "실패" in out                                    # 2회 일치 → 거부
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "v\nv\n"


def test_editor_delete_and_sandbox(make_workspace):
    ws = make_workspace({"gone.txt": "x"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    tools["delete_path"].invoke({"path": "gone.txt"})
    assert not (ws.root / "gone.txt").exists()
    with pytest.raises(SandboxError):
        tools["write_file"].invoke({"path": "../evil.txt", "content": "x"})


def test_atomic_write_preserves_exec_bit(make_workspace):
    # _atomic_write + copymode: 편집 후에도 실행 비트(chmod +x)가 보존돼야 한다
    import os as _os
    ws = make_workspace({"run.sh": "#!/bin/sh\necho old\n"})
    (ws.root / "run.sh").chmod(0o755)
    tools = {t.name: t for t in make_editor_tools(ws)}
    tools["edit_file"].invoke({"path": "run.sh", "old": "old", "new": "new"})
    assert _os.stat(ws.root / "run.sh").st_mode & 0o111        # 실행 비트 보존
    assert "new" in (ws.root / "run.sh").read_text(encoding="utf-8")


def test_edit_replace_all(make_workspace):
    ws = make_workspace({"a.py": "v\nv\nv\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["edit_file"].invoke({"path": "a.py", "old": "v", "new": "w", "replace_all": True})
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "w\nw\nw\n"
    assert "replace_all 3회" in out


def test_edit_no_match_suggests_line(make_workspace):
    ws = make_workspace({"a.py": "def hello():\n    return 1\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["edit_file"].invoke({"path": "a.py", "old": "def helo():", "new": "x"})
    assert "실패" in out and "비슷한 줄" in out                  # 근접 후보 안내
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "def hello():\n    return 1\n"  # 무변경


def test_multi_edit_applies_all(make_workspace):
    ws = make_workspace({"a.py": "x = 1\ny = 2\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    tools["multi_edit"].invoke({"path": "a.py", "edits": [
        {"old": "x = 1", "new": "x = 10"}, {"old": "y = 2", "new": "y = 20"}]})
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "x = 10\ny = 20\n"


def test_multi_edit_rolls_back_on_failure(make_workspace):
    ws = make_workspace({"a.py": "x = 1\ny = 2\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["multi_edit"].invoke({"path": "a.py", "edits": [
        {"old": "x = 1", "new": "x = 10"}, {"old": "NOPE", "new": "z"}]})
    assert "실패" in out and "롤백" in out
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "x = 1\ny = 2\n"  # 전부 롤백


def _diff_body(out: str) -> str:
    return out.split("\n", 1)[1] if "\n" in out else ""


def test_edit_success_emits_mixed_hunk(make_workspace):
    # 교체 → unified 훅에 -/+ 둘 다, 요약에 +1/-1
    ws = make_workspace({"a.py": "x = 1\ny = 2\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["edit_file"].invoke({"path": "a.py", "old": "x = 1", "new": "x = 10"})
    assert out.startswith("수정함: a.py") and "+1/-1" in out
    body = _diff_body(out)
    assert body.startswith("@@") and "-x = 1" in body and "+x = 10" in body


def test_patch_insert_is_add_only(make_workspace):
    # 삽입 → +줄만, -줄 없음(순수 추가)
    ws = make_workspace({"a.py": "a\nb\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["patch_file"].invoke({"path": "a.py", "start_line": 2, "end_line": 1, "replacement": "NEW"})
    assert "+1/-0" in out
    body = _diff_body(out)
    assert "+NEW" in body and not any(ln.startswith("-") for ln in body.splitlines())


def test_patch_delete_is_remove_only(make_workspace):
    # 삭제 → -줄만, +줄 없음(순수 삭제)
    ws = make_workspace({"a.py": "a\nb\nc\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["patch_file"].invoke({"path": "a.py", "start_line": 2, "end_line": 2, "replacement": ""})
    assert "+0/-1" in out
    body = _diff_body(out)
    assert "-b" in body and not any(ln.startswith("+") for ln in body.splitlines())


def test_multi_edit_emits_hunk(make_workspace):
    ws = make_workspace({"a.py": "x = 1\ny = 2\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["multi_edit"].invoke({"path": "a.py", "edits": [{"old": "x = 1", "new": "x = 10"}]})
    assert _diff_body(out).startswith("@@") and "+x = 10" in out


def test_large_edit_counts_accurate_despite_truncation(make_workspace):
    # 40줄 초과 대형 편집: diff 본문은 절단돼도 요약 +N/-M 은 절단 전 전체 기준이라 정확해야 한다.
    before = "\n".join(f"old{i}" for i in range(100)) + "\n"
    after = "\n".join(f"new{i}" for i in range(100)) + "\n"
    ws = make_workspace({"big.py": before})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["edit_file"].invoke({"path": "big.py", "old": before, "new": after})
    assert "+100/-100" in out          # 절단돼도 수치는 정확(방향 뒤집힘·과소집계 없음)
    assert "줄 더" in out               # 본문은 실제로 절단됨


def test_bwrap_prefix_disabled_paths(monkeypatch, tmp_path):
    # 미설치/BH_SANDBOX=off 면 빈 접두 — 기존 실행 경로와 동일(비활성 smoke)
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


def test_run_command_reports_failure_through_pipe(make_workspace):
    # `pytest … | tail` 꼴에서 exit_code 가 tail 의 0 으로 덮이면 실패가 성공으로 보고된다 (pipefail).
    from koda.tools.shell import make_shell_tools
    run = make_shell_tools(make_workspace())[0]
    out = run.invoke({"command": "false | tail -1"})
    assert out.startswith("exit_code=1")
    assert run.invoke({"command": "true | tail -1"}).startswith("exit_code=0")


def test_readonly_command_reports_failure_through_pipe(make_workspace):
    from koda.tools.readonly_shell import make_readonly_shell_tools
    run = make_readonly_shell_tools(make_workspace())[0]
    assert run.invoke({"command": "false | cat"}).startswith("exit_code=1")
