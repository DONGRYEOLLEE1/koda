from __future__ import annotations

from koda.session import Session


def test_add_turn_stores_final_answer_only(tmp_path):
    s = Session(tmp_path)
    s.add_turn("파일 만들어줘", "만들었습니다.", route="edit", verdict="pass", summary="a.py 생성")
    assert s.history == [
        {"role": "user", "content": "파일 만들어줘"},
        {"role": "assistant", "content": "만들었습니다."},
    ]
    assert s.turns[0]["route"] == "edit" and s.turns[0]["verdict"] == "pass"


def test_save_load_roundtrip(tmp_path):
    s = Session(tmp_path, session_id="abc123")
    s.add_turn("q1", "a1", route="read", verdict=None)
    p = s.save()
    assert p.name == "abc123.json" and ".koda/sessions" in str(p)

    loaded = Session.load(tmp_path, "abc123")
    assert loaded.history == s.history and loaded.turns == s.turns


def test_save_is_idempotent(tmp_path):
    s = Session(tmp_path, session_id="same")
    s.add_turn("q", "a", route="read", verdict=None)
    s.save()
    first = s.path.read_bytes()
    s.save()  # 같은 상태 재저장 → 파일 내용 동일 (멱등)
    assert s.path.read_bytes() == first


def test_load_then_save_unchanged(tmp_path):
    s = Session(tmp_path, session_id="x1")
    s.add_turn("q", "a", route="edit", verdict="pass")
    s.save()
    before = s.path.read_bytes()
    Session.load(tmp_path, "x1").save()  # 로드→재저장 무변화 (멱등)
    assert s.path.read_bytes() == before


def test_compaction_replaces_old_keeps_recent(tmp_path):
    s = Session(tmp_path)
    for i in range(8):  # 8턴 = 16메시지
        s.add_turn(f"q{i}", f"a{i}", route="read", verdict=None)
    old = s.old_messages()
    assert old and old[-1]["content"] == "a3"     # 최근 4턴(q4~q7)은 압축 대상 아님
    s.apply_compaction("과거 8턴 요약")
    assert "[이전 대화 요약]" in s.history[0]["content"]
    assert s.history[2]["content"] == "q4"        # 최근 구간이 user 로 시작


def test_compaction_noop_when_nothing_old(tmp_path):
    # 거대 단일 턴 — 자를 것이 없으면 합성 요약을 덧붙이지 않는다
    s = Session(tmp_path)
    s.add_turn("q", "x" * 40_000, route="read", verdict=None)
    assert s.needs_compaction() is True and s.old_messages() == []
    before = list(s.history)
    s.apply_compaction("요약")
    assert s.history == before                    # no-op


def test_compaction_preserves_pair_boundary_on_odd_history(tmp_path):
    # 홀수 히스토리(수동 편집된 세션 파일)에서 assistant 고아가 생기지 않는다
    s = Session(tmp_path)
    for i in range(4):
        s.add_turn(f"q{i}", f"a{i}", route="read", verdict=None)
    s.history.append({"role": "user", "content": "q4"})   # 홀수(9개) 상태
    old = s.old_messages()
    if old:  # 잘렸다면 최근 구간 첫 메시지는 반드시 user
        assert s.history[len(old)]["role"] == "user"
    s.apply_compaction("요약")
    kept = [m for m in s.history if "[이전 대화 요약]" not in m["content"]]
    assert kept[1]["role"] == "user" or kept[0]["role"] == "user"


def test_list_ids(tmp_path):
    assert Session.list_ids(tmp_path) == []
    Session(tmp_path, "b2").save()
    Session(tmp_path, "a1").save()
    assert Session.list_ids(tmp_path) == ["a1", "b2"]


def test_summaries_orders_by_last_answer_not_mtime(tmp_path):
    """정렬·시각 표시는 마지막 답변 시각(updated_at) 기준이다. 세션을 다시 저장(전환·이름 변경)해도 순서가 흔들리지 않는다."""
    import os, time
    a = Session(tmp_path, session_id="aaa"); a.add_turn("첫 질문 A", "답", route="read", verdict=None); a.save()
    b = Session(tmp_path, session_id="bbb"); b.add_turn("첫 질문 B", "답", route="read", verdict=None)
    b.updated_at = a.updated_at - 100                          # b 가 더 오래 전에 답변받음
    b.save()
    (tmp_path / ".koda" / "sessions" / "broken.json").write_text("{not json", encoding="utf-8")
    os.utime(b.path, (time.time() + 10, time.time() + 10))     # b 를 나중에 저장(파일 mtime 만 최신)
    out = Session.summaries(tmp_path)
    assert [s["id"] for s in out] == ["aaa", "bbb"]             # mtime 이 아니라 답변 시각 순, 깨진 파일 제외
    assert out[0]["turns"] == 1 and out[0]["first_task"] == "첫 질문 A"
    assert out[0]["answered"] == a.updated_at


def test_updated_at_tracks_answers_and_legacy_falls_back(tmp_path):
    import json
    s = Session(tmp_path, session_id="u1")
    assert s.updated_at == 0.0 and s.created_at > 0            # 대화 전에는 답변 시각이 없다
    s.add_turn("q", "a", route="read", verdict=None)
    first = s.updated_at
    assert first > 0
    s.add_turn("q2", "a2", route="read", verdict=None)
    assert s.updated_at >= first                               # 답변마다 갱신
    s.save()
    assert Session.load(tmp_path, "u1").updated_at == s.updated_at
    # 구버전 파일(시각 필드 없음)은 파일 mtime 으로 대신한다
    (tmp_path / ".koda" / "sessions" / "old.json").write_text(
        json.dumps({"id": "old", "history": [], "turns": [{"task": "q", "route": "read"}]}), encoding="utf-8")
    legacy = next(x for x in Session.summaries(tmp_path) if x["id"] == "old")
    assert legacy["answered"] > 0


def test_title_state_roundtrip_and_delete(tmp_path):
    s = Session(tmp_path, session_id="t1")
    s.title_tries = 1
    s.title_manual = True
    s.add_turn("q", "a", route="read", verdict=None)
    s.save()
    loaded = Session.load(tmp_path, "t1")
    assert loaded.title_tries == 1 and loaded.title_manual is True
    assert Session.delete(tmp_path, "t1") is True
    assert Session.list_ids(tmp_path) == []
    assert Session.delete(tmp_path, "t1") is False   # 없는 세션 삭제는 조용히 False


def test_title_roundtrip_and_summaries(tmp_path):
    s = Session(tmp_path)
    s.title = "calc 뺄셈 추가"
    s.add_turn("calc.py 에 sub 추가", "완료", "edit", "pass")
    s.save()
    assert Session.load(tmp_path, s.id).title == "calc 뺄셈 추가"
    assert Session.summaries(tmp_path)[0]["title"] == "calc 뺄셈 추가"


def test_compact_threshold_scales_with_budget():
    """압축 임계는 컨텍스트 예산에 비례한다. 기본 예산에서는 기존 값 그대로라 동작이 바뀌지 않는다."""
    from koda.config import DEFAULT_CONTEXT_BUDGET
    from koda.session import COMPACT_THRESHOLD_CHARS, compact_threshold
    assert compact_threshold(DEFAULT_CONTEXT_BUDGET) == COMPACT_THRESHOLD_CHARS
    assert compact_threshold(1_000_000) > COMPACT_THRESHOLD_CHARS * 8


def test_needs_compaction_follows_budget(tmp_path):
    """같은 이력이라도 1M 예산에서는 아직 압축 대상이 아니다 — 1M 모델의 멀티턴 맥락이 조기 요약되지 않는다."""
    s = Session(tmp_path)
    s.add_turn("q", "x" * 40_000, route="read", verdict=None)
    assert s.needs_compaction() is True
    assert s.needs_compaction(1_000_000) is False
