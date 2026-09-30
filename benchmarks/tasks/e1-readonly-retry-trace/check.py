"""e1 check: read-only question.

pass iff the user-visible answer contains every required fact (regex keyword groups below) AND the
workspace is unchanged (project files compared to the pre-run hash snapshot; VCS/venv/caches/harness state
excluded). Facts: the retry lives in weatherkit/net/backoff.py::retry_with_backoff; the effective attempt
count is 4 (settings.RETRY_ATTEMPTS overrides the default 7); waits are 0.5 s, 1 s, 2 s.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))
import checklib as cl  # noqa: E402

# Number/unit may be separated by markdown emphasis or code marks (`**4**번`, "4 **attempts**"); English
# number words and millisecond spellings are accepted, so the phrasing or language of an answer does not decide it.
E = r"[*_`\s]{0,6}"
GROUPS = {
    "file_backoff_py": [r"backoff\.py"],
    "function_retry_with_backoff": [r"retry_with_backoff"],
    "attempts_4": [rf"(?<![\d.])4{E}(번|회|차례|times|attempts?|tries|total)", r"RETRY_ATTEMPTS\W{0,6}4\b",
                   r"attempts\s*=\s*4\b", rf"총{E}4(?![\d.])", rf"\bfour{E}(times|attempts|tries)\b",
                   rf"(up to|at most|maximum of|max(imum)?)\s*{E}4(?![\d.])"],
    "source_settings": [r"settings\.py", r"RETRY_ATTEMPTS"],
    "delay_0_5": [r"(?<![\d.])0\.5(?![\d])", r"(?<![\d.])500\s*ms\b"],
    "delay_1": [rf"(?<![\d.])1(\.0)?{E}(초|s\b|sec)", r"(?<![\d.])1\.0(?![\d])", r"(?<![\d.])1000\s*ms\b",
                r"\bone second\b"],
    "delay_2": [rf"(?<![\d.])2(\.0)?{E}(초|s\b|sec)", r"(?<![\d.])2\.0(?![\d])", r"(?<![\d.])2000\s*ms\b",
                r"\btwo seconds\b"],
}

a = cl.parse_args()
answer = cl.answer_text(a.out)
facts_ok, hits = cl.regex_groups(answer, GROUPS)
changes = cl.workspace_changes(a.ws, a.private)
cl.emit(facts_ok and changes["n_changed"] == 0 and bool(answer.strip()), {
    "facts": hits, "answer_chars": len(answer), "workspace_unchanged": changes["n_changed"] == 0,
    "workspace_changes": changes,
})
