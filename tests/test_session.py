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
    assert s.needs_compaction is True and s.old_messages() == []
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


def test_summaries_lists_recent_first_and_skips_broken(tmp_path):
    a = Session(tmp_path, session_id="aaa"); a.add_turn("첫 질문 A", "답", route="read", verdict=None); a.save()
    b = Session(tmp_path, session_id="bbb"); b.add_turn("첫 질문 B", "답", route="read", verdict=None); b.save()
    (tmp_path / ".koda" / "sessions" / "broken.json").write_text("{not json", encoding="utf-8")
    import os, time
    os.utime(a.path, (time.time() + 10, time.time() + 10))   # a 를 더 최근으로
    out = Session.summaries(tmp_path)
    assert [s["id"] for s in out] == ["aaa", "bbb"]           # 최근 수정순, 깨진 파일 제외
    assert out[0]["turns"] == 1 and out[0]["first_task"] == "첫 질문 A"
