# 사내 vLLM 서버 (LiteLLM 게이트웨이)

사내망의 vLLM 추론 서버를 LiteLLM 게이트웨이(포트 4000)로 호출한다. koda 에서는 `PRIMARY=local` 로 우선시키고, 서버가 죽어 있으면 DeepSeek·OpenAI 키가 있을 때 그쪽으로 폴백한다.

## 환경변수

같은 PC 의 WSL 에서 쓸 때 (게이트웨이가 이 PC 에 떠 있다)

```bash
export PRIMARY=local
export LOCAL_OPENAI_BASE_URL=http://127.0.0.1:4000/v1
export LOCAL_MODEL_NAME=qwen3.8-27b
export LOCAL_API_KEY=<LiteLLM Virtual Key>
export REASONING_EFFORT=medium
```

다른 PC 에서 쓸 때는 주소만 게이트웨이 호스트로 바꾼다(아래 `llm-gateway.example.com` 은 예시).

```bash
export LOCAL_OPENAI_BASE_URL=http://llm-gateway.example.com:4000/v1
```

Windows (PowerShell)

```powershell
$env:PRIMARY = "local"
$env:LOCAL_OPENAI_BASE_URL = "http://llm-gateway.example.com:4000/v1"
$env:LOCAL_MODEL_NAME = "qwen3.8-27b"
$env:LOCAL_API_KEY = "<LiteLLM Virtual Key>"
$env:REASONING_EFFORT = "medium"
```

키는 `inner-inference/.env` 의 `LITELLM_SDK_API_KEY`(LiteLLM Virtual Key)다. 게이트웨이는 키 없이는 `/v1/models` 도 401 을 돌려주므로 반드시 넣는다.

## 실행

```bash
cd my-project
koda --yolo
```

## 모델

게이트웨이가 보고하는 모델: `qwen3.8-27b` · `muse-glimmer-30b` · `internal-default`. `LOCAL_MODEL_NAME` 만 바꾸면 된다. 목록 확인:

```bash
curl -s -H "Authorization: Bearer $LOCAL_API_KEY" $LOCAL_OPENAI_BASE_URL/models
```

## 알아둘 것

- 파라미터 계열은 기본값 그대로 쓴다(`REASONING_STYLE=effort`, `STRUCTURED_METHOD=function_calling`). 실측(`qwen3.8-27b`)으로 `qwen` 스타일, `json_schema`, 도구 + reasoning 도 모두 동작한다.
- **`REASONING_EFFORT` 는 `low`·`medium`·`xhigh` 만 받는다.** `high` 를 주면 reasoning 을 켜는 경로(answer·reader)만 `400 Unexpected reasoning effort high` 로 실패한다. koda 기본값은 `medium` 이지만, 다른 provider 에서 쓰던 값이 셸에 남아 있으면 위 블록처럼 명시한다.
- 서버가 본문과 따로 주는 사고 내용(`reasoning_content`)은 TUI 에서 답변 앞에 접힌 `🧠 사고 · N자` 블록으로 보인다. 쉬운 질문은 사고가 200자 안팎이라 빨리 끝나고, 어려운 문제는 xhigh 에서 8천 자를 넘기도 한다(실측).
- `PRIMARY=local` 이라 헬스체크(`GET /models`, 키 포함)가 통과해야 이 서버로 간다. 통과하지 못하면 DeepSeek·OpenAI 키가 있을 때 그쪽으로 조용히 폴백하니, 어느 모델로 답했는지는 TUI statusline 이나 langfuse trace 의 `provider` 로 확인한다.

## 문제 해결

| 증상 | 원인 → 조치 |
|---|---|
| statusline 에 `deepseek-v4-flash` 가 뜸 (로컬이 아님) | 헬스체크 실패로 폴백됨 → 게이트웨이 기동 여부, `LOCAL_API_KEY`, 주소(WSL 안이면 `127.0.0.1`) 확인 |
| `Unexpected reasoning effort high. Supported types are xhigh (default), medium, and low.` | `REASONING_EFFORT=high` → `medium` |
| `Authentication` 401 | `LOCAL_API_KEY` 가 비었거나 틀림 |
| `curl` 이 `000` / 연결 실패 | WSL 안에서 게이트웨이 호스트 주소를 씀 → `127.0.0.1:4000/v1` 로. 다른 PC 에서는 방화벽·VPN 확인 |
