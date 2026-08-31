from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler

_ARG_LIMIT = 60


def _brief(name: str, args) -> str:
    """도구 인자 한 줄 요약. 검증 증거로 쓰이므로 무엇을 대상으로 했는지만 남긴다."""
    args = args if isinstance(args, dict) else {}
    if name in ("run_command", "run_readonly_command"):
        argv = args.get("argv")
        cmd = args.get("command") or (" ".join(map(str, argv)) if isinstance(argv, list) else "")
        return str(cmd)[:_ARG_LIMIT]
    for key in ("path", "query", "question"):
        if args.get(key):
            return str(args[key])[:_ARG_LIMIT]
    return ""


class ToolLog(BaseCallbackHandler):
    """이 턴에서 실제로 실행된 도구를 기록한다. 검증 증거와 '도구 0회' 게이트가 이 기록을 쓴다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []   # (도구, 인자 요약, 성공 여부)
        self._pending: dict[str, tuple[str, str]] = {}

    def on_tool_start(self, serialized, input_str, *, run_id=None, inputs=None, **kwargs) -> None:
        name = (serialized or {}).get("name") or "tool"
        self._pending[str(run_id)] = (name, _brief(name, inputs))

    def on_tool_end(self, output, *, run_id=None, **kwargs) -> None:
        if entry := self._pending.pop(str(run_id), None):
            self.calls.append((*entry, True))

    def on_tool_error(self, error, *, run_id=None, **kwargs) -> None:
        if entry := self._pending.pop(str(run_id), None):
            self.calls.append((*entry, False))

    def summary(self) -> str:
        """'run_command×5 · edit_file×2' 꼴 집계. 히스토리에 남길 한 줄."""
        counts: dict[str, int] = {}
        for name, _arg, _ok in self.calls:
            counts[name] = counts.get(name, 0) + 1
        return " · ".join(f"{n}×{c}" if c > 1 else n for n, c in counts.items())

    def evidence(self) -> str:
        """검증자에게 줄 관측 기록. 도구를 하나도 안 썼으면 그 사실을 명시한다."""
        if not self.calls:
            return "(없음: 이 턴에서 실행된 도구가 하나도 없다)"
        return "\n".join(f"- {name}({arg}) → {'성공' if ok else '실패'}" for name, arg, ok in self.calls)
