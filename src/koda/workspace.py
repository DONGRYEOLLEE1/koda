"""워크스페이스 루트 스코프. 루트 밖 경로 접근을 차단한다 (PLAN §3.0 샌드박스 불변식)."""
from __future__ import annotations

import os
from pathlib import Path

# 순회에서 제외할 무거운/무관한 디렉토리 (컨텍스트 폭증 방지).
# .koda 는 koda 산출물(세션·메모리·artifact) — 에이전트 파일 도구로 노출하지 않는다(§2.3).
_IGNORE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".idea", ".vscode", ".tox", "dist", "build", ".koda",
}


def _is_ignored_dir(name: str) -> bool:
    return name in _IGNORE_DIRS or name.endswith(".egg-info")


class SandboxError(Exception):
    pass


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise SandboxError(f"경로가 워크스페이스 밖입니다: {rel}")
        parts = p.relative_to(self.root).parts if p != self.root else ()
        if parts and parts[0] == ".koda":  # koda 내부 저장소(세션·메모리·artifact) 비노출
            raise SandboxError("'.koda' 는 koda 내부 저장소다 — artifact 는 read_artifact 로 조회하라")
        return p

    def read_file(self, rel: str) -> str:
        return self.resolve(rel).read_text(encoding="utf-8", errors="replace")

    def list_files(self, rel: str = ".", max_depth: int | None = None) -> list[str]:
        base = self.resolve(rel)
        if not base.exists():
            return []
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(base):
            # 무거운/무관한 디렉토리는 순회 자체를 가지친다 (.venv 등)
            dirnames[:] = [d for d in dirnames if not _is_ignored_dir(d)]
            if max_depth is not None and len(Path(dirpath).relative_to(base).parts) >= max_depth:
                dirnames[:] = []  # 이 깊이 아래로는 더 내려가지 않는다
            for name in filenames:
                out.append(str((Path(dirpath) / name).relative_to(self.root)))
        return sorted(out)

    def suggest_similar(self, rel: str, n: int = 3) -> list[str]:
        """없는 경로에 대해 이름이 비슷한 실제 파일을 제안한다 (파일명 오질의 복구용)."""
        import difflib
        base = os.path.basename(rel)
        files = self.list_files(".")
        close = difflib.get_close_matches(base, {os.path.basename(f) for f in files}, n=n, cutoff=0.5)
        return [f for f in files if os.path.basename(f) in close][:n]

    def search_text(self, query: str, rel: str = ".", glob: str | None = None) -> list[str]:
        import fnmatch
        hits: list[str] = []
        for f in self.list_files(rel):
            if glob and not fnmatch.fnmatch(f, glob):
                continue
            try:
                p = self.resolve(f)
                if p.stat().st_size > 2_000_000:            # 대용량 파일 스킵
                    continue
                data = p.read_bytes()
                if b"\x00" in data[:8192]:                  # 바이너리 스킵
                    continue
                for i, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                    if query in line:
                        hits.append(f"{f}:{i}:{line.strip()}")
            except OSError:
                continue
        return hits
