from __future__ import annotations

import re

from langchain_core.callbacks import BaseCallbackHandler

from .model import DIFF_TOOLS, ReasoningEnd, ToolEnd, ToolStart

_OUTPUT_LIMIT = 2000  # 툴 output 표시 상한 (긴 파일 내용·셸 출력 절단)
_REASONING_LIMIT = 6000  # 사고 내용 표시 상한. 어려운 문제는 8k 자를 넘기도 한다
_EDIT_OUTPUT_LIMIT = 6000  # 편집 도구 diff 는 길 수 있어 별도(더 큰) 상한 — 기본 상한이 diff 를 반토막 내는 것 방지.

# 자격증명 대입으로 보이는 줄의 값만 가려 시크릿이 스크롤백에 평문으로 남지 않게 한다. 선택적 diff 마커([+\-])를 허용해 diff 의 +/- 접두 라인도 마스킹한다.
_SECRET_LINE = re.compile(
    r"(?im)^([+\-]?\s*[\w.\-]*(?:token|secret|password|authorization|credential|api[_-]?key)[\w.\-]*\s*[=:]\s*)(\S+)"
)


def _redact(text: str) -> str:
    return _SECRET_LINE.sub(r"\1[REDACTED]", text)


def _trim(text: str, limit: int = _OUTPUT_LIMIT) -> str:
    return text[:limit] + " …(생략)" if len(text) > limit else text


def tool_title(name: str, args: dict) -> str:
    """도구 + 완성된 args → 표시용 제목. 결정론으로 만들며, 예상 키가 없으면 도구 이름으로 폴백한다."""
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
    if name == "multi_edit":
        return f"다중수정 · {path}" if path else "다중 수정"
    if name == "patch_file":
        return f"패치 · {path}" if path else "라인 패치"
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
    """툴 실행을 관측해 TUI 로 post 한다. run_id 로 start↔end 를 매칭하며, post 는 thread-safe 하다."""

    def __init__(self, post) -> None:
        self._post = post
        self._names: dict[str, str] = {}  # run_id → 도구명. on_tool_end 의 trim 상한·diff 렌더 분기에 쓴다.

    def on_tool_start(self, serialized, input_str, *, run_id=None, inputs=None, **kwargs) -> None:
        name = (serialized or {}).get("name") or "tool"
        self._names[str(run_id)] = name
        args = inputs if isinstance(inputs, dict) else {}
        self._post(ToolStart(str(run_id), name, tool_title(name, args)))

    def on_tool_end(self, output, *, run_id=None, **kwargs) -> None:
        name = self._names.pop(str(run_id), "")
        limit = _EDIT_OUTPUT_LIMIT if name in DIFF_TOOLS else _OUTPUT_LIMIT
        self._post(ToolEnd(str(run_id), _trim(_redact(_to_text(output)), limit), ok=True, name=name))

    def on_llm_end(self, response, *, run_id=None, **kwargs) -> None:
        # 스트리밍이어도 여기엔 청크가 합쳐진 메시지가 온다. reasoning_content 는 서버가 본문과 따로 주는 사고 내용.
        for gens in getattr(response, "generations", None) or []:
            for g in gens:
                msg = getattr(g, "message", None)
                text = (getattr(msg, "additional_kwargs", None) or {}).get("reasoning_content")
                if text and text.strip():
                    self._post(ReasoningEnd(str(run_id), _trim(_redact(text.strip()), _REASONING_LIMIT)))

    def on_tool_error(self, error, *, run_id=None, **kwargs) -> None:
        name = self._names.pop(str(run_id), "")
        self._post(ToolEnd(str(run_id), f"오류: {type(error).__name__}: {error}", ok=False, name=name))


def _to_text(output) -> str:
    """ToolMessage 나 원시 반환값에서 표시 텍스트를 뽑는다."""
    content = getattr(output, "content", output)
    return content if isinstance(content, str) else str(content)
