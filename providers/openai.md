# OpenAI

`gpt-*` · `o1/o3/o4*` · `chatgpt-*` 모델. 모델명 접두로 OpenAI 프로필이 잡혀 base_url 과 파라미터 계열이 자동으로 맞춰진다.

## 환경변수

Linux / macOS

```bash
export OPENAI_API_KEY=sk-...
export MODEL=gpt-5.6-terra
```

Windows (PowerShell)

```powershell
$env:OPENAI_API_KEY = "sk-..."
$env:MODEL = "gpt-5.6-terra"
```

키는 https://platform.openai.com/api-keys 에서 발급한다.

## 실행

```bash
cd my-project
koda
```

## 모델

`MODEL` 만 바꾼다. DeepSeek 와 번갈아 쓰려면 키를 둘 다 export 해 두고 `MODEL` 만 바꾸면 된다.

- reasoning 모델(gpt-5 계열 등): 그대로 쓴다.
- reasoning 파라미터를 모르는 모델(gpt-4o 계열 등): `export REASONING_STYLE=off` 를 추가한다.

## 알아둘 것

- 요청은 **Responses API** 로 나간다. Chat Completions 는 도구와 `reasoning_effort` 를 함께 보내면 `400 Function tools with reasoning_effort are not supported` 로 거절하는데, koda 의 reader·coder 는 둘 다 쓴다.
- 구조화 출력은 `json_schema` 다. reasoning 이 켜져 있어도 동작한다.
- 응답의 reasoning 블록은 화면에 표시하지 않는다(text 블록만 본문).

## 문제 해결

| 증상 | 원인 → 조치 |
|---|---|
| `401 Authentication Fails … api key is invalid` (DeepSeek 문구) | `OPENAI_BASE_URL` 이 DeepSeek 로 남아 있음 → `unset OPENAI_BASE_URL` |
| `400 Function tools with reasoning_effort are not supported` | Chat Completions 로 나감 → `OPENAI_USE_RESPONSES_API=false` 가 잡혀 있는지 확인해 지운다 |
| `400 Unsupported parameter: reasoning_effort` | reasoning 을 모르는 모델 → `REASONING_STYLE=off` |
