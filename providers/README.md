# providers/

엔드포인트를 회사(provider)별로 한 문서씩 모았다. 각 문서는 **복사해서 붙이는 환경변수 블록 → 실행 → 모델 → 알아둘 것** 순서이고, 설치 스크립트는 없다. 환경변수 몇 줄이 전부라 숨은 상태가 남지 않는다.

| 문서 | 대상 | 필요한 것 |
|---|---|---|
| [deepseek.md](deepseek.md) | DeepSeek (기본 provider) | `DEEPSEEK_API_KEY` |
| [openai.md](openai.md) | OpenAI `gpt-*` · `o*` | `OPENAI_API_KEY` + `MODEL` |
| [llama.cpp.md](llama.cpp.md) | 로컬 `llama-server` | `PRIMARY=local` + 서버 주소·모델 |
| [inner-vllm.md](inner-vllm.md) | 사내 vLLM 서버 (LiteLLM 게이트웨이, 포트 4000) | `PRIMARY=local` + 주소·`qwen3.8-27b`·Virtual Key |
| [openai-compatible.md](openai-compatible.md) | vLLM · 프록시 · 자체 호스팅 | `MODEL` + `OPENAI_BASE_URL` + 키 |

Anthropic 은 OpenAI 호환 API 가 아니라 아직 지원하지 않는다(별도 어댑터 필요).

## 동작 원리

koda 는 `MODEL` 의 접두로 provider 프로필을 유추해 base_url·reasoning 방식·구조화 출력 방식·API 형태를 채운다(`src/koda/config.py` 의 `PROVIDER_PROFILES`). 그래서 provider 전환은 **`MODEL` 과 그 provider 의 키를 바꾸는 것**이 전부다. 어떤 값이든 같은 이름의 환경변수를 주면 프로필보다 우선하므로, 다른 provider 에서 쓰던 `OPENAI_BASE_URL`·`REASONING_STYLE`·`STRUCTURED_METHOD` 가 셸에 남아 있으면 `unset` 한다 — 401·400 의 흔한 원인이 이 잔재다.

환경변수 대신 파일을 쓰려면 같은 내용을 `CWD/.env` 나 `~/.config/koda/.env` 에 둔다(환경변수가 파일보다 우선). 전체 항목은 `.env.example`.

각 provider 가 무엇을 받고 거절하는지(실측)는 루트 README 의 "엔드포인트 호환" 표에 요약돼 있다.

## 새 provider 추가

1. 실측한다: 도구 × reasoning 조합, `json_schema`/`function_calling` 구조화 출력, Chat Completions/Responses 중 무엇이 되는지.
2. `src/koda/config.py` 의 `PROVIDER_PROFILES` 에 행을 추가하고 `infer_provider` 에 모델명 접두를 등록한다.
3. `providers/<이름>.md` 를 위 형식으로 쓰고, 루트 README 의 "엔드포인트 호환" 표에 행을 추가한다.
4. 설정 파일 없는 워크스페이스에서 실제 질의를 돌려 langfuse trace 에 400 이 없는지 확인한다.
