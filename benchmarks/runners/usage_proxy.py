# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.9"]
# ///
"""Metering reverse proxy in front of DeepSeek (OpenAI-compatible).

Every harness (koda / opencode / hermes) gets its base_url pointed at this proxy, so all three are measured
the same way, on the wire. Requests and responses are relayed unmodified; the log (JSONL) keeps only a
parameter summary and the usage block of each response.

- The Authorization header is forwarded upstream and never logged.
- Message text is not logged (only sizes and counts), with two opt-in exceptions used by the benchmark:
  --capture-tool-args N   log each tool call the model emits: name + arguments truncated to N chars.
  --watch-regex RE        for tool calls whose arguments match RE, log the first 1500 chars of the tool
                          result when the harness sends it back to the model (safety tasks need to know
                          whether a dangerous call was executed or blocked by the harness).
- DeepSeek puts usage in the last SSE chunk even with stream=true (measured). Requests are not modified.

usage: uv run --script usage_proxy.py --log out.jsonl --port-file port.txt [--upstream https://api.deepseek.com]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import signal
import time
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

HOP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive",
       "accept-encoding", "content-encoding", "proxy-authorization", "upgrade"}

# With the proxy on 127.0.0.1 some tools (hermes with a custom provider) probe for a *local server* type
# (LM Studio / Ollama / llama.cpp / vLLM). That traffic does not exist against api.deepseek.com, so it is
# answered with an immediate 404 instead of being forwarded (still logged).
LOCAL_PROBE_PATHS = {"/api/v1/models", "/api/tags", "/v1/props", "/props", "/version", "/api/version"}
WATCHED_RESULT_CHARS = 1500


def _req_summary(raw: bytes) -> tuple[dict, dict | None]:
    try:
        body = json.loads(raw) if raw else {}
    except Exception:
        return {"parse_error": True, "bytes": len(raw)}, None
    if not isinstance(body, dict):
        return {"bytes": len(raw)}, None
    msgs = body.get("messages") or []
    tools = body.get("tools") or []
    sys_chars = 0
    for m in msgs:
        if isinstance(m, dict) and m.get("role") in ("system", "developer"):
            c = m.get("content")
            sys_chars += len(c) if isinstance(c, str) else len(json.dumps(c, ensure_ascii=False))
    names = []
    for t in tools:
        if isinstance(t, dict):
            fn = t.get("function") or {}
            names.append(fn.get("name") or t.get("name") or "?")
    extra = {k: body[k] for k in ("thinking", "reasoning", "chat_template_kwargs") if k in body}
    return {
        "model": body.get("model"),
        "stream": bool(body.get("stream")),
        "reasoning_effort": body.get("reasoning_effort"),
        "reasoning_extra": extra or None,
        "temperature": body.get("temperature"),
        "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens"),
        "n_messages": len(msgs),
        "system_chars": sys_chars,
        "n_tools": len(tools),
        "tool_names": names,
        "tool_choice": body.get("tool_choice") if not isinstance(body.get("tool_choice"), dict) else "object",
        "request_bytes": len(raw),
    }, body


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(str(p.get("text") or p.get("content") or ""))
            else:
                parts.append(str(p))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False) if content is not None else ""


class SSEScan:
    """Scans an SSE stream line by line for usage, finish_reason and tool calls."""

    def __init__(self) -> None:
        self.buf = b""
        self.usage = None
        self.finish = None
        self.tool_call_ids: set[str] = set()
        self.tool_call_idx: set[int] = set()
        self.calls: dict[int, dict] = {}   # index -> {id, name, args}
        self.chunks = 0
        self.resp_model = None
        self.fingerprint = None

    def feed(self, data: bytes) -> None:
        self.buf += data
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            self._line(line.strip())

    def close(self) -> None:
        if self.buf.strip():
            self._line(self.buf.strip())
        self.buf = b""

    def _line(self, line: bytes) -> None:
        if not line.startswith(b"data:"):
            return
        payload = line[5:].strip()
        if not payload or payload == b"[DONE]":
            return
        try:
            obj = json.loads(payload)
        except Exception:
            return
        self.chunks += 1
        if self.resp_model is None and obj.get("model"):
            self.resp_model = obj.get("model")
        if self.fingerprint is None and obj.get("system_fingerprint"):
            self.fingerprint = obj.get("system_fingerprint")
        if obj.get("usage"):
            self.usage = obj["usage"]
        for ch in obj.get("choices") or []:
            if ch.get("finish_reason"):
                self.finish = ch["finish_reason"]
            for tc in (ch.get("delta") or {}).get("tool_calls") or []:
                idx = tc.get("index") if isinstance(tc.get("index"), int) else len(self.calls)
                if tc.get("id"):
                    self.tool_call_ids.add(tc["id"])
                self.tool_call_idx.add(idx)
                c = self.calls.setdefault(idx, {"id": None, "name": None, "args": ""})
                if tc.get("id"):
                    c["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    c["name"] = fn["name"]
                if fn.get("arguments"):
                    c["args"] += fn["arguments"]

    @property
    def tool_calls(self) -> int:
        return max(len(self.tool_call_ids), len(self.tool_call_idx))

    def call_list(self) -> list[dict]:
        return [self.calls[k] for k in sorted(self.calls)]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", default="https://api.deepseek.com")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--log", required=True)
    ap.add_argument("--port-file", required=True)
    ap.add_argument("--capture-tool-args", type=int, default=0)
    ap.add_argument("--watch-regex", default="")
    args = ap.parse_args()

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    upstream = args.upstream.rstrip("/")
    session = ClientSession(timeout=ClientTimeout(total=None, sock_connect=30, sock_read=900),
                            connector=TCPConnector(limit=64), auto_decompress=True)
    watch_re = re.compile(args.watch_regex) if args.watch_regex else None
    watched: dict[str, dict] = {}     # tool_call_id -> {name, captured}
    seq = 0

    def write(entry: dict) -> None:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def record_calls(entry: dict, calls: list[dict]) -> None:
        if args.capture_tool_args > 0 and calls:
            entry["resp_tool_call_list"] = [
                {"id": c.get("id"), "name": c.get("name"), "args": (c.get("args") or "")[: args.capture_tool_args],
                 "args_chars": len(c.get("args") or "")}
                for c in calls]
        if watch_re is not None:
            for c in calls:
                if c.get("id") and watch_re.search(c.get("args") or ""):
                    watched.setdefault(c["id"], {"name": c.get("name"), "captured": False})
                    entry.setdefault("watched_calls", []).append(c["id"])

    def scan_watched_results(entry: dict, body: dict | None) -> None:
        if not watched or not body:
            return
        found = []
        for m in body.get("messages") or []:
            if not isinstance(m, dict) or m.get("role") != "tool":
                continue
            tid = m.get("tool_call_id")
            w = watched.get(tid)
            if w is None or w["captured"]:
                continue
            w["captured"] = True
            found.append({"id": tid, "name": w["name"],
                          "result_prefix": _content_text(m.get("content"))[:WATCHED_RESULT_CHARS]})
        if found:
            entry["watched_tool_results"] = found

    async def handle(request: web.Request) -> web.StreamResponse:
        nonlocal seq
        seq += 1
        n = seq
        raw = await request.read()
        path = request.rel_url.path_qs
        is_llm = request.method == "POST" and "completions" in request.rel_url.path
        summary, body = _req_summary(raw) if is_llm else (None, None)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        headers["Accept-Encoding"] = "identity"
        t0 = time.time()
        entry: dict = {"seq": n, "t_start": round(t0, 3), "method": request.method, "path": request.rel_url.path}
        if request.method == "GET" and request.rel_url.path in LOCAL_PROBE_PATHS:
            entry.update(status=404, short_circuit="local-server-probe", latency_s=0.0)
            write(entry)
            return web.json_response({"error": "not found"}, status=404)
        if is_llm:
            scan_watched_results(entry, body)
        body = None  # do not keep request bodies around
        try:
            async with session.request(request.method, upstream + path, headers=headers, data=raw or None) as up:
                out_headers = {k: v for k, v in up.headers.items() if k.lower() not in HOP}
                resp = web.StreamResponse(status=up.status, headers=out_headers)
                await resp.prepare(request)
                sse = "text/event-stream" in up.headers.get("content-type", "")
                scan = SSEScan() if sse else None
                rbody = bytearray()
                t_first = None
                async for chunk in up.content.iter_any():
                    if t_first is None:
                        t_first = time.time()
                    try:
                        await resp.write(chunk)
                    except (ConnectionResetError, asyncio.CancelledError):
                        entry["client_disconnected"] = True
                        break
                    if scan:
                        scan.feed(chunk)
                    elif len(rbody) < 4_000_000:
                        rbody += chunk
                try:
                    await resp.write_eof()
                except Exception:
                    pass
                entry["status"] = up.status
                entry["ttfb_s"] = round((t_first or time.time()) - t0, 3)
                if is_llm:
                    entry["req"] = summary
                    if scan:
                        scan.close()
                        entry.update(usage=scan.usage, finish_reason=scan.finish, resp_tool_calls=scan.tool_calls,
                                     sse_chunks=scan.chunks, resp_model=scan.resp_model,
                                     system_fingerprint=scan.fingerprint)
                        record_calls(entry, scan.call_list())
                    else:
                        try:
                            obj = json.loads(bytes(rbody))
                            ch = (obj.get("choices") or [{}])[0]
                            tcs = (ch.get("message") or {}).get("tool_calls") or []
                            entry.update(usage=obj.get("usage"), finish_reason=ch.get("finish_reason"),
                                         resp_tool_calls=len(tcs),
                                         resp_model=obj.get("model"),
                                         system_fingerprint=obj.get("system_fingerprint"))
                            record_calls(entry, [{"id": t.get("id"), "name": (t.get("function") or {}).get("name"),
                                                  "args": (t.get("function") or {}).get("arguments") or ""}
                                                 for t in tcs if isinstance(t, dict)])
                        except Exception:
                            entry["resp_parse_error"] = True
                    if up.status >= 400:
                        entry["error_body"] = bytes(rbody[:600]).decode("utf-8", "replace") if rbody else None
                return resp
        except Exception as e:  # upstream connection failure etc.
            entry.update(status=599, error=f"{type(e).__name__}: {e}"[:300])
            if is_llm:
                entry["req"] = summary
            return web.json_response({"error": {"message": f"proxy upstream error: {type(e).__name__}"}}, status=502)
        finally:
            entry["latency_s"] = round(time.time() - t0, 3)
            write(entry)

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    Path(args.port_file).write_text(str(port))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await runner.cleanup()
    await session.close()


if __name__ == "__main__":
    asyncio.run(main())
