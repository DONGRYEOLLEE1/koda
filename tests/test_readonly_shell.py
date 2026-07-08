"""핵심 모듈: read-only 셸 명령 도구의 3-레이어 validator (AGENT_ROSTER §5).

이 도구는 SAFE_TOOLS(자동 허용)라 validator 가 유일 보안 경계다 — 쓰기 방지 + 읽기 유출 방지
둘 다를 검증한다. fail-closed 는 예외가 아니라 문자열 반환(§5.2).
"""
from __future__ import annotations

import os

from koda.tools.readonly_shell import _validate, make_readonly_shell_tools


def _tool(ws):
    return make_readonly_shell_tools(ws)[0]


def _run(ws, argv):
    return _tool(ws).invoke({"argv": argv})


# --- (1) 명령명 allowlist ---

def test_allowed_commands_pass_validation(make_workspace):
    ws = make_workspace({"a.txt": "x"})
    for argv in (["pwd"], ["ls", "-al"], ["tree", "-L", "2"], ["find", ".", "-maxdepth", "1"],
                 ["wc", "-l", "a.txt"], ["cat", "a.txt"], ["grep", "-rn", "x", "."],
                 ["head", "-n", "5", "a.txt"], ["tail", "-5", "a.txt"]):
        assert _validate(argv, ws) is None, argv


def test_disallowed_command_rejected(make_workspace):
    ws = make_workspace()
    for argv in (["rm", "-rf", "."], ["python", "x.py"], ["git", "status"], ["pytest"],
                 ["echo", "hi"], ["bash", "-c", "ls"], ["sh"], ["chmod", "777", "."]):
        err = _validate(argv, ws)
        assert err and "허용되지 않은 명령" in err, argv


# --- (2) 옵션 whitelist: 실행/삭제/심링크추적 프리미티브 거부 ---

def test_dangerous_options_rejected(make_workspace):
    ws = make_workspace({"a.txt": "x"})
    for argv in (["find", ".", "-delete"], ["find", ".", "-exec", "rm", "{}", ";"],
                 ["find", ".", "-execdir", "cat", "{}", ";"], ["find", ".", "-fprintf", "/tmp/x", "%p"],
                 ["find", "-L", "."], ["grep", "-R", "x", "."], ["ls", "-Z"],
                 ["ls", "--color=always"], ["cat", "--help"]):
        err = _validate(argv, ws)
        assert err and "허용되지 않은 옵션" in err, argv


def test_allowed_options_pass(make_workspace):
    ws = make_workspace({"a.txt": "x"})
    for argv in (["ls", "-la"], ["ls", "-al", "-h"], ["grep", "-rn", "-i", "x", "."],
                 ["grep", "-A3", "x", "a.txt"], ["grep", "-A", "3", "x", "a.txt"],
                 ["find", ".", "-name", "*.txt", "-type", "f", "-maxdepth", "2"],
                 ["head", "-n20", "a.txt"], ["tail", "-20", "a.txt"], ["tree", "-L", "2", "-d"]):
        assert _validate(argv, ws) is None, argv


# --- (3) 경로 realpath confinement (읽기 유출 방지) ---

def test_path_escape_rejected(make_workspace):
    ws = make_workspace({"a.txt": "x"})
    for argv in (["cat", "/etc/passwd"], ["cat", "../../etc/passwd"], ["ls", "/"],
                 ["ls", ".."], ["grep", "x", "/etc/hosts"], ["head", "/etc/hosts"],
                 ["find", "/etc"]):
        err = _validate(argv, ws)
        assert err and "밖" in err, argv


def test_tilde_is_literal_not_expanded(make_workspace):
    # 셸이 없으므로 ~ 는 확장되지 않는다 — 리터럴 하위경로로 취급돼 워크스페이스 안에 갇힌다
    # (진짜 홈의 ~/.ssh 로 새지 않는다). 존재하지 않는 경로라 명령이 "없음"을 보고할 뿐.
    assert _validate(["cat", "~/.ssh/id_rsa"], make_workspace({"a.txt": "x"})) is None


def test_symlink_escape_rejected(make_workspace):
    ws = make_workspace({"a.txt": "x"})
    os.symlink("/etc/passwd", ws.root / "leak")  # 워크스페이스 안 심링크가 밖을 가리킴
    err = _validate(["cat", "leak"], ws)
    assert err and "밖" in err


def test_koda_internal_rejected(make_workspace):
    ws = make_workspace({"a.txt": "x"})
    (ws.root / ".koda").mkdir(exist_ok=True)
    for argv in (["cat", ".koda/x"], ["ls", ".koda"], ["grep", "y", ".koda"]):
        err = _validate(argv, ws)
        assert err and ".koda" in err, argv


def test_in_workspace_paths_ok(make_workspace):
    ws = make_workspace({"sub/a.txt": "x"})
    for argv in (["cat", "sub/a.txt"], ["ls", "sub"], ["grep", "x", "sub/a.txt"], ["ls", "."]):
        assert _validate(argv, ws) is None, argv


# --- 실행: 통과 명령은 실제로 돌고, 위반은 거부 문자열(예외 아님) ---

def test_tool_executes_pwd_and_ls(make_workspace):
    ws = make_workspace({"hello.txt": "hi"})
    out = _run(ws, ["pwd"])
    assert "exit_code=0" in out and str(ws.root) in out
    assert "hello.txt" in _run(ws, ["ls"])


def test_tool_rejects_without_raising(make_workspace):
    ws = make_workspace()
    # fail-closed 는 예외가 아니라 문자열 (langgraph ToolNode re-raise 크래시 방지 §5.2)
    out = _run(ws, ["cat", "/etc/passwd"])
    assert "거부됨" in out and "밖" in out
    out = _run(ws, ["rm", "-rf", "."])
    assert "거부됨" in out and "허용되지 않은 명령" in out


def test_pipe_chars_are_literal_not_shell(make_workspace):
    # argv 전용 — "|" 는 셸 파이프가 아니라 리터럴 인자(경로로 취급). 셸 해석이 없으므로
    # 뒤따르는 rm 이 실행되지 않는다(파이프·체이닝 구조적 차단).
    ws = make_workspace({"a.txt": "x"})
    out = _run(ws, ["grep", "x", "a.txt", "|"])
    assert "거부됨" not in out


def test_null_byte_rejected(make_workspace):
    # 널바이트는 realpath/subprocess 에서 ValueError → 사전 거부(예외 누수·그래프 크래시 방지 §5.2)
    ws = make_workspace({"a.txt": "x"})
    assert "널바이트" in _validate(["cat", "a.txt\x00/etc/passwd"], ws)
    assert "거부됨" in _run(ws, ["grep", "x\x00", "a.txt"])


def test_no_exception_leaks_from_tool(make_workspace):
    # 어떤 입력도 예외로 새지 않고 문자열로 돌아온다 (fail-closed 불변식 (d))
    ws = make_workspace({"a.txt": "x"})
    for argv in ([], ["cat", "a\x00b"], ["definitely-not-a-cmd"], ["cat", "/etc/passwd"]):
        assert isinstance(_run(ws, argv), str), argv


def test_find_and_grep_hide_koda_contents(make_workspace):
    # 재귀 find/grep 이 .koda 내부(세션·artifact 이름·내용)를 열거하지 않는다 (reader 와 parity)
    ws = make_workspace({"src/a.py": "needle"})
    (ws.root / ".koda" / "artifacts").mkdir(parents=True)
    (ws.root / ".koda" / "artifacts" / "001-secret.txt").write_text("needle", encoding="utf-8")
    find_out = _run(ws, ["find", "."])
    assert "001-secret.txt" not in find_out and "a.py" in find_out
    grep_out = _run(ws, ["grep", "-rn", "needle", "."])
    assert "001-secret.txt" not in grep_out and "a.py" in grep_out
