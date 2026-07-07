"""정적 deny gate: LLM·사용자 승인 판단 이전의 하드 경계. `--yes` 로도 우회 불가 (ROADMAP §2.5)."""
from __future__ import annotations

import re

# (패턴, 사유). deny = 무조건 차단, confirm = 대화형 사용자 승인 필수(--yes 자동승인 무효).
_DENY = [
    (re.compile(r"(^|[;&|]\s*)sudo\b"), "권한 상승(sudo)"),
    (re.compile(r"(^|[;&|]\s*)(env|nohup|nice|setsid|command|xargs|timeout\s+\S+)\s+sudo\b"),
     "권한 상승(래퍼 경유 sudo)"),
    (re.compile(r"\|\s*(env\s+|xargs\s+)?sudo\b"), "권한 상승(파이프 경유 sudo)"),
    (re.compile(r"(^|[;&|]\s*)su\b\s"), "권한 상승(su)"),
    (re.compile(r"\b(curl|wget)\b[^|;&]*\|\s*(env\s+)?(ba|z|da)?sh\b"), "원격 스크립트 파이프 실행"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "비가역 git 조작(reset --hard)"),
    (re.compile(r"\bgit\s+clean\s+-\w*f"), "비가역 git 조작(clean -f)"),
    (re.compile(r"\bchmod\s+(-\w+\s+)*0?777\b"), "전체 권한 개방(chmod 777)"),
    (re.compile(r"\bmkfs\b|\bdd\s+[^|]*of=/dev/"), "디스크 파괴 명령"),
    (re.compile(r"\bfind\b[^|;&]*(\s-delete\b|\s-exec\s+rm\b)"), "find 기반 대량 삭제"),
    (re.compile(r"(^|\s)(:\(\)\s*\{.*\};\s*:)"), "포크 폭탄"),
]
_CONFIRM = [
    (re.compile(r"(^|[\s;&|/])\.(ssh|aws|gnupg|kube)(/|\b)"), "자격증명 디렉터리 접근"),
    (re.compile(r"\bid_(rsa|ed25519)\b|\bcredentials\b|\.pem\b"), "개인키/자격증명 파일 접근"),
    (re.compile(r"(^|[\s;&|/])\.env(\.\w+)?\b"), "환경 비밀 파일 접근"),
    (re.compile(r"\bgit\s+push\s+.*--force\b|\bgit\s+push\s+-f\b"), "원격 히스토리 강제 덮어쓰기"),
    # 워크스페이스 밖으로의 쓰기 가능 명령(절대경로/상위경로 인자) — OS 격리(bwrap) 부재 시 1차 방어
    (re.compile(r"\b(cp|mv|tee|dd)\b[^|;&]*(\s/\S+|\s\.\./)"), "워크스페이스 밖 경로 대상 쓰기성 명령"),
]

# rm 은 플래그 순서·장문 플래그(--recursive 등) 우회가 많아 단일 정규식 대신 2단 판정한다
# (리뷰 H1: `rm -rf .`/`rm --recursive --force /` 류가 플래그-결합 패턴을 우회했다).
_RM_SEG = re.compile(r"\brm\b([^|;&]*)")
_RM_RECURSIVE = re.compile(r"(^|\s)-\w*[rR]|\s--recursive\b")
_RM_DANGER_TARGET = re.compile(
    r"(^|\s)(--no-preserve-root|/\S*|~(/\S*)?|\$HOME\S*|\.\.(/\S*)?|\.(/)?|\*)(\s|$)"
)


def _rm_hazard(cmd: str) -> str | None:
    for m in _RM_SEG.finditer(cmd):
        args = m.group(1)
        if not _RM_RECURSIVE.search(args) and "--no-preserve-root" not in args:
            continue  # 비재귀 rm(단일 파일)은 워크스페이스 내 일상 작업
        if _RM_DANGER_TARGET.search(args):
            return "워크스페이스 밖/대량 삭제(rm)"
    return None


def check_command(command: str) -> tuple[str, str] | None:
    """셸 명령을 검사해 ("deny"|"confirm", 사유) 또는 통과 시 None 을 반환한다."""
    cmd = " ".join(command.split())  # 공백 정규화 (개행·탭 우회 방지)
    reason = _rm_hazard(cmd)
    if reason:
        return "deny", reason
    for pat, why in _DENY:
        if pat.search(cmd):
            return "deny", why
    for pat, why in _CONFIRM:
        if pat.search(cmd):
            return "confirm", why
    return None
