"""Thin wrapper that runs the koda CLI (`koda run ...`) unchanged and only observes its result and token usage.

koda's source is not modified. `koda.cli.main()` is called as-is, but
- `koda.cli.run_task` is wrapped to capture its result dict (route/verdict/tool_calls/tool_summary, ...), and
- one LangChain callback is attached through run_task's public `extra_callbacks` argument to sum per-call
  usage_metadata (the same callback chain Langfuse sees; calls outside it, e.g. title generation, may be
  missed -- the wire truth is usage_proxy).

usage: <koda>/.venv/bin/python koda_entry.py run "<task>" --workspace <ws> --yolo --no-mcp
Writes its result to the JSON path in the KODA_BENCH_RESULT environment variable (removed from os.environ at
import, so child processes of koda do not inherit it).
"""
from __future__ import annotations

import json
import os
import sys
import time

from langchain_core.callbacks import BaseCallbackHandler

import koda
import koda.cli as cli


class UsageTap(BaseCallbackHandler):
    def __init__(self) -> None:
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.reasoning_tokens = 0
        self.cache_read_tokens = 0
        self.calls_without_usage = 0
        self.tool_starts = 0

    def on_llm_end(self, response, **kwargs) -> None:
        self.llm_calls += 1
        found = False
        for gens in getattr(response, "generations", None) or []:
            for g in gens:
                msg = getattr(g, "message", None)
                um = getattr(msg, "usage_metadata", None) if msg is not None else None
                if um:
                    found = True
                    self.input_tokens += int(um.get("input_tokens") or 0)
                    self.output_tokens += int(um.get("output_tokens") or 0)
                    self.reasoning_tokens += int((um.get("output_token_details") or {}).get("reasoning") or 0)
                    self.cache_read_tokens += int((um.get("input_token_details") or {}).get("cache_read") or 0)
        if not found:
            self.calls_without_usage += 1

    def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        self.tool_starts += 1


TAP = UsageTap()
CAPTURED: dict = {}
# Read the result path, then drop it from the environment so koda's shells (and the model) never see it.
RESULT_PATH = os.environ.pop("KODA_BENCH_RESULT", None)
_orig_run_task = cli.run_task


async def _wrapped_run_task(*args, **kwargs):
    kwargs["extra_callbacks"] = [*(kwargs.get("extra_callbacks") or []), TAP]
    result = await _orig_run_task(*args, **kwargs)
    for k in ("route", "verdict", "tool_calls", "tool_summary", "empty_final", "empty_reason",
              "retries", "empty_retries", "title"):
        CAPTURED[k] = result.get(k)
    return result


cli.run_task = _wrapped_run_task


def main() -> int:
    t0 = time.monotonic()
    rc = 99
    err = None
    try:
        rc = cli.main(sys.argv[1:])
    except SystemExit as e:  # argparse etc.
        rc = e.code if isinstance(e.code, int) else 1
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        rc = 2
    finally:
        out = RESULT_PATH
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump({
                    "exit_code": rc,
                    "error": err,
                    "elapsed_in_process_s": round(time.monotonic() - t0, 3),
                    "koda_module": getattr(koda, "__file__", None),
                    "result": CAPTURED,
                    "callback_usage": vars(TAP),
                }, f, ensure_ascii=False, indent=2)
    return rc


if __name__ == "__main__":
    sys.exit(main())
