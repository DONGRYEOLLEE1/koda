# 설정

[English](configuration.md) · **한국어** · [README](../README.ko.md) 로 돌아가기

셸 환경변수 → 현재 디렉터리의 `.env` → `~/.koda/.env` 순으로 읽고, 앞의 것이 이긴다. 전체 항목은 [`.env.example`](../.env.example).

## provider

| provider | `MODEL` | 키 | 비고 |
|---|---|---|---|
| **DeepSeek** (기본) | `deepseek-*` (기본 `deepseek-v4-flash`) | `DEEPSEEK_API_KEY` | Chat Completions, 구조화 출력은 function calling. `deepseek-v4-flash` 는 DeepSeek 가 아직 받아 주는 퇴역한 이름이고, 요청은 DeepSeek-V4.1-Flash 가 처리한다. DeepSeek 문서와 데모·벤치마크는 `deepseek-flash` 를 쓴다. |
| **OpenAI** | `gpt-*`, `o1`·`o3`·`o4`, `chatgpt-*` | `OPENAI_API_KEY` | Chat Completions 가 도구와 reasoning 을 함께 받지 않아 Responses API 로 보낸다. reasoning 을 모르는 모델(gpt-4o 류)은 `REASONING_STYLE=off`. |
| **llama.cpp** (로컬) | 임의, `LOCAL_MODEL_NAME` 으로 | 서버가 요구하면 `LOCAL_API_KEY` | `PRIMARY=local LOCAL_OPENAI_BASE_URL=http://localhost:8080/v1 REASONING_STYLE=qwen STRUCTURED_METHOD=json_schema` |

그 밖의 OpenAI 호환 서버(vLLM, 프록시)는 `MODEL`·`OPENAI_BASE_URL`·`OPENAI_API_KEY` 로 붙는다. Anthropic API 는 OpenAI 호환이 아니라 지원하지 않는다.

프로필을 고르는 방식, `PRIMARY=local` 폴백, 세션 중 provider 바꾸기는 [features.ko.md 의 provider](features.ko.md#provider).

## 튜닝

`REASONING_STYLE`, `REASONING_EFFORT`, `THINKING_BUDGET_READER`/`_EXPLORE`, `STRUCTURED_METHOD`, `CONTEXT_BUDGET_TOKENS`, `KODA_MCP_AUTO_APPROVE`, `LANGFUSE_*`.

- `CONTEXT_BUDGET_TOKENS`: [features.ko.md 의 컨텍스트 예산과 프롬프트 캐시](features.ko.md#컨텍스트-예산과-프롬프트-캐시).
- `KODA_MCP_AUTO_APPROVE`: [safety.ko.md](safety.ko.md)(2계층)와 [mcp/](mcp/README.md).
- `LANGFUSE_*`: [features.ko.md 의 추적](features.ko.md#추적-langfuse).

## 더 보기

- provider 별 환경변수 블록과 문제 해결: [providers/](providers/README.md).
- 읽는 순서의 자세한 설명과, 셸에 남아 `.env` 보다 먼저 적용되는 변수 확인법: [onboarding.md 5절](onboarding.md#5-설정이-읽히는-순서).
