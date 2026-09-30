"""Run one command, measure wall clock, enforce a timeout (kills the whole process group). Stdlib only.

usage: python bench_exec.py --timeout 300 --cwd DIR --stdout F --stderr F --result R.json
                            [--env-allow NAME,NAME,...] [--env-set NAME=VALUE ...] -- cmd args...
stdin is /dev/null (e.g. `opencode run` reads a non-TTY stdin until EOF and would hang).

--env-allow: the child gets ONLY these variables from this process's environment (plus --env-set). Without it
the environment is inherited unchanged. The allowlist keeps the host session (terminal multiplexer, SSH agent,
the calling agent's own variables and tokens) and the benchmark's own variables (repo path, watch regexes)
out of the tool under test. Values never appear on a command line.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--stdout", required=True)
    ap.add_argument("--stderr", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--env-allow", default=None, help="comma-separated variable names passed to the child")
    ap.add_argument("--env-set", action="append", default=[], help="NAME=VALUE forced in the child env")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        print("no command", file=sys.stderr)
        return 2

    env = None
    env_names = None
    if a.env_allow is not None:
        allow = [n for n in a.env_allow.split(",") if n]
        env = {n: os.environ[n] for n in allow if n in os.environ}
        for kv in a.env_set:
            k, _, v = kv.partition("=")
            env[k] = v
        env_names = sorted(env)

    started = datetime.now(timezone.utc).isoformat()
    timed_out = False
    t0 = time.monotonic()
    with open(a.stdout, "wb") as out, open(a.stderr, "wb") as err, open(os.devnull, "rb") as devnull:
        p = subprocess.Popen(cmd, cwd=a.cwd, stdin=devnull, stdout=out, stderr=err, start_new_session=True,
                             env=env)
        try:
            rc = p.wait(timeout=a.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
                try:
                    os.killpg(p.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    p.wait(timeout=grace)
                    break
                except subprocess.TimeoutExpired:
                    continue
            rc = p.returncode if p.returncode is not None else -9
    wall = time.monotonic() - t0
    # also clean up background processes the child left behind (servers etc.)
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    with open(a.result, "w", encoding="utf-8") as f:
        json.dump({"exit_code": rc, "timed_out": timed_out, "timeout_s": a.timeout,
                   "wall_seconds": round(wall, 3), "started_at": started,
                   "ended_at": datetime.now(timezone.utc).isoformat(), "argv0": os.path.basename(cmd[0]),
                   # names only (never values): what the tool process could see
                   "env_names": env_names},
                  f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
