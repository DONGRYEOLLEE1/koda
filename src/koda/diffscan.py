from __future__ import annotations

import difflib
import hashlib
from pathlib import Path

EXCLUDE_DIRS = {".git", ".venv", ".koda", "node_modules", "__pycache__"}
KEEP_TEXT_BYTES = 256 * 1024  # 이하 텍스트 파일만 원문 보관 (diff 생성용)
MAX_DIFF_LINES_PER_FILE = 200
MAX_TOTAL_CHARS = 8000


def snapshot(root: Path) -> dict:
    """{상대경로: {"sha", "size", "text"}} — 메모리 내 스냅샷. text 는 대용량/바이너리면 None."""
    snap: dict = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        text = None
        if len(data) <= KEEP_TEXT_BYTES:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                pass
        snap[str(rel)] = {"sha": hashlib.sha256(data).hexdigest(), "size": len(data), "text": text}
    return snap


def diff_report(before: dict, after: dict) -> tuple[list[str], str]:
    """(변경 목록, unified diff). 신규(A)·삭제(D) 파일도 보관 원문 기준으로 diff 를 만든다 — git diff 가 untracked 를 못 보는 구멍을 막는다."""
    changed: list[str] = []
    chunks: list[str] = []
    for rel in sorted(set(before) | set(after)):
        b, a = before.get(rel), after.get(rel)
        if b and a and b["sha"] == a["sha"]:
            continue
        mark = "A" if b is None else ("D" if a is None else "M")
        changed.append(f"{mark} {rel}")
        binary = (b is not None and b["text"] is None) or (a is not None and a["text"] is None)
        if binary:
            size = (a or b)["size"]  # type: ignore[index]
            chunks.append(f"--- {rel}: binary/large changed ({mark}, {size} bytes)\n")
            continue
        old = b["text"] if b else ""
        new = a["text"] if a else ""
        lines = list(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3,
        ))
        if len(lines) > MAX_DIFF_LINES_PER_FILE:
            omitted = len(lines) - MAX_DIFF_LINES_PER_FILE
            lines = lines[:MAX_DIFF_LINES_PER_FILE] + [f"... ({rel}: {omitted}줄 절단)\n"]
        chunks.append("".join(lines))
    text = "".join(chunks)
    if len(text) > MAX_TOTAL_CHARS:
        text = text[:MAX_TOTAL_CHARS] + "\n... (전체 diff 상한 절단)"
    return changed, text
