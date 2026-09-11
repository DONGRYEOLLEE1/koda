# llama.cpp (로컬)

`llama-server` 의 OpenAI 호환 엔드포인트. `PRIMARY=local` 로 우선시키고, 서버가 죽어 있으면 API 키(DeepSeek·OpenAI)가 있을 때 그쪽으로 폴백한다.

## 서버

```bash
llama-server -m model.gguf --port 8080 --jinja
```

`--reasoning-budget` 플래그 없이 띄운다. 있으면 koda 가 요청마다 보내는 사고 예산(`thinking_budget_tokens`)이 무시된다.

## 환경변수

Linux / macOS

```bash
export PRIMARY=local
export LOCAL_OPENAI_BASE_URL=http://localhost:8080/v1
export LOCAL_MODEL_NAME=Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf
export REASONING_STYLE=qwen STRUCTURED_METHOD=json_schema
```

Windows (PowerShell)

```powershell
$env:PRIMARY = "local"
$env:LOCAL_OPENAI_BASE_URL = "http://localhost:8080/v1"
$env:LOCAL_MODEL_NAME = "Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf"
$env:REASONING_STYLE = "qwen"; $env:STRUCTURED_METHOD = "json_schema"
```

## 실행

```bash
cd my-project
koda
```

## 알아둘 것

- `REASONING_STYLE=qwen` 은 `chat_template_kwargs.enable_thinking` 과 `thinking_budget_tokens` 로 사고를 켜고 끈다. reader 1024 · explore 512 (`THINKING_BUDGET_*`), coder 등은 사고를 끈다.
- `STRUCTURED_METHOD=json_schema` 는 문법 강제라 enum 밖 토큰이 아예 생성되지 않는다. llama.cpp 에서 가장 안정적이다.
- `CONTEXT_BUDGET_TOKENS`(기본 64000)는 서버 `n_ctx` 의 절반 수준으로 맞춘다.
- 위 두 값은 전역이라 **로컬이 죽어 API 로 폴백될 때도 그대로 적용**된다. DeepSeek 는 `json_schema` 를 거절하므로 폴백 중에는 구조화 출력이 텍스트 폴백으로 떨어진다. 폴백을 주로 쓰게 되면 두 변수를 `unset` 한다.
- 이미지 분석(`analyze_image`)은 서버가 `mmproj` 와 함께 떠야 동작한다.
