from __future__ import annotations

import re
from typing import NamedTuple

from langchain_core.callbacks import BaseCallbackHandler

from ..artifacts import redact

_ARG_LIMIT = 200      # 명령 한 줄이 잘리면 검증자가 '확인 명령이 없다'고 오판한다
_OUT_LIMIT = 160      # 도구 출력 요약 한 건
_BLOCK_LIMIT = 2000   # 증거 블록 전체
_HEAD_LIMIT = 200             # first output line of a screen tool call in the timeline
_SCREEN_OUT_LIMIT = 600       # the last screen_diff output is kept whole for the verifier
_SCREEN_TIMELINE_LIMIT = 30
_ERROR_PREFIX = "오류:"       # every tool reports a failure as this string instead of raising
_EXIT = re.compile(r"^exit_code=(\d+)\n")
_RENDERS_DIR = ".koda/renders/"
_RENDER_HEAD = re.compile(r"렌더 완료 · (\S+\.png)")   # the path on the first line is the render's canonical name
_RENDER_SUFFIXES = (".parse.json", ".dom.json", ".meta.json", ".png")

SCREEN_TOOLS = ("render_page", "parse_screen", "screen_diff")
EDIT_TOOLS = ("write_file", "edit_file", "multi_edit", "patch_file", "delete_path", "create_dir")


def _cut(text: str, limit: int) -> str:
    return text[:limit] + "…" if len(text) > limit else text


def _brief(name: str, args) -> str:
    """One-line argument summary. It is verification evidence, so the target must survive uncut."""
    args = args if isinstance(args, dict) else {}
    if name in ("run_command", "run_readonly_command"):
        argv = args.get("argv")
        cmd = args.get("command") or (" ".join(map(str, argv)) if isinstance(argv, list) else "")
        return _cut(" ".join(str(cmd).split()), _ARG_LIMIT)
    target = ""
    for key in ("path", "target", "query", "question"):
        if args.get(key):
            target = str(args[key])
            break
    if not target and (args.get("before") or args.get("after")):
        target = f"{args.get('before') or ''} vs {args.get('after') or ''}"
    if target and args.get("name"):
        target += f" → {args['name']}"
    return _cut(target, _ARG_LIMIT)


def _text(output) -> str:
    content = getattr(output, "content", output)
    text = content if isinstance(content, str) else str(content)
    return redact(text)[0]


def _outcome(text: str) -> str:
    """Tool output summary. For side effects outside files (killed processes, started services) this is the only evidence."""
    prefix = ""
    if m := _EXIT.match(text):
        prefix, text = f"exit_code={m.group(1)} · ", text[m.end():]
    lines = [" ".join(ln.split()) for ln in text.splitlines()
             if ln.strip() and ln.strip() not in ("stdout:", "stderr:")]
    return prefix + _cut(" / ".join(lines[:3]), _OUT_LIMIT) if lines else (prefix or "(출력 없음)")


def _is_error(text: str) -> bool:
    return text.lstrip().startswith(_ERROR_PREFIX)


def _head(text: str) -> str:
    first = (text.strip().splitlines() or [""])[0]
    return _cut(" ".join(first.split()), _HEAD_LIMIT)


def _render_stem(path: str) -> str:
    """'.koda/renders/before.png', '.koda/renders/before.dom.json' -> 'before'. Not a render: ''."""
    path = path.strip()
    if not path.startswith(_RENDERS_DIR):
        return ""
    base = path[len(_RENDERS_DIR):]
    for suffix in _RENDER_SUFFIXES:
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return ""


class ScreenCall(NamedTuple):
    """One screen tool call in the timeline. ordinal is the call's position among every tool call of the turn."""

    ordinal: int
    name: str
    arg: str
    ok: bool
    head: str
    output: str


class ToolLog(BaseCallbackHandler):
    """Records the tools that actually ran this turn and their output. The verifier's evidence and the no-tool gate read it.

    calls holds every call in completion order as (tool, args, ok, output summary); a call's ordinal is
    its 1-based position in that list. screen_calls keeps the screen tools apart with their full output.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool, str]] = []
        self.screen_calls: list[ScreenCall] = []
        self._pending: dict[str, tuple[str, str]] = {}

    def on_tool_start(self, serialized, input_str, *, run_id=None, inputs=None, **kwargs) -> None:
        name = (serialized or {}).get("name") or "tool"
        self._pending[str(run_id)] = (name, _brief(name, inputs))

    def on_tool_end(self, output, *, run_id=None, **kwargs) -> None:
        if entry := self._pending.pop(str(run_id), None):
            text = _text(output)
            self._record(entry, not _is_error(text), _outcome(text), text)

    def on_tool_error(self, error, *, run_id=None, **kwargs) -> None:
        if entry := self._pending.pop(str(run_id), None):
            message = f"{type(error).__name__}: {error}"
            self._record(entry, False, message[:_OUT_LIMIT], message)

    def _record(self, entry: tuple[str, str], ok: bool, summary: str, text: str) -> None:
        name, arg = entry
        self.calls.append((name, arg, ok, summary))
        if name in SCREEN_TOOLS:
            self.screen_calls.append(
                ScreenCall(len(self.calls), name, arg, ok, _head(text), _cut(text.strip(), _SCREEN_OUT_LIMIT)))

    @property
    def first_edit_ordinal(self) -> int:
        """Ordinal of the first edit tool call, 0 when the turn edited nothing."""
        return next((i for i, call in enumerate(self.calls, 1) if call[0] in EDIT_TOOLS), 0)

    def summary(self) -> str:
        """'run_command×5 · edit_file×2' 꼴 집계. 히스토리에 남길 한 줄."""
        counts: dict[str, int] = {}
        for call in self.calls:
            counts[call[0]] = counts.get(call[0], 0) + 1
        return " · ".join(f"{n}×{c}" if c > 1 else n for n, c in counts.items())

    def evidence(self) -> str:
        """The observation record for the verifier: each command with its output, then the screen evidence block."""
        if not self.calls:
            block = "(없음: 이 턴에서 실행된 도구가 하나도 없다)"
        else:
            lines = [f"- #{i} {name}({arg})\n  → {'성공' if ok else '실패'}: {out}"
                     for i, (name, arg, ok, out) in enumerate(self.calls, 1)]
            block = "\n".join(lines)
            if len(block) > _BLOCK_LIMIT:
                block = block[:_BLOCK_LIMIT] + "\n  …(이하 생략)"
        return block + "\n\n" + self.screen_evidence()

    def screen_evidence(self) -> str:
        """The [화면 증거] block: counts, whether a baseline render preceded the first edit, the timeline, and the diffs."""
        lines = ["[화면 증거]"]
        if not self.screen_calls:
            lines.append("(없음: 이 턴에서 화면 도구 호출이 없다)")
            return "\n".join(lines)
        counts = {name: [0, 0] for name in SCREEN_TOOLS}
        for call in self.screen_calls:
            counts[call.name][0 if call.ok else 1] += 1
        lines.append(" · ".join(f"{n} 성공 {counts[n][0]}/실패 {counts[n][1]}" for n in SCREEN_TOOLS))
        first_edit = self.first_edit_ordinal
        if not first_edit:
            lines.append("기준 렌더: 편집 없음")
        else:
            lines.append(self._baseline_line(first_edit))
        for call in self.screen_calls[:_SCREEN_TIMELINE_LIMIT]:
            lines.append(f"#{call.ordinal} {call.name}({call.arg}) {'성공' if call.ok else '실패'}: {call.head}")
        if len(self.screen_calls) > _SCREEN_TIMELINE_LIMIT:
            lines.append(f"... 외 {len(self.screen_calls) - _SCREEN_TIMELINE_LIMIT}건")
        diffs = [c for c in self.screen_calls if c.name == "screen_diff"]
        if diffs:
            lines.append(f"첫 screen_diff(#{diffs[0].ordinal}): {diffs[0].head}")
            lines.append(f"마지막 screen_diff(#{diffs[-1].ordinal}) 전문:")
            lines.append(diffs[-1].output)
        return "\n".join(lines)

    def _baseline_line(self, first_edit: int) -> str:
        """Whether the render the last screen_diff compared against was taken before the first edit.

        The baseline is the last successful render_page, before that diff, whose output names the
        diff's `before` render. A render of the same name taken after the first edit overwrites the
        files, so an earlier one no longer counts. Without a diff on a render, any successful
        render_page before the first edit is reported.
        """
        renders = [c for c in self.screen_calls if c.name == "render_page" and c.ok]
        diffs = [c for c in self.screen_calls if c.name == "screen_diff"]
        prefix = f"기준 렌더: 첫 편집(#{first_edit}) 이전 render_page 호출 "
        stem = _render_stem(diffs[-1].arg.split(" vs ")[0]) if diffs else ""
        if stem:
            same = [c for c in renders if c.ordinal < diffs[-1].ordinal
                    and _render_stem((_RENDER_HEAD.search(c.head) or [None, ""])[1]) == stem]
            if not same:
                return prefix + "없음"
            if same[-1].ordinal < first_edit:
                return prefix + f"있음(#{same[-1].ordinal})"
            return prefix + f"없음(비교 기준으로 쓴 렌더가 첫 편집 뒤의 #{same[-1].ordinal})"
        baseline = next((c for c in renders if c.ordinal < first_edit), None)
        return prefix + (f"있음(#{baseline.ordinal})" if baseline else "없음")
