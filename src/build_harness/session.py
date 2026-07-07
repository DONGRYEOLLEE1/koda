"""세션: 멀티턴 대화 상태(히스토리 + 턴 기록)와 영속화.

- history 에는 user 질문과 assistant **최종 답변 텍스트만** 저장한다(reasoning·도구 원본 제외
  — interleaved thinking 규칙: 턴 경계에서 reasoning 은 버린다. plan/MEMORY_MULTITURN.md §3).
- 저장은 원자적(tmp → rename)이고 멱등: 같은 상태를 다시 저장해도 파일 내용이 같다.
- 위치: <workspace>/.harness/sessions/<id>.json
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

HARNESS_DIR = ".harness"

# 점진적 문맥 압축(패턴 5): 히스토리가 임계 초과 시 오래된 턴을 LLM 요약으로 치환.
# 압축은 프롬프트 prefix 를 깨므로(캐시 미스 1회) 임계 도달 시에만 수행한다.
COMPACT_THRESHOLD_CHARS = 36_000   # ≈ 12k 토큰 (chars/3 추정)
KEEP_RECENT_TURNS = 4              # 최근 4턴(user/assistant 8메시지)은 원본 유지


class Session:
    def __init__(self, ws_root: str | Path, session_id: str | None = None):
        self.root = Path(ws_root).resolve()
        self.id = session_id or uuid.uuid4().hex[:8]
        self.history: list[dict] = []   # [{"role": "user"|"assistant", "content": str}]
        self.turns: list[dict] = []     # [{"task","route","verdict","summary"}] 턴별 기록

    @property
    def path(self) -> Path:
        return self.root / HARNESS_DIR / "sessions" / f"{self.id}.json"

    def add_turn(self, task: str, answer: str, route: str, verdict: str | None, summary: str = "") -> None:
        """턴 결과를 히스토리에 추가한다. 최종 답변 텍스트만 저장(reasoning/도구 원본 없음)."""
        self.history.append({"role": "user", "content": task})
        self.history.append({"role": "assistant", "content": answer})
        self.turns.append({"task": task, "route": route, "verdict": verdict, "summary": summary})

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(
            {"id": self.id, "history": self.history, "turns": self.turns},
            ensure_ascii=False, indent=1,
        )
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(self.path)  # 원자적 교체
        return self.path

    @classmethod
    def load(cls, ws_root: str | Path, session_id: str) -> "Session":
        s = cls(ws_root, session_id)
        data = json.loads(s.path.read_text(encoding="utf-8"))
        s.history = data.get("history", [])
        s.turns = data.get("turns", [])
        return s

    # --- 점진적 문맥 압축 (요약 "생성"은 LLM 몫, 하네스는 분할·교체만) ---

    @property
    def needs_compaction(self) -> bool:
        return sum(len(m["content"]) for m in self.history) > COMPACT_THRESHOLD_CHARS

    def _cut_index(self) -> int:
        """압축 경계 인덱스. user/assistant **쌍 경계를 보존**한다(리뷰 M3 — 홀수 히스토리에서
        assistant 가 user 없이 고아가 되지 않도록). 자를 것이 없으면 0."""
        keep = KEEP_RECENT_TURNS * 2
        if len(self.history) <= keep:
            return 0
        cut = len(self.history) - keep
        if self.history[cut].get("role") == "assistant":
            cut -= 1  # 최근 구간이 user 로 시작하도록 한 칸 양보
        return max(cut, 0)

    def old_messages(self) -> list[dict]:
        """압축(요약) 대상인 오래된 메시지들. 최근 KEEP_RECENT_TURNS 턴은 제외."""
        return self.history[: self._cut_index()]

    def apply_compaction(self, summary: str) -> None:
        """오래된 메시지들을 LLM 요약 한 쌍으로 치환한다. 최근 턴 원본은 유지.

        자를 오래된 메시지가 없으면 아무것도 하지 않는다(리뷰 M1 — 거대 단일 턴에서
        합성 요약만 덧붙어 히스토리가 오히려 커지는 것 방지).
        """
        cut = self._cut_index()
        if cut == 0 or not summary.strip():
            return
        recent = self.history[cut:]
        self.history = [
            {"role": "user", "content": "[이전 대화 요약]\n" + summary.strip()},
            {"role": "assistant", "content": "요약을 확인했다. 이어서 진행한다."},
        ] + recent

    @classmethod
    def list_ids(cls, ws_root: str | Path) -> list[str]:
        d = Path(ws_root).resolve() / HARNESS_DIR / "sessions"
        return sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []
