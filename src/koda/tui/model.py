from __future__ import annotations

from typing import Callable

from textual.message import Message

# 출력이 unified diff 훅인 편집 도구 — tool_handler(별도 trim 상한)·widgets(diff 색상 렌더)의 단일 출처.
DIFF_TOOLS = frozenset({"edit_file", "multi_edit", "patch_file"})


class TokenChunk(Message):
    """답변 본문 토큰 (astream_agent on_token 통과분)."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class PhaseEvent(Message):
    """단계/도구/think/heartbeat 이벤트 (astream_agent·graph on_phase 통과분)."""

    def __init__(self, phase: str) -> None:
        self.phase = phase
        super().__init__()


class ApprovalRequest(Message):
    """위험 도구 승인 요청. resolve(bool) 이 threading.Event 를 풀어 worker 를 재개한다."""

    def __init__(self, tool: str, args: object, resolve: Callable[[bool], None]) -> None:
        self.tool = tool
        self.args = args
        self.resolve = resolve
        super().__init__()


class TurnDone(Message):
    """턴 종료. result 는 run_task 반환 dict 그대로, turns 는 세션 누적 턴 수."""

    def __init__(self, result: dict, turns: int) -> None:
        self.result = result
        self.turns = turns
        super().__init__()


class TurnFailed(Message):
    """턴 실행 예외 (chat 의 '턴 실패: ...' 와 동등)."""

    def __init__(self, error: str) -> None:
        self.error = error
        super().__init__()


class SessionClosed(Message):
    """세션 저장·메모리 추출 완료 — 앱 종료 신호."""

    def __init__(self, session_id: str, saved: bool) -> None:
        self.session_id = session_id
        self.saved = saved
        super().__init__()


class CompactDone(Message):
    """/compact 슬래시 커맨드 완료 — 압축 결과 안내."""

    def __init__(self, note: str) -> None:
        self.note = note
        super().__init__()


class ToolStart(Message):
    """툴 실행 시작 (langchain on_tool_start). run_id 로 ToolEnd 와 매칭하며, raw args 는 싣지 않는 표시 전용 채널이다."""

    def __init__(self, run_id: str, name: str, title: str) -> None:
        self.run_id = run_id
        self.name = name
        self.title = title
        super().__init__()


class ToolEnd(Message):
    """툴 실행 종료. output 은 redaction+길이 절단이 끝난 표시용 문자열, ok 는 성공 여부, name 은 도구 이름(diff 렌더 분기용)."""

    def __init__(self, run_id: str, output: str, ok: bool = True, name: str = "") -> None:
        self.run_id = run_id
        self.output = output
        self.ok = ok
        self.name = name
        super().__init__()


class ReasoningEnd(Message):
    """LLM 호출 하나가 끝났을 때의 사고 내용(reasoning_content). 스트리밍하지 않고 완료 후 한 번에 싣는다."""

    def __init__(self, run_id: str, text: str) -> None:
        self.run_id = run_id
        self.text = text
        super().__init__()


class MemorySaved(Message):
    """턴 완료 후 메모리 판정으로 항목이 저장됨 (유형, 항목)."""

    def __init__(self, kind: str, note: str) -> None:
        self.kind = kind
        self.note = note
        super().__init__()
