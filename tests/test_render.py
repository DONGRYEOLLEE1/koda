from __future__ import annotations

from koda.cli import MarkdownStreamRenderer


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


# --- 본문 단일 출력 불변식 ---

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


# --- 재시도 버퍼 리셋(on_phase) ---

def test_retry_phase_resets_buffer():
    r = _rich_renderer()
    with r:
        r.on_token("1차 시도 부분 출력")
        r.on_phase("retry:no_content")
        r.on_token("2차 최종 답변")
        r.finalize(None)  # answer 미제공 → 버퍼가 정본
    assert _md_prints(r)[0][0].markup == "2차 최종 답변"  # 1차 부분 출력이 남지 않음


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


# --- reasoning 진행 표시 (사용자 실측 피드백: thinking 무출력 구간이 죽은 화면으로 보임) ---

def test_plain_mode_silences_think(capsys):
    with MarkdownStreamRenderer(use_rich=False) as r:
        r.on_phase("think:50")
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == ""  # 파이프 로그 오염 금지 — 하트비트가 생존 신호 담당
