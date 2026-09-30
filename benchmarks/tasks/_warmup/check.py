"""Warm-up check: calc.sub exists and works (result is not aggregated)."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))
import checklib as cl  # noqa: E402

a = cl.parse_args()
code = "import calc; assert calc.sub(7, 4) == 3 and calc.sub(4, 7) == -3 and calc.add(2, 3) == 5"
cp = subprocess.run([sys.executable, "-c", code], cwd=a.ws, capture_output=True, text=True, timeout=30)
cl.emit(cp.returncode == 0, {"stderr": cp.stderr[-300:]})
