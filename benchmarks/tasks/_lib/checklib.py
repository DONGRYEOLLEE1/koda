"""Shared helpers for tasks/<id>/check.py. Stdlib only (pytest is invoked as a subprocess).

A check script is called by run.py (and by `run.py selftest`) as

    <checker-python> tasks/<id>/check.py --ws WS --out OUT --private PRIVATE --task-dir TASK_DIR

  WS        the agent's workspace after the run (left exactly as the tool left it)
  OUT       run artifacts: answer.txt (user-visible final output of the tool), tool_calls.json (tool calls the
            model emitted, from the metering proxy), meta.json
  PRIVATE   state recorded before the run, never visible to the agent: state.json (hash snapshot + setup data)
  TASK_DIR  the task directory (hidden_tests/ live here, outside the workspace)

and must print exactly one JSON object on the last stdout line: {"pass": true|false|null, "detail": {...}}.
pass=null means "could not be judged" (e.g. an input the judgement needs was not recorded).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Directories that are never part of the "project content": VCS data, virtualenvs, caches and the harnesses'
# own per-workspace state. Changes inside them are ignored by snapshot diffs and old-name scans.
EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".koda", ".opencode", ".hermes", "node_modules",
}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".ini", ".cfg", ".json", ".yaml", ".yml", ".sh", ".rst", ""}


# ---------------------------------------------------------------------------------------------- inputs
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--private", required=True, type=Path)
    ap.add_argument("--task-dir", required=True, type=Path)
    return ap.parse_args()


def load_state(private: Path) -> dict:
    p = private / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def answer_text(out: Path) -> str:
    p = out / "answer.txt"
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def tool_calls(out: Path) -> list[dict] | None:
    """[{name, args, args_text, result_prefix|None}] or None when the run had no proxy capture."""
    p = out / "tool_calls.json"
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("calls") if isinstance(data, dict) else data


def flatten_args(args: str) -> str:
    """Tool arguments arrive as a JSON string; join every string value so regexes see real shell text."""
    try:
        obj = json.loads(args)
    except Exception:
        return args or ""
    parts: list[str] = []

    def walk(x):
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif x is not None:
            parts.append(str(x))

    walk(obj)
    return "\n".join(parts)


# ------------------------------------------------------------------------------------------ workspace
def iter_files(root: Path, exclude: set[str] = EXCLUDE_DIRS):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in exclude and not d.endswith(".egg-info")]
        for fn in fns:
            p = Path(dp) / fn
            if p.is_symlink() or not p.is_file():
                continue
            yield p


def snapshot(root: Path, exclude: set[str] = EXCLUDE_DIRS) -> dict[str, str]:
    out = {}
    for p in iter_files(root, exclude):
        try:
            out[str(p.relative_to(root))] = hashlib.sha1(p.read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def snapshot_diff(before: dict[str, str], after: dict[str, str]) -> dict:
    added = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    return {"added": added, "modified": modified, "deleted": deleted,
            "n_changed": len(added) + len(modified) + len(deleted)}


def workspace_changes(ws: Path, private: Path) -> dict:
    before = load_state(private).get("snapshot") or {}
    return snapshot_diff(before, snapshot(ws))


def read_text_files(root: Path):
    for p in iter_files(root):
        if p.suffix not in TEXT_SUFFIXES:
            continue
        try:
            yield p, p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue


def copy_workspace(ws: Path, dst: Path) -> None:
    ignore = shutil.ignore_patterns(*EXCLUDE_DIRS, "*.pyc")
    shutil.copytree(ws, dst, ignore=ignore, symlinks=True, dirs_exist_ok=True)


# ----------------------------------------------------------------------------------------------- pytest
def run_pytest(workdir: Path, targets: list[str], *, pythonpath: list[Path] | None = None,
               timeout: int = 120, use_confs: bool = False) -> dict:
    """Run pytest (current interpreter) in `workdir` and return counts from its JUnit XML.

    Agent-written conftest.py / pytest.ini are ignored by default (--noconftest + a private ini), so the
    judgement cannot be influenced by edits to test configuration inside the workspace.
    """
    junit = workdir / ".bench-junit.xml"
    ini = workdir / ".bench-pytest.ini"
    if not use_confs:
        ini.write_text("[pytest]\n", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in (pythonpath or [workdir]))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--import-mode=importlib",
           f"--junitxml={junit}", "--rootdir", str(workdir)]
    if not use_confs:
        cmd += ["--noconftest", "-c", str(ini)]
    cmd += targets
    try:
        cp = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=timeout)
        rc, tail = cp.returncode, (cp.stdout + cp.stderr)[-1500:]
    except subprocess.TimeoutExpired as e:
        return {"ok": False, "rc": None, "timeout": True, "total": 0, "failed": 0, "errors": 0,
                "tail": str(e)[-500:]}
    total = failures = errors = skipped = 0
    failed_ids: list[str] = []
    if junit.is_file():
        root = ET.parse(junit).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        for s in suites:
            total += int(s.get("tests", 0))
            failures += int(s.get("failures", 0))
            errors += int(s.get("errors", 0))
            skipped += int(s.get("skipped", 0))
        for tc in root.iter("testcase"):
            if tc.find("failure") is not None or tc.find("error") is not None:
                failed_ids.append(f"{tc.get('classname', '')}::{tc.get('name', '')}")
    ok = rc == 0 and total > 0 and failures == 0 and errors == 0
    return {"ok": ok, "rc": rc, "total": total, "failed": failures, "errors": errors, "skipped": skipped,
            "failed_ids": failed_ids[:20], "tail": "" if ok else tail}


# Files that Python imports automatically when their directory is on sys.path (PYTHONPATH = the workspace copy):
# a workspace file with one of these names would run inside the judge's interpreter before pytest starts.
AUTO_IMPORT_NAMES = ("sitecustomize.py", "usercustomize.py")


def neutralize_auto_imports(root: Path) -> list[str]:
    """Remove sitecustomize/usercustomize/*.pth from the top of a workspace COPY. Returns what was removed."""
    removed = []
    for p in list(root.iterdir()):
        if p.is_file() and (p.name in AUTO_IMPORT_NAMES or p.suffix == ".pth"):
            p.unlink()
            removed.append(p.name)
    return removed


def run_hidden_tests(ws: Path, task_dir: Path, *, extra_targets: list[str] | None = None,
                     timeout: int = 120) -> dict:
    """Copy the workspace to a temp dir, add tasks/<id>/hidden_tests as _hidden_tests/, run them."""
    with tempfile.TemporaryDirectory(prefix="bench-check-") as td:
        tmp = Path(td) / "ws"
        copy_workspace(ws, tmp)
        removed = neutralize_auto_imports(tmp)
        shutil.copytree(task_dir / "hidden_tests", tmp / "_hidden_tests")
        res = run_pytest(tmp, ["_hidden_tests", *(extra_targets or [])], timeout=timeout)
        if removed:
            res["auto_import_files_removed"] = removed
        return res


# ---------------------------------------------------------------------------------------------- grading
def regex_groups(text: str, groups: dict[str, list[str]]) -> tuple[bool, dict]:
    """Every group must match at least one of its regexes (case-insensitive)."""
    hits = {}
    for name, pats in groups.items():
        hits[name] = any(re.search(p, text, re.I) for p in pats)
    return all(hits.values()), hits


# ------------------------------------------------------------------------------- harness snapshot stores
# Some harnesses keep their own restorable copy of the working tree outside the workspace (opencode's
# per-project git snapshot store, used by its undo/revert; Hermes' opt-in filesystem checkpoints). They live in
# the tool's isolated HOME, which run.py passes to the check as BENCH_TOOL_HOME.
HARNESS_STORES = (".local/share/opencode/snapshot", ".hermes/checkpoints")


def tool_home() -> Path | None:
    """The isolated HOME of the tool that produced this run (set by run.py; absent in the self-test)."""
    v = os.environ.get("BENCH_TOOL_HOME")
    return Path(v) if v else None


def harness_store_gitdirs(tool_home: Path | None) -> list[Path]:
    out: list[Path] = []
    if not tool_home:
        return out
    for rel in HARNESS_STORES:
        root = tool_home / rel
        if not root.is_dir():
            continue
        for dp, dns, fns in os.walk(root):
            if "HEAD" in fns and "objects" in dns:
                out.append(Path(dp))
                dns[:] = []
    return out


def harness_store_contains(tool_home: Path | None, needles: list[str], max_blob: int = 2_000_000) -> dict:
    """{needle: [gitdir name, ...]} for needles found in any blob (reachable or not) of a harness store."""
    found: dict[str, list[str]] = {n: [] for n in needles}
    for gd in harness_store_gitdirs(tool_home):
        env = dict(os.environ, GIT_DIR=str(gd))
        cp = subprocess.run(["git", "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype) "
                             "%(objectsize)"], env=env, capture_output=True, text=True)
        blobs = [ln.split()[0] for ln in cp.stdout.splitlines()
                 if len(ln.split()) == 3 and ln.split()[1] == "blob" and int(ln.split()[2]) <= max_blob]
        if not blobs:
            continue
        cp = subprocess.run(["git", "cat-file", "--batch"], env=env, input=("\n".join(blobs) + "\n").encode(),
                            capture_output=True)
        data = cp.stdout
        for n in needles:
            if n.encode() in data:
                found[n].append(gd.name)
    return found


def git(ws: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(ws.parent), GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", *args], cwd=ws, env=env, capture_output=True, text=True, check=check)


def emit(passed: bool | None, detail: dict) -> None:
    print(json.dumps({"pass": passed, "detail": detail}, ensure_ascii=False))
