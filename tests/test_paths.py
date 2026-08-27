from __future__ import annotations

from koda.tui.paths import index_paths, match_paths
from koda.workspace import Workspace


def test_index_lists_dirs_with_slash_shallow_first_and_skips_ignored(tmp_path):
    (tmp_path / "src" / "koda").mkdir(parents=True)
    (tmp_path / "src" / "koda" / "mcp.py").write_text("x")
    (tmp_path / "README.md").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("x")
    paths = index_paths(Workspace(tmp_path))
    assert paths == ["src/", "README.md", "src/koda/", "src/koda/mcp.py"]


def test_match_ranks_prefix_then_name_then_substring_then_subsequence():
    paths = ["src/", "README.md", "mcps/", "mcps/exa.json", "src/koda/mcp.py", "src/koda/tui/model.py", "tests/test_mcp.py"]
    assert match_paths("", paths, limit=5) == paths[:5]
    assert match_paths("mc", paths)[:2] == ["mcps/", "mcps/exa.json"]                     # 경로 접두가 먼저
    assert match_paths("mcp.py", paths) == ["src/koda/mcp.py", "tests/test_mcp.py"]        # 파일명 접두 > 부분 문자열
    assert "src/koda/tui/model.py" in match_paths("skm", paths)                           # 순서 부분열
    assert match_paths("zzz", paths) == []
    assert len(match_paths("s", paths)) == 5                                              # 상한 5
