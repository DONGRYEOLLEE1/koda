# OpenAI 호환 엔드포인트 (vLLM · 프록시 · 자체 호스팅)

모델명으로 프로필이 유추되지 않는 모든 경우. base_url 을 직접 주고, 파라미터 계열은 서버가 받는 것으로 맞춘다.

## 환경변수

Linux / macOS

```bash
export MODEL=my-model
export OPENAI_BASE_URL=https://my-host/v1
export OPENAI_API_KEY=...               # 서버가 키를 안 보면 아무 값이나
```

Windows (PowerShell)

```powershell
$env:MODEL = "my-model"
$env:OPENAI_BASE_URL = "https://my-host/v1"
$env:OPENAI_API_KEY = "..."
```

## 실행

```bash
cd my-project
koda
```

## 파라미터 계열

프로필이 없으니 기본값은 `REASONING_STYLE=effort`, `STRUCTURED_METHOD=function_calling` 이다. 서버가 거절하면 아래로 바꾼다.

| 서버 | reasoning | 구조화 출력 |
|---|---|---|
| vLLM (reasoning 모델) | `effort` | `json_schema` (guided decoding) 또는 `function_calling` |
| vLLM (일반 모델) · 대부분의 프록시 | `off` | `function_calling` |
| llama.cpp | [llama.cpp.md](llama.cpp.md) 를 따른다 | |

`STRUCTURED_METHOD=auto` 는 `json_schema` → `function_calling` 순으로 탐색하되, 미지원 방식에서 프로세스마다 400 을 한 번 맞는다.

## 문제 해결

| 증상 | 조치 |
|---|---|
| `Unrecognized request argument: reasoning_effort` | `REASONING_STYLE=off` |
| `response_format … unavailable` | `STRUCTURED_METHOD=function_calling` |
| 도구 호출이 안 됨 | 서버가 `tools` 를 지원하는지 확인 (vLLM 은 `--enable-auto-tool-choice --tool-call-parser`) |
