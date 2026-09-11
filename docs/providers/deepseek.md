# DeepSeek

koda 의 기본 provider 다. 모델명을 주지 않으면 `deepseek-v4-flash` 를 쓴다.

## 환경변수

Linux / macOS

```bash
export DEEPSEEK_API_KEY=sk-...
export MODEL=deepseek-v4-flash      # 생략 가능 (기본값)
```

Windows (PowerShell)

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
$env:MODEL = "deepseek-v4-flash"
```

키는 https://platform.deepseek.com/api_keys 에서 발급한다. `DEEPSEEK_API_KEY` 가 없으면 `OPENAI_API_KEY` 를 대신 쓴다.

## 실행

```bash
cd my-project
koda
```

## 모델

`deepseek-v4-flash`(기본) · `deepseek-v4-pro`. `MODEL` 만 바꾸면 된다.

## 알아둘 것

- base_url 은 `https://api.deepseek.com` 으로 자동 설정된다. `/anthropic` 이 붙은 주소는 Anthropic SDK 용이라 koda 에서는 쓰지 않는다.
- reasoning 은 `reasoning_effort` 로 제어한다. **`REASONING_STYLE=off` 로 두지 마라** — 파라미터를 안 보내면 thinking 이 켜진 채라 구조화 출력의 강제 `tool_choice` 를 400 으로 거절하고, 그러면 라우터가 텍스트 폴백으로 떨어진다. 사고를 줄이려면 `REASONING_EFFORT=low`.
- 구조화 출력은 `function_calling` 이다. `json_schema`(`response_format`)는 이 API 가 거절한다.

## 문제 해결

| 증상                                                                         | 원인 → 조치                                                                                           |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `The supported API model names are deepseek-v4-pro, deepseek-v4-flash, …` | 다른 provider 의 모델명이`MODEL` 에 남아 있음 → `MODEL` 수정                                      |
| `Authentication Fails, Your api key … is invalid`                         | 다른 provider 의 키가 들어감 →`DEEPSEEK_API_KEY` 확인, `OPENAI_BASE_URL` 이 남아 있으면 `unset` |
| 라우팅이 항상`inspect`, trace 에 `tool_choice` 400                       | `REASONING_STYLE=off` → 지운다                                                                      |
| `This response_format type is unavailable now`                             | `STRUCTURED_METHOD=json_schema` 잔재 → 지운다                                                       |
