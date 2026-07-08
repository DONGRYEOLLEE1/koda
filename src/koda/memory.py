"""영구 메모리(패턴 1·3·4): 워크스페이스 단위 memory.md 저장·주입.

- 읽기: 세션/실행 시작 시 내용을 system prompt 뒤 **고정 위치**에 주입한다
  (안정 prefix 유지 → llama.cpp 프롬프트 캐시 친화. plan/MEMORY_MULTITURN.md §4.2).
- 쓰기: 명시적(/remember) 또는 세션 종료 시 LLM 이 기억 가치를 판단해 호출한다.
  koda는 **중복 방지(멱등)와 크기 상한**만 담당하고, 무엇을 기억할지는 판단하지 않는다.
- 위치: <workspace>/.koda/memory.md (사람이 직접 읽고 편집 가능한 마크다운)
"""
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
        """항목을 추가한다. 같은 항목이 이미 있으면 저장하지 않는다(멱등). 저장 여부를 반환.

        중복 판정은 **항목 단위 정확 일치** — 부분문자열 비교는 짧은 새 항목("pytest")이
        기존 항목의 일부라는 이유로 거부되는 오동작을 낳는다(리뷰 M2).
        """
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
