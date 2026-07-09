"""파일/폴더 생성·수정·삭제 도구. 위험 작업 — Permission middleware 가 실행 전 게이트한다.

용도: 소스코드/파일 변경. 되돌릴 수 없는 변경이 가능하므로 전부 승인 대상.
경로는 workspace.resolve 로 루트에 샌드박스된다(루트 밖이면 SandboxError).
쓰기는 _atomic_write(temp + os.replace + copymode)로 원자적이며 실행 비트를 보존한다.
"""
from __future__ import annotations

import difflib
import os
import shutil
import tempfile
from pathlib import Path

from langchain.tools import tool

from ..workspace import Workspace

# 승인이 필요한 위험 도구 이름
DANGEROUS_TOOLS = {
    "write_file", "edit_file", "multi_edit", "patch_file",
    "create_dir", "delete_path",
}

ENCODING = "utf-8"


def _atomic_write(target: Path, content: str) -> None:
    """temp 파일에 쓴 뒤 os.replace 로 원자 교체. 쓰기 중 오류가 원본을 손상시키지 않게 하고,
    기존 파일이면 shutil.copymode 로 권한(실행 비트)을 보존한다."""
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".koda-tmp-")
    try:
        with os.fdopen(fd, "w", encoding=ENCODING, newline="") as f:  # 개행 번역 금지(CRLF 보존)
            f.write(content)
        if existed:
            shutil.copymode(target, tmp)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _apply_patch(text: str, start: int, end: int, replacement: str) -> str:
    """[start, end] (1-기반, inclusive) 줄을 replacement 로 교체한 새 텍스트를 반환한다.

    - 삭제: replacement == "" (해당 범위 제거).
    - 삽입: start == end + 1 (end 다음에 앞 삽입; 파일 끝은 start=len+1,end=len; 빈 파일은 1,0).
    개행 스타일(\\r\\n / \\n)과 파일 끝 개행 유무를 보존한다(silent corruption 방지).
    호출부(patch_file)가 경계를 먼저 검증한다.
    """
    def _nl_of(s: str) -> str | None:
        return "\r\n" if s.endswith("\r\n") else ("\n" if s.endswith("\n") else None)

    lines = text.splitlines(keepends=True)
    total = len(lines)
    before, after = lines[:start - 1], lines[end:]
    orig_final_nl = (not text) or text.endswith("\n")
    # 개행 스타일: 교체/삭제는 교체 대상 첫 줄, 삽입은 앞 줄(없으면 뒤 줄)에서, 없으면 전체 우세로.
    if start <= end and start - 1 < total:
        span_nl = _nl_of(lines[start - 1])
    elif before:
        span_nl = _nl_of(before[-1])
    elif after:
        span_nl = _nl_of(after[0])
    else:
        span_nl = None
    nl = span_nl or ("\r\n" if "\r\n" in text else "\n")
    block = [] if replacement == "" else [ln + nl for ln in replacement.splitlines()]
    # 뒤에 내용이 오는데 앞 줄이 개행으로 안 끝나면(무-EOF 마지막 줄) 개행을 붙여 줄 병합을 막는다
    if before and (block or after) and _nl_of(before[-1]) is None:
        before = before[:-1] + [before[-1] + nl]
    result = "".join(before + block + after)
    # 원본이 EOF 개행이 없었으면 결과의 마지막 개행을 떼어 그 상태를 보존한다
    if result and not orig_final_nl and _nl_of(result):
        result = result[: -len(_nl_of(result))]
    return result


def _first_match_line(text: str, sub: str) -> int:
    return text.count("\n", 0, text.find(sub)) + 1


def _no_match_help(text: str, old: str, path: str) -> str:
    """old 미일치 시 근접한 실제 줄을 제시 — 로컬 모델이 공백/개행 오차로 무한 재시도하는 것 방지."""
    lines = text.splitlines()
    needle = next((ln for ln in old.splitlines() if ln.strip()), old).strip()
    scored = sorted(
        ((difflib.SequenceMatcher(None, needle, ln.strip()).ratio(), i, ln)
         for i, ln in enumerate(lines, 1)),
        key=lambda t: t[0], reverse=True,
    )
    cand = "\n".join(f"  {i}| {ln}" for r, i, ln in scored[:3] if r > 0.5)
    hint = f"\n가장 비슷한 줄:\n{cand}" if cand else ""
    return (f"실패: old 를 {path} 에서 찾지 못했다. old 는 공백·개행까지 정확히 일치해야 한다 — "
            f"read_file 로 대상 줄을 확인해 그대로 복사하라.{hint}")


def _multi_match_help(text: str, old: str, path: str) -> str:
    """old 다중 일치 시 각 위치(줄번호)와 대안(더 긴 문맥/replace_all)을 제시."""
    starts, idx = [], text.find(old)
    while idx != -1 and len(starts) < 5:
        starts.append(text.count("\n", 0, idx) + 1)
        idx = text.find(old, idx + 1)
    total = text.count(old)
    locs = ", ".join(map(str, starts)) + (" 등" if total > 5 else "")
    return (f"실패: old 가 {path} 에서 {total}회 일치한다(기본은 유일해야 함). 시작 줄: {locs}. "
            f"더 긴 문맥으로 유일하게 만들거나 replace_all=true 를 써라.")


def _edit_success(path: str, orig: str, old: str, new: str, n: int) -> str:
    tag = f"replace_all {n}회" if n != 1 else f"replace 1회 ({_first_match_line(orig, old)}번째 줄)"
    preview = ""
    if len(old) + len(new) < 800:  # 작은 변경만 미리보기
        old_p = "\n".join(f"- {ln}" for ln in old.splitlines()[:20])
        new_p = "\n".join(f"+ {ln}" for ln in new.splitlines()[:20])
        preview = f"\n{old_p}\n{new_p}".rstrip("\n")
    return f"수정함: {path} ({tag}){preview}"


def _patch_success(path: str, new_text: str, start: int, end: int, replacement: str) -> str:
    """패치 후 편집 지점 ±맥락을 줄번호와 함께 보여준다(드리프트 완화 — 다음 편집의 좌표 갱신)."""
    new_lines = new_text.splitlines()
    n_repl = 0 if replacement == "" else len(replacement.splitlines())
    lo, hi = max(1, start - 3), min(len(new_lines), start - 1 + n_repl + 3)
    view = "\n".join(f"{i}| {ln}" for i, ln in enumerate(new_lines[lo - 1:hi], lo)) or "(빈 파일)"
    kind = "삭제" if replacement == "" else ("삽입" if start == end + 1 else "교체")
    return f"패치함: {path} ({kind} {start}-{end} → {n_repl}줄)\n--- 결과(±맥락) ---\n{view}"


def make_editor_tools(ws: Workspace) -> list:
    """워크스페이스에 바인딩된 파일 변경 도구 목록 (위험)."""

    def _read(path: str):
        """파일 텍스트를 읽어 (text, None), 없으면 (None, 오류문자열). SandboxError 는 전파."""
        from .filesystem import not_found_help
        try:
            return ws.resolve(path).read_text(encoding=ENCODING), None
        except (FileNotFoundError, IsADirectoryError):
            return None, not_found_help(ws, path)

    @tool
    def write_file(path: str, content: str) -> str:
        """파일을 생성하거나 전체를 content 로 덮어쓴다(원자적). path 는 루트 기준 상대경로."""
        p = ws.resolve(path)
        existed = p.exists()
        _atomic_write(p, content)
        return (f"{'덮어씀' if existed else '생성함'}: {path} "
                f"({len(content)} chars, {len(content.splitlines())} lines)")

    @tool
    def edit_file(path: str, old: str, new: str, replace_all: bool = False) -> str:
        """파일에서 old 를 new 로 바꾼다(부분 수정, 원자적).

        기본은 old 가 파일에 **정확히 1회** 나타나야 한다. replace_all=True 면 모든 일치를 바꾼다.
        실패 시 근접 후보나 각 일치 위치를 안내하니 그 정보로 다시 시도하라.
        """
        text, err = _read(path)
        if err is not None:
            return err
        count = text.count(old)
        if count == 0:
            return _no_match_help(text, old, path)
        if count > 1 and not replace_all:
            return _multi_match_help(text, old, path)
        new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        _atomic_write(ws.resolve(path), new_text)
        return _edit_success(path, text, old, new, count if replace_all else 1)

    @tool
    def multi_edit(path: str, edits: list) -> str:
        """한 파일에 여러 수정을 한 콜로 적용한다(원자적, 전부 성공해야 반영).

        edits: [{"old": ..., "new": ...}, ...]. 순서대로 적용하며 각 old 는 그 시점 텍스트에서
        정확히 1회 일치해야 한다. 하나라도 실패하면 **전부 롤백**(부분 적용 없음).
        """
        text, err = _read(path)
        if err is not None:
            return err
        if not isinstance(edits, list) or not edits:
            return "실패: edits 는 비어있지 않은 [{old, new}] 리스트여야 한다."
        work = text
        for i, e in enumerate(edits):
            if not isinstance(e, dict) or "old" not in e or "new" not in e:
                return f"실패: edits[{i}] 는 {{old, new}} 형태여야 한다 (롤백, 무변경)."
            c = work.count(e["old"])
            if c != 1:
                why = "를 찾지 못함" if c == 0 else f"가 {c}회 일치(유일해야 함)"
                return f"실패: edits[{i}] 의 old {why} (롤백, 무변경). read_file 로 확인하라."
            work = work.replace(e["old"], e["new"], 1)
        _atomic_write(ws.resolve(path), work)
        return f"수정함: {path} (multi_edit {len(edits)}건 적용)"

    @tool
    def patch_file(path: str, start_line: int, end_line: int, replacement: str,
                   expected_head: str | None = None) -> str:
        """파일의 [start_line, end_line] 줄 범위를 replacement 로 교체한다(1-기반, end 포함, 원자적).

        줄번호는 read_file 이 붙여준 번호를 그대로 쓴다.
        - 삭제: replacement="" (범위 제거).
        - 삽입: start_line = end_line + 1 (end 다음 앞 삽입). 파일 끝 삽입은 start=총줄수+1, end=총줄수.
          빈 파일은 start=1, end=0.
        - expected_head: 주면 start_line 줄이 이와 일치할 때만 적용(드리프트 자기검증). 불일치 시
          변경하지 않고 실제 줄을 돌려준다 — read_file 로 최신 줄번호를 확인하라.
        """
        from .filesystem import not_found_help
        try:  # read_bytes+decode 로 CRLF 를 보존한다(read_text 는 개행을 \n 으로 번역해 손상)
            text = ws.resolve(path).read_bytes().decode(ENCODING, errors="replace")
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return not_found_help(ws, path)
        lines = text.splitlines(keepends=True)
        total = len(lines)
        is_insert = start_line == end_line + 1
        if is_insert:
            if not (1 <= start_line <= total + 1):
                return f"실패: 삽입 위치 start_line={start_line} 범위 밖(1..{total + 1})."
        elif not (1 <= start_line <= end_line <= total):
            return (f"실패: 잘못된 범위 start={start_line} end={end_line} "
                    f"(파일 {total}줄; 1<=start<=end<=총줄수, 삽입은 start=end+1).")
        if expected_head is not None:  # 삽입 포함 — 삽입 지점(밀려날 줄)도 검증(드리프트 미검출 방지)
            actual = lines[start_line - 1].rstrip("\r\n") if start_line - 1 < total else None
            if actual != expected_head.rstrip("\r\n"):
                shown = actual if actual is not None else "(EOF · 해당 줄 없음)"
                return (f"실패(앵커 불일치): {path}:{start_line} 이 expected_head 와 다르다 — "
                        f"파일이 밀렸을 수 있다(드리프트).\n  기대: {expected_head.rstrip(chr(10))}\n"
                        f"  실제: {shown}\nread_file 로 현재 줄번호를 다시 확인하라.")
        new_text = _apply_patch(text, start_line, end_line, replacement)
        _atomic_write(ws.resolve(path), new_text)
        return _patch_success(path, new_text, start_line, end_line, replacement)

    @tool
    def create_dir(path: str) -> str:
        """디렉토리를 생성한다(부모 포함)."""
        ws.resolve(path).mkdir(parents=True, exist_ok=True)
        return f"디렉토리 생성: {path}"

    @tool
    def delete_path(path: str) -> str:
        """파일 또는 디렉토리를 삭제한다(되돌릴 수 없음)."""
        p = ws.resolve(path)
        if not p.exists():
            return f"없음: {path}"
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return f"삭제함: {path}"

    return [write_file, edit_file, multi_edit, patch_file, create_dir, delete_path]
