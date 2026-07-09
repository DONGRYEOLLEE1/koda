"""MarkdownStreamRenderer — 본문 단일 출력 불변식 + 재시도 버퍼 리셋 (plan/MARKDOWN_STREAMING.md §6.4, §7)."""
from __future__ import annotations

from koda.cli import MarkdownStreamRenderer, _format_phase


class _FakeLive:
    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.updates = []

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def update(self, renderable):
        self.updates.append(renderable)

    def refresh(self):
        pass


class _FakeConsole:
    def __init__(self):
        self.prints = []

    def print(self, *args, **kwargs):
        self.prints.append(args)


def _rich_renderer():
    r = MarkdownStreamRenderer(use_rich=True)
    r._live, r._console = _FakeLive(), _FakeConsole()
    return r


def _md_prints(r):
    """console 출력 중 본문(Markdown 렌더링)만 — phase 라벨(str) 출력과 구분."""
    return [p for p in r._console.prints if type(p[0]).__name__ == "Markdown"]


# --- 본문 단일 출력 불변식 (§6.4) ---

def test_plain_mode_streams_once_finalize_no_reprint(capsys):
    with MarkdownStreamRenderer(use_rich=False) as r:
        r.on_token("hello ")
        r.on_token("world")
        r.finalize("hello world")
    out = capsys.readouterr().out
    assert out == "hello world"  # on_token 1회 통과 출력, finalize 재출력 없음


def test_rich_mode_finalize_prints_rendered_body_exactly_once():
    r = _rich_renderer()
    with r:
        r.on_phase("edit")  # phase 라벨 출력은 본문 카운트와 무관해야 한다
        for chunk in ("# 제목\n\n", "본문 ", "텍스트"):
            r.on_token(chunk)
        r.on_phase("tool:write_file")
        r.finalize("# 제목\n\n본문 텍스트")
    # context 종료(__exit__) 후에도 본문 출력은 정확히 1회 — 3중 출력 회귀 방지
    body = _md_prints(r)
    assert len(body) == 1
    assert body[0][0].markup == "# 제목\n\n본문 텍스트"
    assert r._live.stopped >= 1  # transient Live 는 중간 프레임을 지우고 종료


def test_rich_mode_exit_without_finalize_prints_nothing():
    r = _rich_renderer()
    with r:
        r.on_token("스트리밍 중 예외로 finalize 미도달")
    assert _md_prints(r) == []  # __exit__ 은 stop-only, 출력 지점이 아니다


def test_rich_mode_empty_buffer_finalize_prints_nothing():
    r = _rich_renderer()
    with r:
        r.finalize("")
    assert _md_prints(r) == []


def test_rich_phase_prints_via_live_console():
    # phase 라벨은 Live 가 소유한 같은 console 로 — 별도 stderr Console 은 Live 커서
    # 추적을 깨 화면이 깨진다 (폴백 리뷰 Medium)
    r = _rich_renderer()
    with r:
        r.on_phase("plan")
        r.finalize(None)
    labels = [p[0] for p in r._console.prints if isinstance(p[0], str)]
    assert labels == ["[📋 계획 수립…]"]


# --- 재시도 버퍼 리셋 (§2.1 on_phase) ---

def test_retry_phase_resets_buffer():
    r = _rich_renderer()
    with r:
        r.on_token("1차 시도 부분 출력")
        r.on_phase("retry:no_content")
        r.on_token("2차 최종 답변")
        r.finalize(None)  # answer 미제공 → 버퍼가 정본
    assert _md_prints(r)[0][0].markup == "2차 최종 답변"  # 1차 부분 출력이 남지 않음


def test_paragraph_boundary_split_across_chunks_triggers_render():
    # \n\n 이 청크 2개에 걸쳐 쪼개져도 문단 경계로 검출된다 (deepseek 리뷰)
    r = _rich_renderer()
    with r:
        r.on_token("줄1\n")
        assert r._live.updates == []  # 4자·경계 미완성 → 미갱신
        r.on_token("\n줄2")
        assert len(r._live.updates) == 1  # 버퍼 꼬리에서 \n\n 검출 → 즉시 갱신
        r.finalize(None)


def test_finalize_answer_overrides_buffer():
    r = _rich_renderer()
    with r:
        r.on_token("유실된 부분 스트림")
        r.finalize("최종 정본 답변")
    assert _md_prints(r)[0][0].markup == "최종 정본 답변"


# --- phase 표시 (plain 모드 stderr 하위 호환 + heartbeat 통합) ---

def test_plain_phase_goes_to_stderr_not_stdout(capsys):
    with MarkdownStreamRenderer(use_rich=False) as r:
        r.on_phase("route")
        r.on_phase("tool:read_file")
        r.on_phase("heartbeat:30s")
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "[🔎 라우팅…]" in cap.err
    assert "· 도구 실행: read_file" in cap.err
    assert "· 진행 중… (30s 경과)" in cap.err


def test_format_phase_known_and_unknown():
    assert _format_phase("plan") == "[📋 계획 수립…]"
    assert _format_phase("retry:length_truncation") == "· 빈 응답(length_truncation) — 변형 재시도"
    assert _format_phase("mcp:서버 연결") == "· 서버 연결"
    assert _format_phase("plan:실패(계획 없이 진행)") == "· plan:실패(계획 없이 진행)"


# --- reasoning 진행 표시 (사용자 실측 피드백: thinking 무출력 구간이 죽은 화면으로 보임) ---

def test_think_phase_updates_status_only_no_persistent_label():
    r = _rich_renderer()
    with r:
        r.on_phase("read")
        r.on_phase("think:120")
        labels = [p[0] for p in r._console.prints if isinstance(p[0], str)]
        assert labels == ["[📖 읽는 중…]"]  # think 는 영구 라벨을 찍지 않는다 (스팸 방지)
        assert "생각 중" in r._status and "120" in r._status
        assert len(r._live.updates) >= 2  # status line 은 즉시 갱신됐다
        r.on_token("본문 생성이 시작되면 표시가 해제된다")  # 20자+ → 렌더
        assert r._status == "[📖 읽는 중…]"  # phase 라벨로 복귀
        r.finalize(None)


def test_plain_mode_silences_think(capsys):
    with MarkdownStreamRenderer(use_rich=False) as r:
        r.on_phase("think:50")
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == ""  # 파이프 로그 오염 금지 — 하트비트가 생존 신호 담당
