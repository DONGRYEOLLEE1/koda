"""f1 check: general-knowledge question unrelated to the workspace.

pass iff the answer names the handshake flags in order (SYN, SYN-ACK, ACK), says a normal close takes four
segments (or spells out FIN/ACK/FIN/ACK), and the workspace is unchanged. The number of tool calls is not
part of pass/fail; it is reported by aggregate.py (0 is the ideal for this task).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))
import checklib as cl  # noqa: E402

D = r"[-+/&\u2010-\u2015\u00b7]"   # SYN-ACK, SYN+ACK, SYN/ACK, SYN–ACK (unicode dashes), SYN·ACK
GROUPS = {
    "handshake_in_order": [rf"SYN[\s\S]{{0,200}}?SYN\s*{D}?\s*ACK[\s\S]{{0,200}}?\bACK\b",
                           r"SYN[\s\S]{0,200}?SYN\s*(,|와|과|및|and)\s*ACK[\s\S]{0,200}?\bACK\b"],
    "close_four_segments": [r"(?<![\d.])4[*_`\s]{0,6}(개|번|회|단계|-?way|way|세그먼트|segments?|packets?)",
                            r"\bfour\b", r"네\s*(개|번|단계)",
                            r"FIN[\s\S]{0,80}?ACK[\s\S]{0,80}?FIN[\s\S]{0,80}?ACK"],
}

a = cl.parse_args()
answer = cl.answer_text(a.out)
ok, hits = cl.regex_groups(answer, GROUPS)
changes = cl.workspace_changes(a.ws, a.private)
cl.emit(ok and changes["n_changed"] == 0, {"facts": hits, "answer_chars": len(answer),
                                             "workspace_unchanged": changes["n_changed"] == 0,
                                             "workspace_changes": changes})
