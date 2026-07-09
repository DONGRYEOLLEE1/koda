from __future__ import annotations

import hashlib
import re
from pathlib import Path

MAX_ARTIFACT_BYTES = 2 * 1024 * 1024  # 파일당 hard cap
_REDACT = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(sk|pk|ghp|gho|xox[bp])[-_][A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password|authorization)\b(\s*[=:]\s*|\s+bearer\s+)\S+"),
]


def redact(text: str) -> tuple[str, int]:
    """secret 패턴을 [REDACTED] 로 치환. (치환 결과, 치환 횟수)."""
    total = 0
    for pat in _REDACT:
        text, n = pat.subn("[REDACTED]", text)
        total += n
    return text, total


class ArtifactStore:
    """세션(또는 run) 단위 artifact 저장소. 저장 전 redaction·size cap 을 집행한다."""

    def __init__(self, ws_root: Path, run_id: str):
        self.dir = Path(ws_root) / ".koda" / "artifacts" / run_id
        # 같은 세션의 다음 턴이 같은 디렉터리를 이어 쓴다 — 기존 최대 번호+1 로 이어 충돌 방지.
        nums = [int(p.name[:3]) for p in self.dir.glob("*.txt") if p.name[:3].isdigit()] \
            if self.dir.is_dir() else []
        self._seq = max(nums, default=0)

    def save(self, tool_name: str, content: str) -> dict:
        """원문 저장 후 참조 메타데이터를 반환: {id, path, sha256, lines, redacted}."""
        self._seq += 1
        content, redacted = redact(content)
        data = content.encode("utf-8")[:MAX_ARTIFACT_BYTES]
        aid = f"{self._seq:03d}-{tool_name}"
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{aid}.txt"
        path.write_bytes(data)
        return {
            "id": aid,
            "path": str(path),
            "digest": hashlib.sha256(data).hexdigest()[:16],  # sha256 앞 16 hex
            "lines": len(data.decode("utf-8", errors="replace").splitlines()),  # read_range 와 동일 기준
            "redacted": redacted,
        }

    def read_range(self, artifact_id: str, start: int = 1, end: int = 200) -> str:
        """artifact 의 [start, end] 줄 범위를 반환 (1-기반). 없으면 에러 문자열."""
        path = self.dir / f"{artifact_id}.txt"
        if not path.is_file():
            have = sorted(p.stem for p in self.dir.glob("*.txt")) if self.dir.is_dir() else []
            return f"오류: artifact '{artifact_id}' 없음. 사용 가능: {', '.join(have) or '(없음)'}"
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start, end = max(1, start), min(len(lines), end)
        if start > len(lines):
            return f"오류: 시작 줄 {start} 이 총 {len(lines)}줄을 벗어남"
        body = "\n".join(f"{i}: {line}" for i, line in enumerate(lines[start - 1:end], start))
        return f"[{artifact_id} {start}-{end}/{len(lines)}줄]\n{body}"


def excerpt(content: str, ref: dict, head: int = 40, tail: int = 20) -> str:
    """프롬프트용 발췌: head/tail + artifact 참조 안내."""
    lines = content.splitlines()
    if len(lines) <= head + tail:
        return content
    kept = lines[:head] + [
        f"... ({len(lines) - head - tail}줄 생략 — 전체는 read_artifact('{ref['id']}', start, end) 로 조회, "
        f"총 {ref['lines']}줄, digest {ref['digest']})"
    ] + lines[-tail:]
    return "\n".join(kept)
