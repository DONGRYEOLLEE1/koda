from __future__ import annotations

from pathlib import Path

KODA_DIR = ".koda"
# 유형은 langchain 의 메모리 구분을 따른다: semantic=사실, episodic=경험, procedural=규칙(지시).
KINDS = ("semantic", "episodic", "procedural")
_TITLES = {"semantic": "사실 (semantic)", "episodic": "경험 (episodic)", "procedural": "규칙 (procedural)"}
MAX_CHARS = 3000  # 유형별 상한. 초과 시 압축 필요 신호 (압축 내용 생성은 LLM 몫)


def parse_remember(arg: str) -> tuple[str, str]:
    """`/remember [유형] <내용>` 인자를 (유형, 내용)으로. 유형을 안 쓰면 semantic."""
    head, _, rest = arg.strip().partition(" ")
    if head in KINDS and rest.strip():
        return head, rest.strip()
    return "semantic", arg.strip()


class Memory:
    def __init__(self, ws_root: str | Path):
        root = Path(ws_root).resolve() / KODA_DIR
        self.dir = root / "memory"
        self._legacy = root / "memory.md"  # 유형 분리 이전의 단일 파일. semantic 으로 읽고 첫 저장 때 새 파일로 옮겨 간다

    def _path(self, kind: str) -> Path:
        if kind not in KINDS:
            raise ValueError(f"메모리 유형은 {KINDS} 중 하나여야 한다: {kind}")
        return self.dir / f"{kind}.md"

    def _read_kind(self, kind: str) -> str:
        try:
            return self._path(kind).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            pass
        if kind == "semantic":
            try:
                return self._legacy.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                pass
        return ""

    def items(self, kind: str = "semantic") -> list[str]:
        """저장된 항목 목록 (불릿 제거)."""
        return [ln[2:].strip() for ln in self._read_kind(kind).splitlines() if ln.startswith("- ")]

    def read(self) -> str:
        """시스템 프롬프트 주입용 본문. 비어 있지 않은 유형만 소제목과 함께 잇는다."""
        parts = [f"### {_TITLES[k]}\n{body}" for k in KINDS if (body := self._read_kind(k))]
        return "\n\n".join(parts)

    def remember(self, note: str, kind: str = "semantic") -> bool:
        """항목을 추가한다(멱등). 판정은 항목 단위 정확 일치(부분문자열 비교 시 오탐)."""
        note = " ".join(note.split()).strip()
        if not note or note in self.items(kind):
            return False
        current = self._read_kind(kind)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(kind).write_text((current + "\n" if current else "") + f"- {note}\n", encoding="utf-8")
        return True

    def needs_compaction(self, kind: str) -> bool:
        return len(self._read_kind(kind)) > MAX_CHARS

    def replace(self, kind: str, content: str) -> None:
        """압축(LLM 요약) 결과로 한 유형을 통째로 교체."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(kind).write_text(content.strip() + "\n", encoding="utf-8")
