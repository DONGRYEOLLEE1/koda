from __future__ import annotations

from pathlib import Path

KODA_DIR = ".koda"
MAX_CHARS = 4000  # 초과 시 압축 필요 신호 (압축 내용 생성은 LLM 몫)


class Memory:
    def __init__(self, ws_root: str | Path):
        self.path = Path(ws_root).resolve() / KODA_DIR / "memory.md"

    def read(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""

    def items(self) -> list[str]:
        """저장된 항목 목록 (불릿 제거)."""
        return [ln[2:].strip() for ln in self.read().splitlines() if ln.startswith("- ")]

    def remember(self, note: str) -> bool:
        """항목을 추가한다(멱등 — 이미 있으면 저장 안 함). 판정은 항목 단위 정확 일치(부분문자열 비교 시 오탐)."""
        note = " ".join(note.split()).strip()
        if not note or note in self.items():
            return False
        current = self.read()
        updated = (current + "\n" if current else "") + f"- {note}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(updated + "\n", encoding="utf-8")
        return True

    @property
    def needs_compaction(self) -> bool:
        return len(self.read()) > MAX_CHARS

    def replace(self, content: str) -> None:
        """압축(LLM 요약) 결과로 전체 교체."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(content.strip() + "\n", encoding="utf-8")
