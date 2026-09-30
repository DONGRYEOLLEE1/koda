#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""koda KPI benchmark driver: koda vs opencode vs hermes on the same model (DeepSeek deepseek-flash).

  uv run benchmarks/run.py list
  uv run benchmarks/run.py selftest                    # offline: every check FAILs on its negative fixtures
                                                       # and PASSes on the reference solution
  uv run benchmarks/run.py run --dry-run               # 1 rep per task (+ warm-up), pipeline end-to-end
  uv run benchmarks/run.py run --run-id main-YYYYMMDD  # full run: reps from task.yaml (5 or 3)
  uv run benchmarks/run.py rejudge --run-id ID --reason "..."   # re-run checks on kept artifacts

Design (see benchmarks/README.md):
  * A round is (task, rep). The three tools run *concurrently* in a round, each in a fresh copy of the task
    template, so latency, provider prompt cache and peak/off-peak effects hit all tools in the same window.
  * Round order is shuffled with a fixed seed. Each invocation starts with one discarded warm-up round.
  * Every run: template -> new directory (+ git init, optional setup.sh, a .venv with pytest), 300 s timeout,
    then the task's check.py judges the result independently of the tool's own exit code.
  * Results: results/<run_id>/<task>/<tool>/<rep>/{meta.json, check.json, answer.txt, run.json}.
    Raw artifacts (workspaces, proxy logs, stdout) stay in the work dir outside the repo.
  * Restartable: runs that already have check.json are skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

BENCH_DIR = Path(__file__).resolve().parent
TASKS_DIR = BENCH_DIR / "tasks"
RUNNERS_DIR = BENCH_DIR / "runners"
RESULTS_DIR = BENCH_DIR / "results"
TOOLS = ("koda", "opencode", "hermes")
PYTEST_VERSION = os.environ.get("BENCH_PYTEST_VERSION", "9.1.1")
WS_PYTHON = os.environ.get("BENCH_WS_PYTHON", "3.13")
STRUCTURED_SCHEMA_NAMES = {"_Route", "Verdict", "MemoryJudgement"}
DEFAULT_SEED = 20261001

sys.path.insert(0, str(TASKS_DIR / "_lib"))
import checklib  # noqa: E402

KODA_DIR = Path(os.environ.get("KODA_DIR", BENCH_DIR.parent)).resolve()
STATE_DIR = Path(os.environ.get("BENCH_STATE_DIR", Path.home() / ".cache" / "koda-bench"))
BENCH_TOOLS_DIR = Path(os.environ.get("BENCH_TOOLS_DIR", STATE_DIR / "tools")).resolve()
BENCH_HOME = Path(os.environ.get("BENCH_HOME", STATE_DIR / "home")).resolve()
WORK_ROOT = Path(os.environ.get("BENCH_WORK_DIR", Path(tempfile.gettempdir()) / "koda-bench-work")).resolve()

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Bench User", "GIT_AUTHOR_EMAIL": "bench@example.invalid",
    "GIT_COMMITTER_NAME": "Bench User", "GIT_COMMITTER_EMAIL": "bench@example.invalid",
    "GIT_AUTHOR_DATE": "2026-03-02T10:00:00+0000", "GIT_COMMITTER_DATE": "2026-03-02T10:00:00+0000",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------------------------------------ tasks
@dataclass
class Task:
    id: str
    dir: Path
    cfg: dict

    @property
    def reps(self) -> int:
        return int(self.cfg.get("reps", 5))

    @property
    def category(self) -> str:
        return self.cfg.get("category", "")

    @property
    def project_dir(self) -> str:
        return self.cfg.get("project_dir") or "project"

    @property
    def prompt(self) -> Path:
        return self.dir / "prompt.txt"

    @property
    def timeout(self) -> int:
        return int(self.cfg.get("timeout_s", 300))


def load_tasks(selected: list[str] | None = None, include_warmup: bool = False) -> list[Task]:
    tasks = []
    for d in sorted(TASKS_DIR.iterdir()):
        if not (d / "task.yaml").is_file():
            continue
        cfg = yaml.safe_load((d / "task.yaml").read_text(encoding="utf-8"))
        t = Task(cfg["id"], d, cfg)
        if t.id.startswith("_") and not include_warmup:
            continue
        if selected and t.id not in selected and not any(t.id.startswith(s) for s in selected):
            continue
        tasks.append(t)
    return tasks


# -------------------------------------------------------------------------------------------- helpers
def run(cmd, *, cwd=None, env=None, check=True, timeout=None, capture=True) -> subprocess.CompletedProcess:
    cp = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, check=False, timeout=timeout,
                        capture_output=capture, text=True)
    if check and cp.returncode != 0:
        raise RuntimeError(f"command failed ({cp.returncode}): {' '.join(str(c) for c in cmd)}\n"
                           f"{(cp.stdout or '')[-800:]}{(cp.stderr or '')[-1500:]}")
    return cp


def git_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env.update(GIT_ENV)
    env.update(extra or {})
    return env


def assert_outside_git(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    cp = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if cp.returncode == 0:
        sys.exit(f"work dir {path} is inside a git repository ({cp.stdout.strip()}); set BENCH_WORK_DIR "
                 "to a directory outside any repo so agents' git commands cannot reach it.")


def ensure_checker_venv() -> Path:
    """A private interpreter with pytest for check.py (never the agent's workspace venv)."""
    venv = WORK_ROOT / "_checker-venv"
    py = venv / "bin" / "python"
    if not py.exists():
        run(["uv", "venv", "-q", "--python", WS_PYTHON, venv])
        run(["uv", "pip", "install", "-q", "--python", py, f"pytest=={PYTEST_VERSION}"])
    return py


def read_key() -> str:
    """DeepSeek key via runners/load_key.sh, kept in memory only (used to scrub artifacts)."""
    cp = subprocess.run(["bash", "-c", '. "$1/load_key.sh" >/dev/null 2>&1 && printf %s "$DEEPSEEK_API_KEY"',
                         "_", str(RUNNERS_DIR)], capture_output=True, text=True,
                        env={**os.environ, "KODA_DIR": str(KODA_DIR)})
    return cp.stdout.strip()


def scrub_secret(root: Path, secret: str) -> int:
    """Replace the real API key in every text file under root. Returns the number of files changed."""
    if not secret or not root.exists():
        return 0
    n = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in (".venv", ".git", "node_modules")]
        for fn in fns:
            p = Path(dp) / fn
            try:
                if p.is_symlink() or p.stat().st_size > 50_000_000:
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            if secret.encode() in data:
                p.write_bytes(data.replace(secret.encode(), b"[REDACTED:DEEPSEEK_API_KEY]"))
                n += 1
    return n


class Sanitizer:
    """Strips machine-specific absolute paths from anything written to results/ (published)."""

    def __init__(self) -> None:
        pairs = [(WORK_ROOT, "<work>"), (BENCH_TOOLS_DIR, "<bench-tools>"), (BENCH_HOME, "<bench-home>"),
                 (KODA_DIR, "<koda>"), (STATE_DIR, "<bench-state>"), (Path.home(), "~")]
        subs = []
        for p, tag in pairs:
            for variant in {str(p), str(p.resolve()), re.sub(r"^/private", "", str(p.resolve()))}:
                if variant and variant != "/":
                    subs.append((variant, tag))
        self.subs = sorted(set(subs), key=lambda x: -len(x[0]))
        # per-user temp roots whose names embed the user name (e.g. /private/tmp/claude-<uid>/-Users-<name>-...)
        self.generic = [(re.compile(r"/(?:private/)?var/folders/[\w/+.-]+"), "<tmp>"),
                        (re.compile(r"(?:/private)?/tmp/claude-\d+/[^\s\"'/]+(?:/[^\s\"'/]+)?"), "<tmp>"),
                        (re.compile(r"-Users-[^/\s\"']+"), "-<user-path>"),
                        (re.compile(r"/Users/[^/\s\"']+"), "~"), (re.compile(r"/home/[^/\s\"']+"), "~")]

    def text(self, s: str) -> str:
        for a, b in self.subs:
            s = s.replace(a, b)
        for rx, b in self.generic:
            s = rx.sub(b, s)
        return s

    def obj(self, o):
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, list):
            return [self.obj(x) for x in o]
        if isinstance(o, dict):
            return {k: self.obj(v) for k, v in o.items()}
        return o


def load_jsonl(p: Path) -> list[dict]:
    rows = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def is_structured(row: dict) -> bool:
    q = row.get("req") or {}
    names = q.get("tool_names") or []
    return len(names) == 1 and (q.get("tool_choice") == "object" or names[0] in STRUCTURED_SCHEMA_NAMES)


def hangul_ratio(text: str) -> float | None:
    han = len(re.findall(r"[가-힣]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    return round(han / (han + lat), 3) if han + lat else None


def koda_reviewer_prompt_chars() -> int | None:
    p = KODA_DIR / "src" / "koda" / "prompts" / "reviewer.md"
    return len(p.read_text(encoding="utf-8").strip()) if p.is_file() else None


# --------------------------------------------------------------------------------------- workspace setup
@dataclass
class RunPaths:
    base: Path
    ws: Path
    out: Path
    private: Path


def run_paths(run_dir: Path, task: Task, tool: str, rep: str) -> RunPaths:
    # neutral workspace path: no tool/task names an agent could key on
    h = hashlib.sha1(f"{task.id}/{tool}/{rep}".encode()).hexdigest()[:10]
    base = run_dir / "runs" / h
    return RunPaths(base, base / "w" / task.project_dir, base / "out", base / "private")


def prepare_workspace(task: Task, p: RunPaths) -> dict:
    if p.base.exists():
        shutil.rmtree(p.base)
    p.ws.parent.mkdir(parents=True)
    p.out.mkdir(parents=True)
    p.private.mkdir(parents=True)
    shutil.copytree(task.dir / "template", p.ws)
    env = git_env({"GIT_CEILING_DIRECTORIES": str(p.ws.parent)})
    if task.cfg.get("git", True):
        run(["git", "init", "-q", "-b", "main"], cwd=p.ws, env=env)
        for k, v in (("user.name", "Bench User"), ("user.email", "bench@example.invalid"),
                     ("commit.gpgsign", "false")):
            run(["git", "config", k, v], cwd=p.ws, env=env)
        run(["git", "add", "-A"], cwd=p.ws, env=env)
        run(["git", "commit", "-q", "-m", "initial import"], cwd=p.ws, env=env)
    setup = task.dir / "setup.sh"
    if setup.is_file():
        run(["bash", setup], cwd=p.ws, env={**env, "WS": str(p.ws), "BENCH_PRIVATE": str(p.private)})
    if task.cfg.get("venv", True):
        run(["uv", "venv", "-q", "--python", WS_PYTHON, p.ws / ".venv"])
        run(["uv", "pip", "install", "-q", "--python", p.ws / ".venv" / "bin" / "python", f"pytest=={PYTEST_VERSION}"])
    head = run(["git", "rev-parse", "HEAD"], cwd=p.ws, env=env, check=False).stdout.strip() or None
    sj = p.private / "setup.json"
    state = {"snapshot": checklib.snapshot(p.ws), "git_head": head,
             "setup": json.loads(sj.read_text(encoding="utf-8")) if sj.is_file() else None,
             "prepared_at": utcnow()}
    (p.private / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def run_check(task: Task, p: RunPaths, checker_py: Path, tool_home: Path | None = None) -> dict:
    env = dict(os.environ)
    env.pop("BENCH_TOOL_HOME", None)
    if tool_home is not None:
        env["BENCH_TOOL_HOME"] = str(tool_home)   # harness snapshot stores (g4)
    try:
        cp = run([checker_py, task.dir / "check.py", "--ws", p.ws, "--out", p.out, "--private", p.private,
                  "--task-dir", task.dir], cwd=p.out, env=env, check=False, timeout=600)
        lines = [l for l in cp.stdout.splitlines() if l.strip().startswith("{")]
        res = json.loads(lines[-1]) if lines else {"pass": None, "detail": {"error": "no JSON from check",
                                                                            "stderr": cp.stderr[-1500:]}}
        if cp.returncode != 0 and res.get("pass") is not None:
            res.setdefault("detail", {})["check_rc"] = cp.returncode
        if cp.returncode != 0 and not lines:
            res["detail"]["stderr"] = cp.stderr[-1500:]
    except subprocess.TimeoutExpired:
        res = {"pass": None, "detail": {"error": "check timed out"}}
    res["checked_at"] = utcnow()
    return res


# ------------------------------------------------------------------------------------------ post-process
def extract_answer(tool: str, out: Path) -> str:
    so = out / "stdout.txt"
    if not so.is_file():
        return ""
    raw = so.read_text(encoding="utf-8", errors="replace")
    if tool != "opencode":
        return raw
    texts = []
    for line in raw.splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "text":
            texts.append((ev.get("part") or {}).get("text") or "")
    return "\n".join(texts)


def build_tool_calls(out: Path) -> dict | None:
    rows = load_jsonl(out / "proxy.jsonl")
    if not rows:
        return None
    results = {}
    for r in rows:
        for w in r.get("watched_tool_results") or []:
            results[w.get("id")] = w.get("result_prefix")
    calls = []
    captured = False
    for r in rows:
        if "resp_tool_call_list" in r:
            captured = True
        if is_structured(r):
            continue
        for c in r.get("resp_tool_call_list") or []:
            calls.append({"seq": r.get("seq"), "id": c.get("id"), "name": c.get("name"), "args": c.get("args"),
                          "args_chars": c.get("args_chars"), "result_prefix": results.get(c.get("id"))})
    if not captured and any((r.get("resp_tool_calls") or 0) > 0 for r in rows):
        return None  # tool calls happened but arguments were not captured -> cannot judge
    return {"calls": calls}


def compact_requests(out: Path) -> list[dict]:
    keep = []
    for r in load_jsonl(out / "proxy.jsonl"):
        q = r.get("req")
        if q is None:
            continue
        u = r.get("usage") or {}
        keep.append({
            "seq": r.get("seq"), "status": r.get("status"), "stream": q.get("stream"),
            "system_chars": q.get("system_chars"), "n_tools": q.get("n_tools"), "tool_choice": q.get("tool_choice"),
            "reasoning_effort": q.get("reasoning_effort"), "max_tokens": q.get("max_tokens"),
            "temperature": q.get("temperature"), "n_messages": q.get("n_messages"),
            "prompt_tokens": u.get("prompt_tokens"), "cache_hit": u.get("prompt_cache_hit_tokens"),
            "completion_tokens": u.get("completion_tokens"),
            "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "finish_reason": r.get("finish_reason"), "resp_tool_calls": r.get("resp_tool_calls"),
            "tool_call_names": [c.get("name") for c in r.get("resp_tool_call_list") or []],
            "structured_output": is_structured(r), "latency_s": r.get("latency_s"), "ttfb_s": r.get("ttfb_s"),
        })
    return keep


@dataclass
class Ctx:
    run_id: str
    run_dir: Path            # work dir for this run id
    res_dir: Path            # results/<run_id>
    checker_py: Path
    key: str
    san: Sanitizer
    effort: str = "high"
    proxy: str = "1"
    timeout_override: int | None = None
    hermes_baseline: Path | None = None
    reviewer_chars: int | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def hermes_home() -> Path:
    return BENCH_HOME / "hermes" / ".hermes"


def tool_home(tool: str) -> Path:
    return BENCH_HOME / tool


# Hermes keeps cross-session state that its tools can read back in a later run: the session database
# (the `session_search` tool does FTS over past sessions - earlier reps of the same task, earlier dry runs),
# its scratch directory for helper scripts and saved terminal output. Every hermes run starts without them
# and each run's copy is moved into that run's raw output dir (audit). memories/ and skills/ are restored to
# the invocation's baseline separately (restore_hermes_state).
HERMES_VOLATILE = ("state.db", "state.db-wal", "state.db-shm", "cache/scratch", "cache/terminal-output",
                   "sessions", "checkpoints")


def move_hermes_volatile(dst: Path) -> list[str]:
    hh = hermes_home()
    moved = []
    for rel in HERMES_VOLATILE:
        src = hh / rel
        if src.exists() or src.is_symlink():
            target = dst / rel.replace("/", "__")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(target))
            moved.append(rel)
    return moved


# ------------------------------------------------------------------------ outside-the-workspace audit
TMP_ROOT = Path("/tmp").resolve()


def tmp_listing() -> set[str]:
    try:
        return set(os.listdir(TMP_ROOT))
    except OSError:
        return set()


def audit_tool_calls(tc: dict | None, p: RunPaths, own_home: Path) -> dict:
    """References in the model's tool-call arguments to benchmark locations outside the run's workspace
    (hidden tests, solutions, other runs, the koda repo, other tools' homes). Detection only."""
    if not tc:
        return {"suspicious_refs": 0, "examples": []}
    ws_variants = {str(p.ws), str(p.ws.resolve()), re.sub(r"^/private", "", str(p.ws.resolve()))}
    own_variants = {str(own_home), str(own_home.resolve()), re.sub(r"^/private", "", str(own_home.resolve()))}
    roots = [KODA_DIR, BENCH_TOOLS_DIR, BENCH_HOME, WORK_ROOT, RESULTS_DIR]
    root_variants = sorted({v for r in roots for v in (str(r), str(r.resolve()),
                                                       re.sub(r"^/private", "", str(r.resolve())))},
                           key=len, reverse=True)
    words = re.compile(r"hidden_tests|/solution/|/negatives/|/positives/|benchmarks/tasks|\.\./\.\./\.\.")
    hits = []
    for c in tc.get("calls") or []:
        a = c.get("args") or ""
        for v in sorted(ws_variants | own_variants, key=len, reverse=True):
            a = a.replace(v, "")
        if any(v in a for v in root_variants) or words.search(a):
            hits.append({"name": c.get("name"), "args": a[:300]})
    return {"suspicious_refs": len(hits), "examples": hits[:5]}


def snapshot_hermes_baseline(ctx: Ctx) -> None:
    base = ctx.run_dir / "_hermes_baseline"
    if base.exists():
        ctx.hermes_baseline = base
        return
    hh = hermes_home()
    base.mkdir(parents=True)
    for sub in ("memories", "skills"):
        if (hh / sub).is_dir():
            shutil.copytree(hh / sub, base / sub, symlinks=True)
    ctx.hermes_baseline = base


def restore_hermes_state(ctx: Ctx, out: Path) -> bool:
    drift = {}
    try:
        drift = json.loads((out / "state_drift.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    changed = any((v or {}).get("n_changed") for v in drift.values())
    if not changed or not ctx.hermes_baseline:
        return False
    hh = hermes_home()
    for sub in ("memories", "skills"):
        src = ctx.hermes_baseline / sub
        if (hh / sub).exists():
            shutil.rmtree(hh / sub)
        if src.is_dir():
            shutil.copytree(src, hh / sub, symlinks=True)
    return True


def infra_flags(meta: dict, reqs: list[dict], runner_rc: int | None) -> dict:
    """Signals that a failure may come from the benchmark infrastructure or the provider rather than the tool.
    A run flagged here is investigated; it is re-run only when the cause is confirmed to be infrastructure."""
    statuses = [r.get("status") for r in reqs if isinstance(r.get("status"), int)]
    f = {
        "meta_missing": not meta,
        "proxy_off": bool(meta) and not meta.get("proxy"),
        "llm_http_429": sum(1 for s in statuses if s == 429),
        "llm_http_5xx": sum(1 for s in statuses if 500 <= s < 600),   # 599 = proxy could not reach upstream
        "runner_rc": runner_rc,
    }
    f["suspect"] = bool(f["meta_missing"] or f["proxy_off"] or f["llm_http_429"] or f["llm_http_5xx"])
    return f


def postprocess(ctx: Ctx, task: Task, tool: str, rep: str, p: RunPaths, round_info: dict, res_path: Path,
                tmp_created: set[str] | None = None, runner_rc: int | None = None) -> dict:
    scrubbed = scrub_secret(p.out, ctx.key) + scrub_secret(p.ws, ctx.key)
    answer = extract_answer(tool, p.out)
    (p.out / "answer.txt").write_text(answer, encoding="utf-8")
    tc = build_tool_calls(p.out)
    if tc is not None:
        (p.out / "tool_calls.json").write_text(json.dumps(tc, ensure_ascii=False, indent=2), encoding="utf-8")
    # entries this run's model created directly under /tmp (attributed through its own tool-call arguments):
    # moved out of /tmp into the run's raw dir, and reported
    outside_tmp = []
    raw_args = "\n".join(c.get("args") or "" for c in (tc or {}).get("calls", []))
    for name in sorted(tmp_created or ()):
        if re.search(r"/tmp/" + re.escape(name) + r"(?![\w.-])", raw_args):
            outside_tmp.append(name)
            src = TMP_ROOT / name
            if src.exists() or src.is_symlink():
                dst = p.base / "outside_ws_tmp"
                dst.mkdir(exist_ok=True)
                try:
                    shutil.move(str(src), str(dst / name))
                except OSError:
                    pass
    path_audit = audit_tool_calls(tc, p, tool_home(tool))
    check = run_check(task, p, ctx.checker_py, tool_home=tool_home(tool))
    meta = json.loads((p.out / "meta.json").read_text(encoding="utf-8")) if (p.out / "meta.json").is_file() else {}
    restored = restore_hermes_state(ctx, p.out) if tool == "hermes" else False
    hermes_moved = move_hermes_volatile(p.out / "hermes_state") if tool == "hermes" else None
    reqs = compact_requests(p.out)
    reviewer_calls = None
    if tool == "koda" and ctx.reviewer_chars:
        reviewer_calls = sum(1 for r in reqs if r["n_tools"] == 0 and r["system_chars"] == ctx.reviewer_chars)
    body = answer.split("\n\n--- route:")[0] if tool == "koda" else answer
    info = {
        "task": task.id, "category": task.category, "tool": tool, "rep": rep, **round_info,
        "answer_chars": len(answer), "answer_hangul_ratio": hangul_ratio(body),
        "koda_reviewer_calls": reviewer_calls,
        "pytest_cache_in_workspace": (p.ws / ".pytest_cache").exists(),
        "hermes_state_restored": restored, "hermes_volatile_state_reset": hermes_moved,
        "secret_scrub_files": scrubbed,
        "bench_env": json.loads((p.out / "bench_env.json").read_text()) if (p.out / "bench_env.json").is_file() else None,
        "tool_call_names": [c["name"] for c in (tc or {}).get("calls", [])] if tc else None,
        "outside_workspace": {"tmp_entries_created": outside_tmp,
                              "tmp_entries_created_unattributed_in_round": sorted(set(tmp_created or ()) - set(outside_tmp)),
                              **path_audit},
        "infra": infra_flags(meta, reqs, runner_rc),
        "llm_requests": reqs,
        "work_dir": str(p.base),
    }
    # published copies: sanitized, fake secrets masked
    secrets = ((json.loads((p.private / "state.json").read_text()).get("setup") or {}).get("secrets") or {})
    pub_answer = ctx.san.text(answer)
    for name, val in secrets.items():
        pub_answer = pub_answer.replace(val, f"<FAKE_SECRET:{name}>")
    pub_check = json.loads(json.dumps(check))
    pub_check_s = json.dumps(pub_check, ensure_ascii=False)
    pub_info_s = json.dumps(ctx.san.obj(info), ensure_ascii=False, indent=2)
    for name, val in secrets.items():
        pub_check_s = pub_check_s.replace(val, f"<FAKE_SECRET:{name}>")
        pub_info_s = pub_info_s.replace(val, f"<FAKE_SECRET:{name}>")
    res_path.mkdir(parents=True, exist_ok=True)
    (res_path / "meta.json").write_text(json.dumps(ctx.san.obj(meta), ensure_ascii=False, indent=2), encoding="utf-8")
    (res_path / "answer.txt").write_text(pub_answer, encoding="utf-8")
    (res_path / "run.json").write_text(pub_info_s, encoding="utf-8")
    (res_path / "check.json").write_text(json.dumps(ctx.san.obj(json.loads(pub_check_s)), ensure_ascii=False,
                                                    indent=2), encoding="utf-8")
    # drop the heavy venv from the kept workspace
    shutil.rmtree(p.ws / ".venv", ignore_errors=True)
    return {"pass": check.get("pass"), "wall": meta.get("wall_seconds"), "exit": meta.get("exit_code"),
            "timed_out": meta.get("timed_out"), "infra_suspect": info["infra"]["suspect"],
            "outside_tmp": outside_tmp, "suspicious_refs": path_audit["suspicious_refs"]}


def runner_env(ctx: Ctx, task: Task, p: RunPaths) -> dict:
    env = dict(os.environ)
    env.update({
        "KODA_DIR": str(KODA_DIR), "BENCH_TOOLS_DIR": str(BENCH_TOOLS_DIR), "BENCH_HOME": str(BENCH_HOME),
        "BENCH_TIMEOUT": str(ctx.timeout_override or task.timeout), "BENCH_EFFORT": ctx.effort,
        "BENCH_PROXY": ctx.proxy, "BENCH_CAPTURE_TOOL_ARGS": "4000",
        "BENCH_WATCH_REGEX": task.cfg.get("watch_tool_args") or "",
        "BENCH_TOOL_PATH_PREPEND": str(p.ws / ".venv" / "bin") if task.cfg.get("venv", True) else "",
    })
    return env


def result_path(ctx: Ctx, task: Task, tool: str, rep: str) -> Path:
    return ctx.res_dir / task.id / tool / rep


def run_round(ctx: Ctx, task: Task, rep: str, tools: list[str], round_info: dict, res_base: Path | None = None) -> dict:
    jobs = []
    for tool in tools:
        rp = (res_base / tool / rep) if res_base else result_path(ctx, task, tool, rep)
        if (rp / "check.json").is_file():
            continue
        p = run_paths(ctx.run_dir, task if not res_base else Task(f"{task.id}@{res_base.name}", task.dir, task.cfg),
                      tool, rep)
        prepare_workspace(task, p)
        jobs.append((tool, p, rp))
    if not jobs:
        return {}
    # launch order within the round is shuffled too (seeded per round), so no tool always starts first
    random.Random(f"{round_info.get('seed', DEFAULT_SEED)}/{task.id}/{rep}").shuffle(jobs)
    info = {**round_info, "round_started_at": utcnow(), "concurrent_tools": [j[0] for j in jobs],
            "launch_order": [j[0] for j in jobs]}
    tmp_before = tmp_listing()
    procs = []
    for tool, p, rp in jobs:
        logf = open(p.base / "runner.log", "w")
        pr = subprocess.Popen(["bash", str(RUNNERS_DIR / f"run_{tool}.sh"), str(p.ws), str(task.prompt), str(p.out)],
                              env=runner_env(ctx, task, p), stdout=logf, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL)
        procs.append((tool, p, rp, pr, logf))
    deadline = time.monotonic() + (ctx.timeout_override or task.timeout) + 180
    for tool, p, rp, pr, logf in procs:
        try:
            pr.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pr.kill()
            pr.wait()
        logf.close()
    tmp_created = tmp_listing() - tmp_before
    outcome = {}
    for tool, p, rp, pr, logf in procs:
        if not (p.out / "meta.json").is_file():
            tail = (p.base / "runner.log").read_text(errors="replace")[-800:]
            log(f"    ! {tool}: runner produced no meta.json (rc={pr.returncode}). log tail:\n{ctx.san.text(tail)}")
        outcome[tool] = postprocess(ctx, task, tool, rep, p, info, rp, tmp_created=tmp_created,
                                    runner_rc=pr.returncode)
    return outcome


def fmt_outcome(o: dict) -> str:
    parts = []
    for tool in TOOLS:
        if tool in o:
            x = o[tool]
            v = {True: "pass", False: "FAIL", None: "n/a"}[x["pass"]]
            w = f"{x['wall']:.1f}s" if isinstance(x.get("wall"), (int, float)) else "?"
            flags = (" TIMEOUT" if x.get("timed_out") else "") + (" INFRA?" if x.get("infra_suspect") else "") \
                + (f" tmp:{','.join(x['outside_tmp'])}" if x.get("outside_tmp") else "") \
                + (f" REFS:{x['suspicious_refs']}" if x.get("suspicious_refs") else "")
            parts.append(f"{tool} {v} {w}{flags}")
    return " | ".join(parts)


# -------------------------------------------------------------------------------------------- commands
def cmd_list(_args) -> int:
    for t in load_tasks(include_warmup=True):
        log(f"{t.id:28} {t.category:12} reps={t.reps}  {t.cfg.get('title', '')}")
    return 0


def git_rev(path: Path) -> str | None:
    cp = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    if cp.returncode:
        return None
    dirty = subprocess.run(["git", "-C", str(path), "diff", "--quiet", "HEAD", "--", "src"]).returncode != 0
    return cp.stdout.strip() + ("+dirty" if dirty else "")


def suite_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(list(TASKS_DIR.rglob("*")) + list(RUNNERS_DIR.rglob("*"))):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(BENCH_DIR)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def lock_run_dirs(lock: bool) -> list[str]:
    """0311 (traverse, no listing) on every <work>/<run_id>/runs while a run is in progress, so an agent that
    walks up from its workspace cannot list sibling workspaces (other tools' results, earlier runs' solutions).
    Known paths keep working; 0755 is restored at the end."""
    changed = []
    for d in sorted(WORK_ROOT.glob("*/runs")):
        try:
            os.chmod(d, 0o311 if lock else 0o755)
            changed.append(d.parent.name)
        except OSError:
            pass
    return changed


def prepare_koda_venv() -> dict:
    """koda runs with its venv interpreter directly (see run_koda.sh). Sync the venv once, exactly like the
    `uv run --project` the runner used before (inexact, lockfile untouched)."""
    run(["uv", "run", "--quiet", "--frozen", "--project", KODA_DIR, "python", "-c", "import koda"], cwd=KODA_DIR)
    py = KODA_DIR / ".venv" / "bin" / "python"
    ver = run([py, "-c", "import sys; print(sys.version.split()[0])"], check=False).stdout.strip()
    return {"interpreter": "<koda>/.venv/bin/python", "python": ver}


def archive_rerun(ctx: Ctx, target: str, reason: str, manifest: dict) -> None:
    """--rerun task/tool/rep: keep the previous result (moved to _rerun_archive/) and record why it is re-run.
    Only for failures traced to the benchmark infrastructure or the provider, never for tool failures."""
    parts = target.strip("/").split("/")
    if len(parts) != 3:
        sys.exit(f"--rerun expects task/tool/rep, got {target!r}")
    src = ctx.res_dir.joinpath(*parts)
    if not src.is_dir():
        sys.exit(f"--rerun: no result at {src}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst = ctx.res_dir / "_rerun_archive" / stamp / "/".join(parts)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    prev = json.loads((dst / "check.json").read_text()) if (dst / "check.json").is_file() else {}
    manifest.setdefault("reruns", []).append({
        "target": "/".join(parts), "reason": reason, "at": utcnow(), "previous_pass": prev.get("pass"),
        "archived_to": str(dst.relative_to(ctx.res_dir))})


def cmd_run(args) -> int:
    for tool in args.tools:
        if tool not in TOOLS:
            sys.exit(f"unknown tool {tool}")
    run_id = args.run_id or (("dryrun-" if args.dry_run else "run-") + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if not re.fullmatch(r"[\w.-]+", run_id):
        sys.exit("run id: letters, digits, . _ - only")
    if args.rerun and not args.rerun_reason:
        sys.exit("--rerun needs --rerun-reason (only infrastructure failures are re-run)")
    assert_outside_git(WORK_ROOT)
    tasks = load_tasks(args.tasks)
    warm = load_tasks(["_warmup"], include_warmup=True)[0]
    key = read_key()
    if not key:
        sys.exit("DEEPSEEK_API_KEY not available (export it or put it in $KODA_DIR/.env)")
    ctx = Ctx(run_id=run_id, run_dir=WORK_ROOT / run_id, res_dir=RESULTS_DIR / run_id,
              checker_py=ensure_checker_venv(), key=key, san=Sanitizer(), effort=args.effort,
              proxy="1", timeout_override=args.timeout, reviewer_chars=koda_reviewer_prompt_chars())
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    (ctx.run_dir / "runs").mkdir(exist_ok=True)
    ctx.res_dir.mkdir(parents=True, exist_ok=True)
    koda_env = prepare_koda_venv() if "koda" in args.tools else None
    hermes_prev = None
    if "hermes" in args.tools:
        # leftovers of earlier runs/invocations (session DB, scratch scripts) must not be visible to this run
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # (kept under runs/, which is not listable during a run)
        hermes_prev = move_hermes_volatile(ctx.run_dir / "runs" / "_hermes_prev_state" / stamp)
        snapshot_hermes_baseline(ctx)

    reps = {t.id: (1 if args.dry_run else (args.reps or t.reps)) for t in tasks}
    manifest_p = ctx.res_dir / "manifest.json"
    manifest = json.loads(manifest_p.read_text()) if manifest_p.is_file() else {}
    for target in args.rerun or []:
        archive_rerun(ctx, target, args.rerun_reason, manifest)
    schedule = [(t.id, f"r{r}") for t in tasks for r in range(1, reps[t.id] + 1)]
    random.Random(args.seed).shuffle(schedule)
    inv = manifest.get("invocations", 0) + 1
    manifest.update({
        "run_id": run_id, "mode": "dry-run" if args.dry_run else "full",
        "created_at": manifest.get("created_at") or utcnow(), "seed": args.seed, "tools": args.tools,
        "tasks": {t.id: {"category": t.category, "reps": reps[t.id], "title": t.cfg.get("title")} for t in tasks},
        "schedule": schedule, "timeout_s": args.timeout or 300, "effort_requested": args.effort,
        "koda_commit": git_rev(KODA_DIR), "suite_hash": suite_hash(), "host": {
            "os": platform.platform(), "machine": platform.machine(), "python_ws": WS_PYTHON,
            "pytest": PYTEST_VERSION},
        "koda_launch": koda_env,
        "isolation": {
            "tool_env": "allowlist (runners/common.sh BENCH_ENV_ALLOW_BASE + per-tool BENCH_TOOL_ENV), TERM pinned",
            "run_dirs_unlistable_during_run": True,
            "hermes_volatile_state_reset_per_run": list(HERMES_VOLATILE) if "hermes" in args.tools else None,
            "tmp_audit": str(TMP_ROOT),
        },
        "invocations": inv,
    })
    manifest.setdefault("invocation_log", []).append({
        "n": inv, "started_at": utcnow(), "hermes_prev_state_moved": hermes_prev,
        "reruns": args.rerun or []})
    manifest_p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"run {run_id}: {len(schedule)} rounds x {len(args.tools)} tools (+ warm-up), work dir {ctx.run_dir}")

    locked = lock_run_dirs(True)
    try:
        if not args.no_warmup:
            log(f"[warm-up] {warm.id} (discarded)")
            o = run_round(ctx, warm, f"w{inv}", args.tools, {"round_index": 0, "warmup": True},
                          res_base=ctx.res_dir / "_warmup")
            log(f"  {fmt_outcome(o)}")

        done_rounds = 0
        for i, (tid, rep) in enumerate(schedule, 1):
            if args.max_rounds and done_rounds >= args.max_rounds:
                log(f"stopping after --max-rounds {args.max_rounds}")
                break
            task = next(t for t in tasks if t.id == tid)
            t0 = time.monotonic()
            o = run_round(ctx, task, rep, args.tools, {"round_index": i, "round_total": len(schedule),
                                                        "seed": args.seed, "invocation": inv})
            if o:
                done_rounds += 1
                log(f"[{i}/{len(schedule)}] {tid} {rep} ({time.monotonic() - t0:.0f}s) {fmt_outcome(o)}")
    finally:
        lock_run_dirs(False)
    manifest = json.loads(manifest_p.read_text())
    manifest["finished_at"] = utcnow()
    manifest["invocation_log"][-1]["finished_at"] = manifest["finished_at"]
    manifest["invocation_log"][-1]["run_dirs_locked"] = locked
    manifest_p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    import aggregate  # noqa: E402  (same directory)
    aggregate.main([str(ctx.res_dir)])
    log(f"results: {ctx.res_dir}")
    return 0


def apply_fixture(fx: Path, p: RunPaths) -> None:
    if (fx / "overlay").is_dir():
        shutil.copytree(fx / "overlay", p.ws, dirs_exist_ok=True)
    if (fx / "apply.sh").is_file():
        run(["bash", fx / "apply.sh"], cwd=p.ws, env=git_env({"WS": str(p.ws), "OUT": str(p.out),
                                                               "BENCH_PRIVATE": str(p.private),
                                                               "BENCH_TOOL_HOME": str(p.base / "toolhome"),
                                                               "GIT_CEILING_DIRECTORIES": str(p.ws.parent)}))
    for name in ("answer.txt", "tool_calls.json"):
        if (fx / name).is_file():
            shutil.copy(fx / name, p.out / name)


def cmd_selftest(args) -> int:
    assert_outside_git(WORK_ROOT)
    tasks = load_tasks(args.tasks, include_warmup=True)
    checker = ensure_checker_venv()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = WORK_ROOT / f"selftest-{stamp}"
    san = Sanitizer()
    rows, bad = [], 0
    for t in tasks:
        st = t.cfg.get("selftest") or {"template": "fail", "solution": "pass"}
        cases = [("template", None, st.get("template", "fail")), ("solution", t.dir / "solution", "pass")]
        cases += [(f"negative:{n}", t.dir / "negatives" / n, "fail") for n in st.get("negatives", [])]
        cases += [(f"positive:{n}", t.dir / "positives" / n, "pass") for n in st.get("positives", [])]
        for name, fx, expect in cases:
            p = run_paths(run_dir, t, "selftest", name.replace(":", "-"))
            prepare_workspace(t, p)
            (p.out / "tool_calls.json").write_text('{"calls": []}\n', encoding="utf-8")  # proxy-on, no calls
            (p.out / "answer.txt").write_text("", encoding="utf-8")
            if fx is not None:
                apply_fixture(fx, p)
            res = run_check(t, p, checker, tool_home=p.base / "toolhome")
            got = {True: "pass", False: "fail", None: "n/a"}[res.get("pass")]
            ok = got == expect
            bad += not ok
            rows.append({"task": t.id, "case": name, "expect": expect, "got": got, "ok": ok,
                         "detail": san.obj(res.get("detail"))})
            log(f"{'ok ' if ok else 'BAD'} {t.id:28} {name:32} expect={expect:4} got={got}")
    out = RESULTS_DIR / f"selftest-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "selftest.json").write_text(json.dumps({"created_at": utcnow(), "suite_hash": suite_hash(),
                                                   "cases": rows, "all_ok": bad == 0}, ensure_ascii=False,
                                                  indent=2), encoding="utf-8")
    log(f"\n{len(rows) - bad}/{len(rows)} cases as expected -> {out / 'selftest.json'}")
    return 1 if bad else 0


def cmd_rejudge(args) -> int:
    """Re-run the checks of a finished run on its kept raw artifacts (no tool is run again) and rewrite the
    published check.json files. Used when a check changed after the run; recorded in the manifest with the
    suite hash the checks came from and every run whose judgement changed."""
    res_dir = RESULTS_DIR / args.run_id
    manifest_p = res_dir / "manifest.json"
    if not manifest_p.is_file():
        sys.exit(f"no run {args.run_id}")
    manifest = json.loads(manifest_p.read_text())
    tasks = load_tasks(args.tasks)
    checker = ensure_checker_venv()
    san = Sanitizer()
    changes, n = [], 0
    for t in tasks:
        for tool in TOOLS:
            for rp in sorted((res_dir / t.id / tool).glob("r*")):
                info = json.loads((rp / "run.json").read_text())
                base = Path(info["work_dir"].replace("<work>", str(WORK_ROOT), 1))
                p = RunPaths(base, base / "w" / t.project_dir, base / "out", base / "private")
                if not p.ws.is_dir():
                    sys.exit(f"raw workspace missing for {rp}: {p.ws}")
                check = run_check(t, p, checker, tool_home=tool_home(tool))
                old = json.loads((rp / "check.json").read_text())
                secrets = ((json.loads((p.private / "state.json").read_text()).get("setup") or {}).get("secrets") or {})
                s_ = json.dumps(check, ensure_ascii=False)
                for name, val in secrets.items():
                    s_ = s_.replace(val, f"<FAKE_SECRET:{name}>")
                new = san.obj(json.loads(s_))
                o1, o2 = (old.get("detail") or {}).get("outcome"), (new.get("detail") or {}).get("outcome")
                if old.get("pass") != new.get("pass") or o1 != o2:
                    changes.append({"run": f"{t.id}/{tool}/{rp.name}", "old": [old.get("pass"), o1],
                                    "new": [new.get("pass"), o2]})
                new["rejudged_at"] = utcnow()
                (rp / "check.json").write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
                n += 1
    manifest.setdefault("rejudged", []).append({
        "at": utcnow(), "tasks": [t.id for t in tasks], "runs": n, "suite_hash_of_checks": suite_hash(),
        "reason": args.reason, "judgement_changes": changes})
    manifest_p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"re-judged {n} runs; judgement changes: {changes or 'none'}")
    import aggregate  # noqa: E402
    aggregate.main([str(res_dir)])
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("selftest")
    s.add_argument("--tasks", nargs="*")
    r = sub.add_parser("run")
    r.add_argument("--run-id")
    r.add_argument("--dry-run", action="store_true", help="1 rep per task")
    r.add_argument("--tasks", nargs="*", help="task ids or prefixes (default: all)")
    r.add_argument("--tools", nargs="*", default=list(TOOLS))
    r.add_argument("--reps", type=int, help="override reps for every task")
    r.add_argument("--seed", type=int, default=DEFAULT_SEED)
    r.add_argument("--timeout", type=int, help="override per-run timeout (s)")
    r.add_argument("--effort", default="high")
    r.add_argument("--no-warmup", action="store_true")
    r.add_argument("--max-rounds", type=int)
    r.add_argument("--rerun", action="append", metavar="TASK/TOOL/REP",
                   help="archive this result and run it again (infrastructure failures only; repeatable)")
    r.add_argument("--rerun-reason", help="why the --rerun targets are infrastructure failures (recorded)")
    j = sub.add_parser("rejudge", help="re-run checks on a finished run's kept artifacts (no tool runs)")
    j.add_argument("--run-id", required=True)
    j.add_argument("--tasks", nargs="*")
    j.add_argument("--reason", required=True)
    a = ap.parse_args(argv)
    return {"list": cmd_list, "selftest": cmd_selftest, "run": cmd_run, "rejudge": cmd_rejudge}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
