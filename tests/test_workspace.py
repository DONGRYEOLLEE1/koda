"""핵심 모듈: workspace (루트 스코프 샌드박스)."""
from __future__ import annotations

import pytest

from build_harness.workspace import SandboxError


def test_resolve_inside_root_ok(make_workspace):
    ws = make_workspace({"a/b.txt": "x"})
    assert ws.resolve("a/b.txt").is_file()


def test_resolve_escape_blocked(make_workspace):
    ws = make_workspace()
    with pytest.raises(SandboxError):
        ws.resolve("../../etc/passwd")


def test_list_and_read(make_workspace):
    ws = make_workspace({"pkg/m.py": "print(1)\n", "top.txt": "hi\n"})
    files = ws.list_files()
    assert "pkg/m.py" in files and "top.txt" in files
    assert ws.read_file("top.txt") == "hi\n"


def test_search_text(make_workspace):
    ws = make_workspace({"a.py": "alpha\nbeta\n", "b.py": "gamma\n"})
    assert "a.py:2:beta" in ws.search_text("beta")


def test_list_files_ignores_heavy_dirs(make_workspace):
    # .venv/.git/__pycache__ 등은 나열에서 제외돼야 한다 (컨텍스트 폭증 방지)
    ws = make_workspace({
        "src/main.py": "x",
        ".venv/lib/pkg.py": "y",
        "__pycache__/main.pyc": "z",
        ".git/config": "g",
    })
    files = ws.list_files()
    assert "src/main.py" in files
    assert not any(f.startswith((".venv", "__pycache__", ".git")) for f in files)
