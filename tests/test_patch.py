from __future__ import annotations

from koda.tools.edit import _apply_patch, make_editor_tools


# --- _apply_patch 순수 함수: 개행/EOF 보존이 핵심 ---

def test_apply_replace_middle():
    assert _apply_patch("a\nb\nc\n", 2, 2, "B") == "a\nB\nc\n"


def test_apply_replace_multiline():
    assert _apply_patch("a\nb\nc\n", 2, 2, "X\nY") == "a\nX\nY\nc\n"


def test_apply_delete_range():
    assert _apply_patch("a\nb\nc\n", 2, 2, "") == "a\nc\n"
    assert _apply_patch("a\nb\nc\nd\n", 2, 3, "") == "a\nd\n"


def test_apply_insert_middle():
    assert _apply_patch("a\nb\n", 2, 1, "X") == "a\nX\nb\n"   # line1 뒤에 삽입


def test_apply_insert_eof():
    assert _apply_patch("a\nb\n", 3, 2, "X") == "a\nb\nX\n"


def test_apply_insert_empty_file():
    assert _apply_patch("", 1, 0, "X") == "X\n"


def test_apply_preserves_crlf():
    assert _apply_patch("a\r\nb\r\n", 2, 2, "B") == "a\r\nB\r\n"


def test_apply_preserves_no_trailing_newline():
    assert _apply_patch("a\nb", 2, 2, "B") == "a\nB"          # 원본 EOF 개행 없음 → 유지


def test_apply_no_double_newline_when_replacement_has_one():
    assert _apply_patch("a\nb\nc\n", 2, 2, "B\n") == "a\nB\nc\n"


def test_apply_replace_first_and_last():
    assert _apply_patch("a\nb\nc\n", 1, 1, "A") == "A\nb\nc\n"
    assert _apply_patch("a\nb\nc\n", 3, 3, "C") == "a\nb\nC\n"


# --- patch_file 도구 ---

def test_patch_replace(make_workspace):
    ws = make_workspace({"a.py": "x = 1\ny = 2\nz = 3\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["patch_file"].invoke({"path": "a.py", "start_line": 2, "end_line": 2, "replacement": "y = 20"})
    assert "패치함" in out
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "x = 1\ny = 20\nz = 3\n"


def test_patch_anchor_mismatch_no_change(make_workspace):
    ws = make_workspace({"a.py": "x = 1\ny = 2\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["patch_file"].invoke({
        "path": "a.py", "start_line": 2, "end_line": 2, "replacement": "y = 20", "expected_head": "WRONG"})
    assert "앵커 불일치" in out
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "x = 1\ny = 2\n"   # 무변경(자기검증)


def test_patch_anchor_match_applies(make_workspace):
    ws = make_workspace({"a.py": "x = 1\ny = 2\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    tools["patch_file"].invoke({
        "path": "a.py", "start_line": 2, "end_line": 2, "replacement": "y = 20", "expected_head": "y = 2"})
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "x = 1\ny = 20\n"


def test_patch_out_of_range_no_change(make_workspace):
    ws = make_workspace({"a.py": "x = 1\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["patch_file"].invoke({"path": "a.py", "start_line": 5, "end_line": 9, "replacement": "z"})
    assert "실패" in out and "범위" in out
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "x = 1\n"


def test_patch_preserves_exec_bit(make_workspace):
    import os as _os
    ws = make_workspace({"run.sh": "#!/bin/sh\necho old\n"})
    (ws.root / "run.sh").chmod(0o755)
    tools = {t.name: t for t in make_editor_tools(ws)}
    tools["patch_file"].invoke({"path": "run.sh", "start_line": 2, "end_line": 2, "replacement": "echo new"})
    assert _os.stat(ws.root / "run.sh").st_mode & 0o111
    assert (ws.root / "run.sh").read_text(encoding="utf-8") == "#!/bin/sh\necho new\n"


def test_patch_insert_then_delete(make_workspace):
    ws = make_workspace({"a.py": "a\nb\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    tools["patch_file"].invoke({"path": "a.py", "start_line": 3, "end_line": 2, "replacement": "c"})  # EOF 삽입
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "a\nb\nc\n"
    tools["patch_file"].invoke({"path": "a.py", "start_line": 1, "end_line": 1, "replacement": ""})   # 삭제
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "b\nc\n"


# --- 회귀 방지: 개행/EOF/앵커 손상 ---

def test_apply_insert_eof_no_final_newline():
    # 무-EOF 파일 끝 삽입: 줄 병합 없이, 무-EOF 상태 보존
    assert _apply_patch("a\nb", 3, 2, "X") == "a\nb\nX"


def test_apply_delete_last_line_no_final_newline():
    # 무-EOF 파일의 마지막 줄 삭제: EOF 개행을 새로 만들지 않는다
    assert _apply_patch("a\nb", 2, 2, "") == "a"


def test_apply_mixed_newline_keeps_edited_line_lf():
    # 파일 어딘가 CRLF 가 있어도 LF 줄 교체는 LF 유지
    assert _apply_patch("a\nb\r\nc\n", 1, 1, "A") == "A\nb\r\nc\n"


def test_patch_tool_preserves_crlf_bytes(make_workspace):
    # 도구가 read_text 로 CRLF 를 \n 으로 번역해 손상시키지 않는다
    ws = make_workspace({})
    (ws.root / "crlf.txt").write_bytes(b"a\r\nb\r\nc\r\n")
    tools = {t.name: t for t in make_editor_tools(ws)}
    tools["patch_file"].invoke({"path": "crlf.txt", "start_line": 2, "end_line": 2, "replacement": "B"})
    assert (ws.root / "crlf.txt").read_bytes() == b"a\r\nB\r\nc\r\n"


def test_patch_insert_anchor_catches_drift(make_workspace):
    # 삽입에서도 expected_head 로 드리프트를 잡는다
    ws = make_workspace({"a.py": "a\nDRIFT\nb\n"})
    tools = {t.name: t for t in make_editor_tools(ws)}
    out = tools["patch_file"].invoke({
        "path": "a.py", "start_line": 2, "end_line": 1, "replacement": "X", "expected_head": "b"})
    assert "앵커 불일치" in out
    assert (ws.root / "a.py").read_text(encoding="utf-8") == "a\nDRIFT\nb\n"   # 무변경
