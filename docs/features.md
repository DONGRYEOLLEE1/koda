# Features

**English** · [한국어](features.ko.md) · back to the [README](../README.md)

[TUI and sessions](#tui-and-sessions) · [Tools](#tools) · [MCP](#mcp) · [Memory](#memory) · [Context budget and prompt cache](#context-budget-and-prompt-cache) · [Providers](#providers) · [Tracing (Langfuse)](#tracing-langfuse)

## TUI and sessions

- A left panel lists sessions with state glyphs: current `●`, answering (spinner), waiting for approval `◐`, failed `✕`, idle `○`. `Ctrl+B` toggles it; `n` new, `d` delete, `Enter` switch.
- Sessions run in parallel. Each session runs on its own worker (per-session worker groups in [`tui/app.py`](../src/koda/tui/app.py)): start a task, open another session and keep going. A background session keeps answering, notifies you when done and holds approval requests until you open it.
- Status line: model · approval mode · git branch · MCP tool count · context gauge. The gauge counts everything in the prompt (system prompt, memory, skills, tool schemas, history, this turn's tool output) against the budget; `/context` breaks it down.
- `/` completes commands, `@` completes workspace paths. `Ctrl+C` stops the running turn; a second press within 2 s saves and quits.
- Edit diffs stay inline in color; tool steps fold into one line per turn; `reasoning_content` from DeepSeek or vLLM shows as thinking lines.
- Sessions live in `.koda/sessions/<id>.json` with the git branch and start commit; `/resume` reopens one; titles are written by the LLM after the first real turn.
- Commands: `/help` `/status` `/context` `/model` `/mcp` `/resume` `/rename` `/clear` `/compact` `/memory` `/remember` `/thinking` `/theme` `/exit`.
- Outside the TUI, `koda run` and `koda chat` stream Rich-rendered Markdown on a TTY; `koda chat` also takes piped stdin as a batch.

Full guide: [tui.md](tui.md) (Korean).

## Tools

The model decides what to call; code enforces gates, caps and the sandbox.

| | Tools |
|---|---|
| Read, no approval | `read_file` (line numbers, ranges) · `list_files` · `search_text` (capped, glob) · `run_readonly_command` · `explore` · `read_artifact` · `analyze_image` · `parse_screen` · `render_page` · `screen_diff` · `skill` |
| Change, approval | `write_file` · `edit_file` · `multi_edit` · `patch_file` · `create_dir` · `delete_path` · `run_command` |

- Edits are atomic: temp file plus `os.replace`, keeping the exec bit and CRLF. `multi_edit` is all-or-nothing, `patch_file` checks an anchor for drift, and a mismatch returns near-miss candidates.
- At the default budget, tool output over 8,000 characters (3,000 for web search and crawl tools) is cut; both caps grow with `CONTEXT_BUDGET_TOKENS`. The redacted original stays in `.koda/artifacts/` for `read_artifact`.
- Skills: put `skills/<name>/SKILL.md` in the workspace; agents see the list and load a body on demand.

- Screen evidence: `render_page` renders a workspace HTML file or a localhost URL in headless chromium, `parse_screen` turns a capture or a render into a list of UI elements with a vision model, and `screen_diff` compares two screens in code. Results go under `.koda/renders/` and the verifier reads them. Needs the `visual` extra: [visual.md](visual.md).

What each tool may touch, and what `--yolo` changes: [safety.md](safety.md).

## MCP

```sh
koda mcp add --url https://mcp.context7.com/mcp    # global (~/.koda/mcps); --scope project for this workspace; connects before saving
koda mcp add -- npx -y @playwright/mcp@latest      # everything after -- is the stdio command
koda mcp list | remove <name> | enable|disable <name> [--here]
```

- Global `~/.koda/mcps/` and per-project `<workspace>/mcps/`; the project file overrides key by key, so one `"enabled": false` line turns a server off for one workspace.
- Keys are referenced as `${VAR}` and resolved from `.env` and the environment. Missing variables and broken files are shown in `/mcp`, not dropped silently.
- One session per server for the life of the process, so stateful servers such as Playwright keep state between calls.
- MCP calls need approval unless the tool name is listed in `KODA_MCP_AUTO_APPROVE`.

Guide: [mcp/](mcp/README.md) (Korean).

## Memory

- Three kinds in `<workspace>/.koda/memory/`: `semantic` (facts), `episodic` (what was done, with dates), `procedural` (rules to follow).
- After each turn in the TUI or `koda chat`, a judge call picks at most two items per kind (200 characters each). Most turns store nothing; saved items are announced. There is no batch extraction at session end. `koda run` only reads memory.
- A kind over 3,000 characters is consolidated by the LLM. `/remember [kind] <text>` adds one by hand.
- All three are injected into every agent's system prompt: rules as instructions, episodes as reference cases.
- Separately, the session history carries references such as "the file you just made" from turn to turn.

## Context budget and prompt cache

One knob: `CONTEXT_BUDGET_TOKENS` (default 122k) scales history trimming, session compaction and tool-output caps together. The tool-schema share is measured from the bound tools, and token estimates are corrected for CJK text. Code: [`config.py`](../src/koda/config.py), [`middleware/history.py`](../src/koda/middleware/history.py).

- The 122k default is sized as ~17k system prompt and tool schemas + ~12k compacted history + ~93k tool output within a turn.
- Before each model call, history is trimmed to the budget minus the system prompt, the measured tool schemas and 4,096 output tokens, keeping the first user message and tool-call pairs intact.
- Past ~36,000 characters of history (scaled with the budget), older turns are summarized and the last 4 are kept verbatim; `/compact` does it on demand.
- Read caps: `read_file` without a range returns up to 400 lines or 120k characters; `search_text` returns 80 hits by default, 200 at most.
- Tool schemas cost real budget: 44 tools measured about 12k tokens, 74 tools about 61k. Disable MCP servers you don't use.
- The system prompt goes static to dynamic (the date only, no clock), MCP tools load once per session and history only grows between compactions, so the prompt prefix stays cacheable until the agent changes, a memory is saved or history is compacted. Measured on DeepSeek `deepseek-flash` in three 5-turn `koda chat` sessions of read-only questions (2026-09-30): 69-74% of the prompt tokens in turns 2-5 were cache hits, a median 88% inside a turn's tool loop. A turn's first request got only 14-37% when the route had switched to another agent or a memory had been saved since ([data](../benchmarks/results/chat-cache-20260930/README.md)).

## Providers

- The model-name prefix picks the profile, so switching providers means changing `MODEL` and that provider's key. `/model` switches mid-session.
- `PRIMARY=local` prefers a local llama.cpp server and falls back to the API key when it's down (and the reverse).
- If `MODEL` is set in the environment but `.env` still holds another provider's official URL, koda switches to the new provider's URL and derived settings. A self-hosted URL is kept.
- A missing key stops koda at startup with instructions instead of a 401 on the first request.

Provider table and tuning variables: [configuration.md](configuration.md). Per-provider blocks and troubleshooting: [providers/](providers/README.md) (Korean).

## Tracing (Langfuse)

- Traces go to Langfuse, not to local files.
- One trace per turn, named after the session title, with the request and final answer as input and output. `session_id` groups a conversation in the Langfuse Sessions view.
- Child observations hold each agent's tool I/O and token usage per model call.
- Without keys, or if the auth check fails, tracing turns off quietly and never fails a turn. `docker compose up -d` starts a local stack at `http://localhost:28900`.
