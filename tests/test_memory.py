"""핵심 모듈: memory (영구 메모리). 멱등성·상한 신호·주입 형식 검증."""
from __future__ import annotations

from build_harness.memory import MAX_CHARS, Memory


def test_read_empty_when_missing(tmp_path):
    assert Memory(tmp_path).read() == ""


def test_remember_appends_bullet(tmp_path):
    m = Memory(tmp_path)
    assert m.remember("이 프로젝트는 pytest 를 쓴다") is True
    assert m.read() == "- 이 프로젝트는 pytest 를 쓴다"
    assert m.remember("타입힌트는 3.12 스타일") is True
    assert m.read().splitlines() == ["- 이 프로젝트는 pytest 를 쓴다", "- 타입힌트는 3.12 스타일"]


def test_remember_is_idempotent(tmp_path):
    m = Memory(tmp_path)
    m.remember("같은 사실")
    before = m.read()
    assert m.remember("같은 사실") is False    # 중복 저장 안 함
    assert m.remember("  같은   사실 ") is False  # 공백 정규화 후에도 동일 취급
    assert m.read() == before


def test_remember_rejects_empty(tmp_path):
    m = Memory(tmp_path)
    assert m.remember("   ") is False
    assert m.read() == ""


def test_remember_short_note_not_rejected_as_substring(tmp_path):
    # 리뷰 M2: 항목 단위 비교 — 기존 항목의 부분문자열이라도 별개 항목이면 저장돼야 한다
    m = Memory(tmp_path)
    m.remember("이 프로젝트는 pytest 를 쓴다")
    assert m.remember("pytest") is True
    assert m.items() == ["이 프로젝트는 pytest 를 쓴다", "pytest"]


def test_needs_compaction_threshold(tmp_path):
    m = Memory(tmp_path)
    m.remember("x" * (MAX_CHARS + 10))
    assert m.needs_compaction is True
    m.replace("- 요약됨")
    assert m.needs_compaction is False and m.read() == "- 요약됨"
