from __future__ import annotations

from koda import diffscan
from koda.diffscan import diff_report, snapshot


def test_added_modified_deleted_detected(tmp_path):
    (tmp_path / "keep.txt").write_text("same\n")
    (tmp_path / "mod.txt").write_text("old line\n")
    (tmp_path / "gone.txt").write_text("bye\n")
    before = snapshot(tmp_path)

    (tmp_path / "mod.txt").write_text("new line\n")
    (tmp_path / "gone.txt").unlink()
    (tmp_path / "new.txt").write_text("hello new\n")
    changed, diff = diff_report(before, snapshot(tmp_path))

    assert changed == ["D gone.txt", "M mod.txt", "A new.txt"]  # 경로 알파벳순 (결정론)
    assert "+hello new" in diff      # 신규(untracked) 파일도 본문이 diff 에 나온다
    assert "-bye" in diff            # 삭제 파일은 이전 원문 기준
    assert "-old line" in diff and "+new line" in diff
    assert "keep.txt" not in diff


def test_exclude_dirs_ignored(tmp_path):
    (tmp_path / ".koda" / "artifacts").mkdir(parents=True)
    (tmp_path / ".koda" / "artifacts" / "x.txt").write_text("a")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref")
    assert snapshot(tmp_path) == {}


def test_binary_file_one_line(tmp_path):
    (tmp_path / "img.bin").write_bytes(b"\x00\xff\x00")
    before = snapshot(tmp_path)
    (tmp_path / "img.bin").write_bytes(b"\x00\xff\x01")
    changed, diff = diff_report(before, snapshot(tmp_path))
    assert changed == ["M img.bin"]
    assert "binary/large changed" in diff and "+\x00" not in diff


def test_per_file_and_total_caps(tmp_path, monkeypatch):
    monkeypatch.setattr(diffscan, "MAX_DIFF_LINES_PER_FILE", 10)
    monkeypatch.setattr(diffscan, "MAX_TOTAL_CHARS", 300)
    (tmp_path / "big.txt").write_text("")
    before = snapshot(tmp_path)
    (tmp_path / "big.txt").write_text("\n".join(f"line{i}" for i in range(200)))
    _, diff = diff_report(before, snapshot(tmp_path))
    assert "절단" in diff
    assert len(diff) <= 300 + 40  # 총 상한 + 절단 안내 문구


def test_no_change_empty_report(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    s = snapshot(tmp_path)
    assert diff_report(s, snapshot(tmp_path)) == ([], "")
