"""Summarize one run's artifacts (exec.json + proxy log + tool-native logs) into meta.json. Stdlib only.

Token definitions (identical for all three tools, DeepSeek usage fields):
  input_tokens  = sum of prompt_tokens (cache hits included)
  output_tokens = sum of completion_tokens (reasoning included)
  cache_read_tokens / reasoning_tokens are reported separately as subsets of the above.
Top-level token numbers come from the proxy (wire) whenever it is on: it is the only way to meter all three
tools identically and it also catches auxiliary calls (title generation etc.).
Top-level tool_calls prefers each tool's own "executed tools" count (wire value: model_tool_calls_proxy;
the cross-tool comparison column is model_tool_calls_agentic_proxy).
Each tool's self-reported numbers are kept under tool_reported, normalized to the same definitions.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# Structured output (a forced tool_choice with a single schema, returned as a function call) is not "tool use".
# koda receives its router (_Route), verifier (Verdict) and memory judge (MemoryJudgement) outputs this way.
# To apply one definition to all three tools, those calls are excluded on the wire -> model_tool_calls_agentic.
STRUCTURED_SCHEMA_NAMES = {"_Route", "Verdict", "MemoryJudgement"}

# DeepSeek list prices (USD per 1M tokens, PEAK). Off-peak = 50%. Source: https://api-docs.deepseek.com/quick_start/pricing
# (retrieved 2026-09-30). PEAK = Mon-Fri UTC 01:00-04:00 and 06:00-10:00 (Chinese public holidays not modeled).
PRICE_PEAK_USD_PER_M = {
    "deepseek-flash": {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20},
    "deepseek-v4-pro": {"cache_hit": 0.044, "cache_miss": 1.32, "output": 3.96},
}


def _is_structured(r: dict) -> bool:
    q = r.get("req") or {}
    names = q.get("tool_names") or []
    return len(names) == 1 and (q.get("tool_choice") == "object" or names[0] in STRUCTURED_SCHEMA_NAMES)


def _is_peak(started_at: str | None) -> bool | None:
    from datetime import datetime
    if not started_at:
        return None
    try:
        t = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    return t.weekday() < 5 and (1 <= t.hour < 4 or 6 <= t.hour < 10)


def cost_estimate(model: str, cache_hit: int | None, cache_miss: int | None, output: int | None,
                  started_at: str | None) -> dict | None:
    p = PRICE_PEAK_USD_PER_M.get(model)
    if p is None or cache_hit is None or cache_miss is None or output is None:
        return None
    peak = (p["cache_hit"] * cache_hit + p["cache_miss"] * cache_miss + p["output"] * output) / 1e6
    is_peak = _is_peak(started_at)
    return {"usd_peak_basis": round(peak, 6), "usd_offpeak_basis": round(peak / 2, 6),
            "run_started_in_peak_window": is_peak,
            "note": "Compare runs on a single basis (peak or off-peak). Prices: see PRICE_PEAK_USD_PER_M in summarize.py"}


def proxy_summary(log: Path) -> dict | None:
    if not log.is_file():
        return None
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    llm = [r for r in rows if r.get("req") is not None]
    ok = [r for r in llm if r.get("usage")]
    u = lambda r, k: int((r.get("usage") or {}).get(k) or 0)  # noqa: E731
    rd = lambda r: int(((r.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)  # noqa: E731
    main = [r for r in llm if (r["req"].get("n_tools") or 0) > 0]
    # first agentic request (structured-output calls such as koda's router carry a single forced schema)
    agentic = [r for r in main if not _is_structured(r)]
    first_main = (agentic or main)[0]["req"] if main else None
    return {
        "http_requests": len(rows),
        "llm_requests": len(llm),
        "llm_requests_ok": len(ok),
        "llm_requests_error": sum(1 for r in llm if (r.get("status") or 0) >= 400),
        # a request that got no usage block (stream cut by a timeout/kill or a client disconnect): its tokens are
        # missing from the totals -> the run's token numbers are a lower bound
        "llm_requests_without_usage": sum(1 for r in llm if not r.get("usage") and (r.get("status") or 0) < 400),
        "client_disconnects": sum(1 for r in llm if r.get("client_disconnected")),
        "llm_requests_with_tools": len(main),
        "llm_requests_aux": len(llm) - len(main),
        "local_probe_short_circuits": sum(1 for r in rows if r.get("short_circuit")),
        "input_tokens": sum(u(r, "prompt_tokens") for r in ok),
        "output_tokens": sum(u(r, "completion_tokens") for r in ok),
        "reasoning_tokens": sum(rd(r) for r in ok),
        "cache_read_tokens": sum(u(r, "prompt_cache_hit_tokens") for r in ok),
        "cache_miss_tokens": sum(u(r, "prompt_cache_miss_tokens") for r in ok),
        "model_tool_calls": sum(int(r.get("resp_tool_calls") or 0) for r in llm),
        "model_tool_calls_agentic": sum(int(r.get("resp_tool_calls") or 0) for r in llm if not _is_structured(r)),
        "llm_requests_structured_output": sum(1 for r in llm if _is_structured(r)),
        "reasoning_effort_values": dict(Counter(str(r["req"].get("reasoning_effort")) for r in llm)),
        "models": dict(Counter(str(r["req"].get("model")) for r in llm)),
        # model name / fingerprint the server put in its responses (only when usage_proxy recorded them)
        "resp_models": dict(Counter(str(r.get("resp_model")) for r in ok)),
        "system_fingerprints": dict(Counter(str(r.get("system_fingerprint")) for r in ok)),
        "finish_reasons": dict(Counter(str(r.get("finish_reason")) for r in llm)),
        "first_main_request": ({k: first_main.get(k) for k in ("n_tools", "system_chars", "request_bytes",
                                                               "max_tokens", "temperature", "reasoning_effort")}
                               | {"tool_names": first_main.get("tool_names")}) if first_main else None,
        "llm_latency_sum_s": round(sum(float(r.get("latency_s") or 0) for r in llm), 3),
        "errors": [{"seq": r.get("seq"), "status": r.get("status"), "error": (r.get("error_body") or r.get("error") or "")[:300]}
                   for r in llm if (r.get("status") or 0) >= 400][:10],
    }


def native_opencode(out: Path) -> dict:
    p = out / "stdout.txt"
    if not p.is_file():
        return {"available": False, "reason": "no stdout.txt"}
    steps = tools = errors = 0
    tin = tout = treason = tcache_r = tcache_w = 0
    tool_names: Counter = Counter()
    session = None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        session = session or ev.get("sessionID")
        t = ev.get("type")
        part = ev.get("part") or {}
        if t == "step_finish":
            steps += 1
            tk = part.get("tokens") or {}
            cache = tk.get("cache") or {}
            tin += int(tk.get("input") or 0)
            tout += int(tk.get("output") or 0)
            treason += int(tk.get("reasoning") or 0)
            tcache_r += int(cache.get("read") or 0)
            tcache_w += int(cache.get("write") or 0)
        elif t == "tool_use":
            tools += 1
            tool_names[part.get("tool") or "?"] += 1
        elif t == "error":
            errors += 1
    return {
        "available": steps > 0,
        "source": "opencode run --format json: sum of step_finish.tokens / count of tool_use events",
        "session_id": session,
        "llm_steps": steps,
        # opencode reports input without cache.read and output without reasoning -> add them back
        "input_tokens": tin + tcache_r + tcache_w,
        "output_tokens": tout + treason,
        "reasoning_tokens": treason,
        "cache_read_tokens": tcache_r,
        "raw": {"input": tin, "output": tout, "reasoning": treason, "cache_read": tcache_r, "cache_write": tcache_w},
        "tool_calls": tools,
        "tool_names": dict(tool_names),
        "error_events": errors,
        "note": "the session-title call (small_model) is not included in the JSON events",
    }


def _hermes_session_tools(db: str | None, sid: str | None) -> dict | None:
    """Count executed tools for the session (+ child sessions) in hermes state.db (SQLite, read-only)."""
    if not db or not sid or not Path(db).is_file():
        return None
    import sqlite3
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ids = [sid] + [r[0] for r in c.execute("select id from sessions where parent_session_id=?", (sid,))]
        q = ",".join("?" * len(ids))
        tool_msgs = c.execute(f"select count(*) from messages where session_id in ({q}) and role='tool'", ids).fetchone()[0]
        names = dict(c.execute(f"select tool_name, count(*) from messages where session_id in ({q}) and role='tool' group by tool_name", ids).fetchall())
        row = c.execute("select tool_call_count, api_call_count, message_count from sessions where id=?", (sid,)).fetchone()
        return {"sessions": ids, "tool_result_messages": tool_msgs, "tool_names": names,
                "session_row": dict(zip(("tool_call_count", "api_call_count", "message_count"), row)) if row else None}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def native_hermes(out: Path, db: str | None = None) -> dict:
    j = _load_json(out / "hermes_usage.json")
    if not j:
        return {"available": False, "reason": "--usage-file output (hermes_usage.json) missing or unparsable"}
    sess = _hermes_session_tools(db, j.get("session_id"))
    aux = j.get("auxiliary") or {}
    main_in = int(j.get("input_tokens") or 0) + int(j.get("cache_read_tokens") or 0) + int(j.get("cache_write_tokens") or 0)
    aux_in = int(aux.get("input_tokens") or 0) + int(aux.get("cache_read_tokens") or 0) + int(aux.get("cache_write_tokens") or 0)
    return {
        "available": True,
        "source": "hermes -z --usage-file JSON (main + auxiliary)",
        "session_id": j.get("session_id"),
        "llm_steps": int(j.get("api_calls") or 0) + int(aux.get("api_calls") or 0),
        "input_tokens": main_in + aux_in,
        "output_tokens": int(j.get("output_tokens") or 0) + int(aux.get("output_tokens") or 0),
        "reasoning_tokens": int(j.get("reasoning_tokens") or 0) + int(aux.get("reasoning_tokens") or 0),
        "cache_read_tokens": int(j.get("cache_read_tokens") or 0) + int(aux.get("cache_read_tokens") or 0),
        "main_only": {"api_calls": j.get("api_calls"), "input_tokens": main_in, "output_tokens": j.get("output_tokens")},
        "auxiliary_by_task": list((aux.get("by_task") or {}).keys()),
        # --usage-file has no tool-call count -> count role='tool' messages in the session DB (state.db)
        "tool_calls": (sess or {}).get("tool_result_messages"),
        "tool_calls_null_reason": None if (sess or {}).get("tool_result_messages") is not None
        else "no tool-call field in --usage-file and the state.db lookup failed",
        "session_db": sess,
        "completed": j.get("completed"), "failed": j.get("failed"), "turn_exit_reason": j.get("turn_exit_reason"),
    }


def native_koda(out: Path) -> dict:
    j = _load_json(out / "koda_result.json")
    if not j:
        return {"available": False, "reason": "koda_result.json missing or unparsable"}
    cu = j.get("callback_usage") or {}
    res = j.get("result") or {}
    return {
        "available": bool(cu.get("llm_calls")),
        "source": "koda_entry.py: usage_metadata summed via run_task(extra_callbacks=[UsageTap]) + run_task result dict",
        "llm_steps": cu.get("llm_calls"),
        "input_tokens": cu.get("input_tokens"),
        "output_tokens": cu.get("output_tokens"),
        "reasoning_tokens": cu.get("reasoning_tokens"),
        "cache_read_tokens": cu.get("cache_read_tokens"),
        "calls_without_usage": cu.get("calls_without_usage"),
        "tool_calls": res.get("tool_calls"),
        "tool_summary": res.get("tool_summary"),
        "route": res.get("route"), "verdict": res.get("verdict"),
        "empty_final": res.get("empty_final"), "retries": res.get("retries"),
        "note": "calls outside the callback chain (e.g. make_title after run_task) can be missing -> the proxy is the wire truth",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", required=True, choices=["koda", "opencode", "hermes"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--version", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--effort", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--proxy", default="0")
    ap.add_argument("--hermes-db", default=None)
    ap.add_argument("--extra", default=None, help="optional JSON file merged into meta as `extra`")
    a = ap.parse_args()
    out = Path(a.out)
    ex = _load_json(out / "exec.json") or {}
    px = proxy_summary(out / "proxy.jsonl") if a.proxy == "1" else None
    if a.tool == "hermes":
        native = native_hermes(out, a.hermes_db)
    else:
        native = {"koda": native_koda, "opencode": native_opencode}[a.tool](out)

    null_reasons = {}
    if px and px["llm_requests_ok"] > 0:
        tin, tout = px["input_tokens"], px["output_tokens"]
        reas, cache, llm_calls = px["reasoning_tokens"], px["cache_read_tokens"], px["llm_requests"]
        token_source = "proxy: sum of DeepSeek response usage on the wire, auxiliary calls (titles etc.) included"
    elif native.get("available"):
        tin, tout = native.get("input_tokens"), native.get("output_tokens")
        reas, cache, llm_calls = native.get("reasoning_tokens"), native.get("cache_read_tokens"), native.get("llm_steps")
        token_source = "tool_reported: " + native.get("source", "")
    else:
        tin = tout = reas = cache = llm_calls = None
        token_source = None
        null_reasons["tokens"] = "proxy off or silent and no self-report: " + str(native.get("reason"))

    # tool_calls prefers "tools actually executed" (tool's own record). The proxy count is every function call
    # the model emitted, which also includes structured outputs received via function calling (koda router/verifier).
    if native.get("tool_calls") is not None:
        tool_calls, tc_source = native["tool_calls"], "tool_reported(executed tools): " + native.get("source", "")
    elif px:
        tool_calls, tc_source = px["model_tool_calls"], "proxy: sum of tool_calls in model responses (structured-output function calls included)"
    else:
        tool_calls, tc_source = None, None
        null_reasons["tool_calls"] = native.get("tool_calls_null_reason") or "no measurement source"

    meta = {
        "tool": a.tool,
        "tool_version": a.version,
        "model": a.model,
        "reasoning_effort_requested": a.effort,
        "base_url": a.base_url,
        "exit_code": ex.get("exit_code"),
        "timed_out": ex.get("timed_out"),
        "wall_seconds": ex.get("wall_seconds"),
        "input_tokens": tin,
        "output_tokens": tout,
        "tool_calls": tool_calls,
        "reasoning_tokens": reas,
        "cache_read_tokens": cache,
        "llm_calls": llm_calls,
        "token_source": token_source,
        "tool_calls_source": tc_source,
        "model_tool_calls_proxy": px["model_tool_calls"] if px else None,
        "model_tool_calls_agentic_proxy": px["model_tool_calls_agentic"] if px else None,
        "cache_miss_tokens": (px["cache_miss_tokens"] if px and px["llm_requests_ok"] > 0
                              else (tin - cache if isinstance(tin, int) and isinstance(cache, int) else None)),
        "cost_estimate": cost_estimate(
            a.model, cache,
            (px["cache_miss_tokens"] if px and px["llm_requests_ok"] > 0
             else (tin - cache if isinstance(tin, int) and isinstance(cache, int) else None)),
            tout, ex.get("started_at")),
        "null_reasons": null_reasons or None,
        # False when the numbers above are a lower bound: the run timed out (the in-flight request is lost) or a
        # request finished without a usage block. Aggregation reports these runs separately.
        "tokens_complete": (None if not px else
                            (not ex.get("timed_out") and px["llm_requests_without_usage"] == 0)),
        "tokens_incomplete_reason": (None if not px else
                                     ("timed_out" if ex.get("timed_out") else
                                      (f"{px['llm_requests_without_usage']} request(s) without usage"
                                       if px["llm_requests_without_usage"] else None))),
        "tool_env_names": ex.get("env_names"),
        "workspace_changes": _load_json(out / "workspace_changes.json"),
        "tool_state_drift": _load_json(out / "state_drift.json"),
        "tool_reported": native,
        "proxy": px,
        "started_at": ex.get("started_at"),
        "workspace": a.workspace,
        "prompt_file": a.prompt_file,
    }
    if a.extra and (extra := _load_json(Path(a.extra))):
        meta["extra"] = extra
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
