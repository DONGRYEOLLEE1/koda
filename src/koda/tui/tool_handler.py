"""langchain 콜백 핸들러: 툴 실행 시작/종료를 TUI 메시지(ToolStart/ToolEnd)로 변환한다.

**설계 핵심(plan/TUI_UI_OVERHAUL.md)**: astream_agent·graph 노드·run_task 의 스트리밍 로직을
건드리지 않는다. koda 는 이미 langfuse CallbackHandler 를 config.callbacks 로 넘겨 모든 툴 호출
입출력을 관측한다 — 툴 렌더링도 같은 채널에 얹는다. TUI 전용이므로 CLI(run/chat)는 이 핸들러를
넘기지 않고, 따라서 툴 output 비노출 동작이 그대로 유지된다.

제목은 별도 LLM 호출 없이 **도구별 템플릿**을 완성된 tool input(args)으로 만든다(모델 부담 0).
"""
from __future__ import annotations

import re

from langchain_core.callbacks import BaseCallbackHandler

from .model import ToolEnd, ToolStart

_OUTPUT_LIMIT = 2000  # 툴 output 표시 상한 (긴 파일 내용·셸 출력 절단)

# `KEY=value`·`token: ...` 처럼 자격증명 대입으로 보이는 줄의 값만 가린다. read_file 로 .env 를
# 열거나 셸 출력에 시크릿이 섞여도 스크롤백에 평문으로 남지 않게 한다(승인 modal redaction 과 일관).
# 값 부분만 치환하므로 일반 코드/산문은 건드리지 않는다.
_SECRET_LINE = re.compile(
    r"(?im)^(\s*[\w.\-]*(?:token|secret|password|authorization|credential|api[_-]?key)[\w.\-]*\s*[=:]\s*)(\S+)"
)


def _redact(text: str) -> str:
    return _SECRET_LINE.sub(r"\1[REDACTED]", text)


def _trim(text: str) -> str:
    return text[:_OUTPUT_LIMIT] + " …(생략)" if len(text) > _OUTPUT_LIMIT else text


def tool_title(name: str, args: dict) -> str:
    """도구 + 완성된 args → 표시용 제목. 모델이 아니라 koda 가 결정론으로 만든다.

    args 가 비거나 예상 키가 없으면 도구 이름으로 폴백 — UI 에 빈 헤더가 나오지 않게.
    """
    args = args if isinstance(args, dict) else {}
    short = name
    if name in ("run_command", "run_readonly_command"):
        argv = args.get("argv")
        cmd = args.get("command") or (" ".join(map(str, argv)) if isinstance(argv, list) else "")
        return f"$ {cmd}".strip() if cmd else "$ (명령)"
    path = args.get("path")
    if name == "read_file":
        return f"읽기 · {path}" if path else "파일 읽기"
    if name == "search_text":
        q = args.get("query")
        return f"검색 · {q}" if q else "텍스트 검색"
    if name == "list_files":
        return f"목록 · {path}" if path and path != "." else "파일 목록"
    if name == "edit_file":
        return f"수정 · {path}" if path else "파일 수정"
    if name == "write_file":
        return f"쓰기 · {path}" if path else "파일 쓰기"
    if name == "create_dir":
        return f"디렉토리 · {path}" if path else "디렉토리 생성"
    if name == "delete_path":
        return f"삭제 · {path}" if path else "삭제"
    if name == "explore":
        return "탐색 위임"
    return short  # MCP·기타 도구는 이름 그대로


class ToolActivityHandler(BaseCallbackHandler):
    """툴 실행을 관측해 TUI 로 post 한다. run_id(툴 실행 단위 UUID)로 start↔end 를 매칭한다.

    post 는 app.post_message(thread-safe) — 콜백은 worker/executor thread 에서 발화하지만
    runner 의 기존 메시지 계약과 동일하게 안전하다.
    """

    def __init__(self, post) -> None:
        self._post = post

    def on_tool_start(self, serialized, input_str, *, run_id=None, inputs=None, **kwargs) -> None:
        name = (serialized or {}).get("name") or "tool"
        args = inputs if isinstance(inputs, dict) else {}
        self._post(ToolStart(str(run_id), name, tool_title(name, args)))

    def on_tool_end(self, output, *, run_id=None, **kwargs) -> None:
        self._post(ToolEnd(str(run_id), _trim(_redact(_to_text(output))), ok=True))

    def on_tool_error(self, error, *, run_id=None, **kwargs) -> None:
        self._post(ToolEnd(str(run_id), f"오류: {type(error).__name__}: {error}", ok=False))


def _to_text(output) -> str:
    """ToolMessage 나 원시 반환값에서 표시 텍스트를 뽑는다."""
    content = getattr(output, "content", output)
    return content if isinstance(content, str) else str(content)
