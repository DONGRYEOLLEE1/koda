from __future__ import annotations

import pytest

from koda.memory import KINDS, MAX_CHARS, Memory, parse_remember


def test_read_empty_when_missing(tmp_path):
    assert Memory(tmp_path).read() == ""


def test_remember_by_kind_and_injection_layout(tmp_path):
    m = Memory(tmp_path)
    assert m.remember("이 프로젝트는 pytest 를 쓴다") is True                      # 기본 semantic
    assert m.remember("2026-08-26 calc.py 에 sub 추가, 테스트 통과", "episodic") is True
    assert m.remember("함수명은 snake_case", "procedural") is True
    assert m.items() == ["이 프로젝트는 pytest 를 쓴다"]
    assert m.items("procedural") == ["함수명은 snake_case"]
    text = m.read()
    # 주입 본문은 유형별 소제목으로 나뉘고, 비어 있지 않은 유형만 실린다
    assert text.index("### 사실 (semantic)") < text.index("### 경험 (episodic)") < text.index("### 규칙 (procedural)")
    assert (tmp_path / ".koda" / "memory" / "procedural.md").read_text(encoding="utf-8") == "- 함수명은 snake_case\n"


def test_remember_is_idempotent_per_kind(tmp_path):
    m = Memory(tmp_path)
    m.remember("같은 사실")
    assert m.remember("  같은   사실 ") is False        # 공백 정규화 후 동일 취급
    assert m.remember("같은 사실", "procedural") is True  # 유형이 다르면 별개 항목
    assert m.remember("   ") is False


def test_legacy_single_file_reads_as_semantic_and_migrates(tmp_path):
    legacy = tmp_path / ".koda" / "memory.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("- 예전 항목\n", encoding="utf-8")
    m = Memory(tmp_path)
    assert m.items() == ["예전 항목"] and "### 사실 (semantic)" in m.read()
    m.remember("새 항목")                                   # 첫 저장 때 새 파일로 옮겨 간다
    assert m.items() == ["예전 항목", "새 항목"]
    assert (tmp_path / ".koda" / "memory" / "semantic.md").exists()


def test_needs_compaction_per_kind(tmp_path):
    m = Memory(tmp_path)
    m.remember("x" * (MAX_CHARS + 10), "episodic")
    assert m.needs_compaction("episodic") is True and m.needs_compaction("semantic") is False
    m.replace("episodic", "- 요약됨")
    assert m.needs_compaction("episodic") is False and m.items("episodic") == ["요약됨"]


def test_invalid_kind_rejected(tmp_path):
    with pytest.raises(ValueError):
        Memory(tmp_path).remember("x", "working")


def test_parse_remember_kind_prefix():
    assert parse_remember("procedural 함수명은 snake_case") == ("procedural", "함수명은 snake_case")
    assert parse_remember("함수명은 snake_case") == ("semantic", "함수명은 snake_case")
    assert parse_remember("episodic") == ("semantic", "episodic")     # 내용 없는 유형어는 그냥 내용
    assert all(parse_remember(f"{k} 내용")[0] == k for k in KINDS)
