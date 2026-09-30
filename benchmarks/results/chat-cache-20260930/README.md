# Prompt-cache check: multi-turn `koda chat` (koda only)

A small measurement behind one sentence in the main README ("Context budget and prompt cache"). It is not a
comparison with other tools and is not part of the `main-20260930` suite.

- **What:** the share of prompt tokens DeepSeek served from its prompt cache (`prompt_cache_hit_tokens /
  prompt_tokens`) in each turn of a 5-turn `koda chat` session.
- **When / what ran:** 2026-09-30 14:00-14:02 UTC, koda 0.1.0 @ 63fda57 (source unmodified), DeepSeek
  `deepseek-flash` (response model checked on every request), koda's default settings (`REASONING_EFFORT`
  medium), `--no-mcp`, no `--yolo`.
- **Session:** the 5 read-only questions in [`questions.txt`](questions.txt), piped to `koda chat` as a stdin
  batch, one line per turn. Workspace: a fresh copy of `tasks/e1-readonly-retry-trace/template/`. HOME was an
  empty directory, so no user memory, MCP servers or `.env` came in. 3 sessions, run one after another.
- **Metering:** the same reverse proxy as the suite (`runners/usage_proxy.py`) logged every request's size and
  `usage` (no message text). A turn = the requests from one router call to the next: router, agent tool loop,
  session title (turn 1 only) and the memory judge that runs after each chat turn.
- **Raw data:** [`requests.json`](requests.json), one row per request.

## Result

| session | turn 1 | turn 2 | turn 3 | turn 4 | turn 5 | turns 2-5 |
|---|---:|---:|---:|---:|---:|---:|
| s1 | 62% | 68% | 85% | 49% | 77% | **74%** (44,928 / 60,765) |
| s2 | 85% | 68% | 63% | 48% | 77% | **69%** (39,680 / 57,699) |
| s3 | 85% | 65% | 70% | 47% | 78% | **70%** (38,016 / 54,209) |

Routes, the same in all three sessions: turn 1 `inspect`, turns 2-3 `read`, turn 4 (a general TCP question)
`answer`, turn 5 `inspect`.

What the per-request rows show:

- **Within a turn's tool loop the prefix is reused:** after the first agent request of a turn, the median
  request got 88% of its prompt from the cache (27 requests, lowest 72%).
- **A turn's first agent request is mostly a miss when its system prompt changed.** Each route has its own
  agent prompt, and koda's chat judges after every turn whether to save a memory, which is part of every agent's
  system prompt. Turn 2 switched from the investigator (turn 1, `inspect`) to the reader (`read`): 32-37% from
  the cache, and turn 4 switched to the answer agent (a general question): 14-20%. In turns 3 and 5 the
  same agent ran again as in an earlier turn; where a memory had been saved in between (5 of these 6
  turns) the first request got 23-31%, and in the one turn without a new memory (s1 turn 3) it got 83%.
  Memories were saved after 2, 4 and 4 of the 5 turns.
- **The cache is shared by the account.** s2 and s3 reached 85% on turn 1 because s1 had just sent the same
  prompts. Turns 2-5 are the numbers to quote; turn 1 depends on what the account sent before.

Limits: n=3 sessions of one question set on one small project; read-only questions only (edit turns were not
measured); one API account.

Proxy note: five requests per session, one per turn after an idle gap, failed on the proxy's own idle upstream
connection (`status 599`, `ClientConnectionResetError`, about 0.2 s) and koda's HTTP client retried them. These
15 attempts carry no usage and are excluded (`proxy_connection_retries_excluded`).
