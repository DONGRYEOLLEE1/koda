# Configuration

**English** · [한국어](configuration.ko.md) · back to the [README](../README.md)

Settings come from shell variables, then `.env` in the current directory, then `~/.koda/.env`; the first one that sets a value wins. Full list: [`.env.example`](../.env.example).

## Providers

| Provider | `MODEL` | Key | Notes |
|---|---|---|---|
| **DeepSeek** (default) | `deepseek-*` (default `deepseek-v4-flash`) | `DEEPSEEK_API_KEY` | Chat Completions, function-calling structured output. `deepseek-v4-flash` is a retired name DeepSeek still accepts and serves with DeepSeek-V4.1-Flash; its docs, the demo and the benchmarks use `deepseek-flash`. |
| **OpenAI** | `gpt-*`, `o1`/`o3`/`o4`, `chatgpt-*` | `OPENAI_API_KEY` | Sent through the Responses API, because Chat Completions rejects tools combined with reasoning. Set `REASONING_STYLE=off` for models without reasoning (gpt-4o). |
| **llama.cpp** (local) | any, via `LOCAL_MODEL_NAME` | `LOCAL_API_KEY` if the server wants one | `PRIMARY=local LOCAL_OPENAI_BASE_URL=http://localhost:8080/v1 REASONING_STYLE=qwen STRUCTURED_METHOD=json_schema` |

Any other OpenAI-compatible server (vLLM, a proxy) works with `MODEL`, `OPENAI_BASE_URL` and `OPENAI_API_KEY`. Anthropic's API is not OpenAI-compatible and isn't supported.

How the profile is picked, local fallback with `PRIMARY=local`, and switching providers mid-session: [features.md → Providers](features.md#providers).

## Tuning

`REASONING_STYLE`, `REASONING_EFFORT`, `THINKING_BUDGET_READER`/`_EXPLORE`, `STRUCTURED_METHOD`, `CONTEXT_BUDGET_TOKENS`, `KODA_MCP_AUTO_APPROVE`, `LANGFUSE_*`.

- `CONTEXT_BUDGET_TOKENS`: [features.md → Context budget and prompt cache](features.md#context-budget-and-prompt-cache).
- `KODA_MCP_AUTO_APPROVE`: [safety.md](safety.md) (layer 2) and [mcp/](mcp/README.md) (Korean).
- `LANGFUSE_*`: [features.md → Tracing](features.md#tracing-langfuse).

## More

- Per-provider blocks and troubleshooting: [providers/](providers/README.md) (Korean).
- Load order in detail and shell variables that silently override `.env`: [onboarding.md §5](onboarding.md#5-설정이-읽히는-순서) (Korean).
