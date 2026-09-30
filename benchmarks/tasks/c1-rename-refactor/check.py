"""c1 check: rename calc_total -> compute_order_total.

pass iff
  1. the hidden tests pass (new name works everywhere incl. the dynamic lookup, no alias, calc_total_tax intact),
  2. the workspace's own test suite passes and has at least as many tests as the original (tests updated,
     not deleted),
  3. no whole-word occurrence of `calc_total` remains in any project text file (code, tests, README), except a
     documentation line (non-.py) that also names `compute_order_total`, i.e. a note stating the rename.
"""
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))
import checklib as cl  # noqa: E402

ORIGINAL_TEST_COUNT = 8
OLD = re.compile(r"\bcalc_total\b")
NEW = re.compile(r"\bcompute_order_total\b")

a = cl.parse_args()
hidden = cl.run_hidden_tests(a.ws, a.task_dir)

with tempfile.TemporaryDirectory(prefix="bench-check-") as td:
    tmp = Path(td) / "ws"
    cl.copy_workspace(a.ws, tmp)
    removed = cl.neutralize_auto_imports(tmp)
    own = cl.run_pytest(tmp, ["tests"]) if (tmp / "tests").is_dir() else {"ok": False, "total": 0,
                                                                           "reason": "tests/ missing"}

leftovers, rename_notes = [], []
for p, text in cl.read_text_files(a.ws):
    for i, line in enumerate(text.splitlines(), 1):
        if OLD.search(line):
            where = f"{p.relative_to(a.ws)}:{i}: {line.strip()[:120]}"
            # A documentation line that states the rename ("`calc_total` was renamed to `compute_order_total`",
            # a CHANGELOG entry) is not a leftover use of the old name. Only in non-code files; in .py files any
            # occurrence counts (a line holding both names there is typically an alias).
            if p.suffix != ".py" and NEW.search(line):
                rename_notes.append(where)
            else:
                leftovers.append(where)

own_ok = own["ok"] and own.get("total", 0) >= ORIGINAL_TEST_COUNT
passed = hidden["ok"] and own_ok and not leftovers
cl.emit(passed, {
    "hidden_tests": hidden,
    "own_tests": own, "own_tests_min_count": ORIGINAL_TEST_COUNT,
    "old_name_occurrences": len(leftovers), "old_name_examples": leftovers[:10],
    "rename_notes_in_docs": rename_notes[:10],
    "workspace_changes": cl.workspace_changes(a.ws, a.private),
})
