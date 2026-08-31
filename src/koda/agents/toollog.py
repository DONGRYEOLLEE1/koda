from __future__ import annotations

import re

from langchain_core.callbacks import BaseCallbackHandler

from ..artifacts import redact

_ARG_LIMIT = 200      # 명령 한 줄이 잘리면 검증자가 '확인 명령이 없다'고 오판한다
_OUT_LIMIT = 160      # 도구 출력 요약 한 건
_BLOCK_LIMIT = 2000   # 증거 블록 전체
_EXIT = re.compile(r"^exit_code=(\d+)\n")


def _cut(text: str, limit: int) -> str:
    return text[:limit] + "…" if len(text) > limit else text


def _brief(name: str, args) -> str:
    """도구 인자 한 줄 요약. 검증 증거로 쓰이므로 무엇을 대상으로 했는지 잘리지 않게 남긴다."""
    args = args if isinstance(args, dict) else {}
    if name in ("run_command", "run_readonly_command"):
        argv = args.get("argv")
        cmd = args.get("command") or (" ".join(map(str, argv)) if isinstance(argv, list) else "")
        return _cut(" ".join(str(cmd).split()), _ARG_LIMIT)
    for key in ("path", "query", "question"):
        if args.get(key):
            return _cut(str(args[key]), _ARG_LIMIT)
    return ""


def _outcome(output) -> str:
    """도구 출력 요약. 파일 밖 부수효과(프로세스 종료·서비스 기동)는 이 출력이 유일한 증거다."""
    content = getattr(output, "content", output)
    text = content if isinstance(content, str) else str(content)
    text = redact(text)[0]
    prefix = ""
    if m := _EXIT.match(text):
        prefix, text = f"exit_code={m.group(1)} · ", text[m.end():]
    lines = [" ".join(ln.split()) for ln in text.splitlines()
             if ln.strip() and ln.strip() not in ("stdout:", "stderr:")]
    return prefix + _cut(" / ".join(lines[:3]), _OUT_LIMIT) if lines else (prefix or "(출력 없음)")


class ToolLog(BaseCallbackHandler):
    """이 턴에서 실제로 실행된 도구와 그 출력을 기록한다. 검증 증거와 '도구 0회' 게이트가 이 기록을 쓴다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool, str]] = []   # (도구, 인자, 성공 여부, 출력 요약)
        self._pending: dict[str, tuple[str, str]] = {}

    def on_tool_start(self, serialized, input_str, *, run_id=None, inputs=None, **kwargs) -> None:
        name = (serialized or {}).get("name") or "tool"
        self._pending[str(run_id)] = (name, _brief(name, inputs))

    def on_tool_end(self, output, *, run_id=None, **kwargs) -> None:
        if entry := self._pending.pop(str(run_id), None):
            self.calls.append((*entry, True, _outcome(output)))

    def on_tool_error(self, error, *, run_id=None, **kwargs) -> None:
        if entry := self._pending.pop(str(run_id), None):
            self.calls.append((*entry, False, f"{type(error).__name__}: {error}"[:_OUT_LIMIT]))

    def summary(self) -> str:
        """'run_command×5 · edit_file×2' 꼴 집계. 히스토리에 남길 한 줄."""
        counts: dict[str, int] = {}
        for call in self.calls:
            counts[call[0]] = counts.get(call[0], 0) + 1
        return " · ".join(f"{n}×{c}" if c > 1 else n for n, c in counts.items())

    def evidence(self) -> str:
        """검증자에게 줄 관측 기록. 명령과 그 출력이 함께 있어야 수행 여부를 판정할 수 있다."""
        if not self.calls:
            return "(없음: 이 턴에서 실행된 도구가 하나도 없다)"
        lines = [f"- {name}({arg})\n  → {'성공' if ok else '실패'}: {out}" for name, arg, ok, out in self.calls]
        block = "\n".join(lines)
        return block if len(block) <= _BLOCK_LIMIT else block[:_BLOCK_LIMIT] + "\n  …(이하 생략)"
