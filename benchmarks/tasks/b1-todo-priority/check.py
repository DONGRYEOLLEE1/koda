"""b1 check: hidden tests (priority in model/store/CLI + legacy files + original visible tests) pass."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))
import checklib as cl  # noqa: E402

a = cl.parse_args()
res = cl.run_hidden_tests(a.ws, a.task_dir)
cl.emit(res["ok"], {"hidden_tests": res, "workspace_changes": cl.workspace_changes(a.ws, a.private)})
