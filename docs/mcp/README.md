# MCP 가이드

MCP(Model Context Protocol) 서버를 붙이면 그 서버의 도구를 koda 의 에이전트가 쓸 수 있다. 웹 검색·최신 라이브러리 문서·브라우저 조작 같은 것들이다. 도구를 언제 쓸지는 LLM 이 판단하고, 코드를 고치지 않고 설정 파일 하나로 붙는다.

## 빠른 시작

```bash
koda mcp add --url https://mcp.context7.com/mcp    # 최신 라이브러리 문서
koda mcp add --url https://mcp.exa.ai/mcp          # 웹 검색
koda mcp list
```

`koda mcp add` 는 기본으로 **전역**(`~/.koda/mcps/`)에 저장하므로 어느 워크스페이스에서 koda 를 띄워도 잡힌다. 저장하기 전에 실제로 붙여 보고, 성공하면 `연결 확인: 성공 · 도구 N개` 를 낸다. 실패하면 이유를 보이고 저장하지 않는다.

## 등록 방법

| 방식 | 명령 | 서버 이름 |
|---|---|---|
| 원격 URL | `koda mcp add --url https://mcp.context7.com/mcp` | URL 호스트에서 추론 → `context7` |
| 문서의 설정 스니펫 | `koda mcp add --json '{"mcpServers":{"exa":{"url":"…"}}}'` | 스니펫의 키 → `exa` |
| 로컬 명령 (stdio) | `koda mcp add -- npx -y @playwright/mcp@latest` | 패키지명에서 추론 → `playwright` |
| 이름 지정 | `koda mcp add hf --url https://huggingface.co/mcp` | `hf` |

- `--` 뒤는 통째로 실행 명령이다
- 이름을 생략하면 URL 호스트나 패키지명에서 추론한다(`mcp-remote https://mcp.linear.app/sse` → `linear`). 파일명은 항상 서버명이라 목록과 파일이 어긋나지 않는다
- 연결 확인을 건너뛰려면 `--no-probe`, 실패해도 저장하려면 `--force`, 연결 대기 시간은 `--timeout` 으로 준다

## 전역과 프로젝트

| 범위 | 위치 | 등록 | 쓰이는 곳 |
|---|---|---|---|
| 전역 (기본) | `~/.koda/mcps/` | `koda mcp add …` | 모든 워크스페이스. exa·context7 같은 범용 서버 |
| 프로젝트 | `<workspace>/mcps/` | `koda mcp add --scope project …` | 그 워크스페이스에서만 |

- 전역을 먼저 읽고 프로젝트를 나중에 읽는다. 같은 서버명이면 프로젝트가 **키 단위로** 덮는다. 그래서 `"enabled": false` 한 줄만 둔 프로젝트 파일로 전역 서버를 그 워크스페이스에서만 끌 수 있다(`koda mcp disable <이름> --here` 가 이 파일을 만든다)
- 프로젝트 폴더는 `--mcps-dir <경로>` 로 바꾼다
- 프로젝트 폴더에는 키가 들어갈 수 있으니, 저장소라면 `.gitignore` 에 `mcps/` 를 넣는다

## 설정 파일

파일 하나에 서버 하나를 둔다. JSON 과 YAML(`.yaml`·`.yml`)을 읽는다.

```jsonc
// ~/.koda/mcps/context7.json
{ "mcpServers": { "context7": { "url": "https://mcp.context7.com/mcp" } } }

// ~/.koda/mcps/playwright.json
{ "mcpServers": { "playwright": { "command": "npx", "args": ["-y", "@playwright/mcp@latest"] } } }
```

- `url` 이 있으면 `streamable_http`, `command` 가 있으면 `stdio` 로 전송 방식을 채운다
- `"command": "npx -y mcp-remote https://…"` 처럼 한 줄로 적어도 실행 파일과 인자로 쪼개 넘긴다
- `"enabled": false` 면 연결하지 않고 프롬프트에서도 뺀다
- stdio 서버의 작업 디렉터리는 `cwd` 를 적지 않으면 워크스페이스다. 서버가 상대 경로로 저장한 파일이 워크스페이스 안에 떨어진다

## API 키 다루기

키는 설정 파일에 평문으로 두지 말고 `${변수}` 로 참조한다.

```bash
koda mcp add exa --url 'https://mcp.exa.ai/mcp?exaApiKey=${EXA_API_KEY}'
koda mcp add hf --url https://huggingface.co/mcp --header 'Authorization: Bearer ${HF_TOKEN}'
koda mcp add gh --env 'GITHUB_TOKEN=${GITHUB_TOKEN}' -- npx -y @modelcontextprotocol/server-github
```

```bash
# ~/.koda/.env
EXA_API_KEY=...
HF_TOKEN=...
GITHUB_TOKEN=...
```

- **작은따옴표로 감싼다.** 큰따옴표면 셸이 `${EXA_API_KEY}` 를 먼저 풀어, 키 값이 설정 파일에 그대로 기록된다
- 값은 `~/.koda/.env` 를 깔고 작업 디렉터리의 `.env` 가 덮으며, 셸 환경변수가 가장 우선한다
- 참조한 변수가 없으면 그 서버는 연결하지 않고 `환경변수 미설정: EXA_API_KEY` 로 표시한다
- URL 쿼리에 실린 키 값은 `/mcp` 화면과 `koda mcp list` 에서 `***` 로 가린다

## 켜고 끄기, 삭제

```bash
koda mcp disable exa            # 정의된 파일에 "enabled": false 를 기록한다. 다음 실행에도 유지된다
koda mcp disable exa --here     # 이 워크스페이스에서만 끈다 (프로젝트 폴더에 오버라이드 파일을 둔다)
koda mcp enable exa
koda mcp remove exa
```

TUI 의 `/mcp` 화면에서도 같은 일을 한다. 서버마다 상태(연결됨·실패·꺼짐)·출처(전역·프로젝트)·전송 방식·도구 목록과 설명이 보인다.

| 키 | 동작 |
|---|---|
| `a` | 스니펫·URL·명령을 붙여 넣어 추가 |
| `x` | 삭제 |
| `r` | 재연결 |
| `d` | 이 세션에서만 끄기 |
| `A` | 켜짐·꺼짐을 설정 파일에 기록 |

추가와 삭제는 바로 반영되어 koda 를 다시 띄울 필요가 없다. 상태줄의 `mcp N` 은 지금 쓸 수 있는 도구 수이고, 연결 중이면 `mcp 연결 중`, 실패한 서버가 있으면 `(실패 M)` 이 붙는다.

## 승인과 자동 승인

MCP 도구는 기본으로 모두 **승인 대상**이다. 무엇을 하는 도구인지 koda 가 알 수 없기 때문이다. 호출 전에 승인을 묻고, `--yolo` 면 자동으로 승인한다.

읽기만 하는 도구는 **도구 이름**을 `KODA_MCP_AUTO_APPROVE` 에 쉼표로 적어 승인 없이 돌게 할 수 있다. 서버 이름이 아니라 도구 이름이며, 실제 이름은 `/mcp` 화면에서 확인한다.

```bash
# ~/.koda/.env  (예시 — 실제 도구 이름으로 바꾼다)
KODA_MCP_AUTO_APPROVE=web_search,query-docs
```

파일을 쓰거나 임의의 주소로 요청을 보낼 수 있는 도구는 넣지 않는다. 승인 없이 실행되므로 그대로 유출 경로가 된다.

## 컨텍스트 비용

붙인 서버의 도구 스키마(이름·설명·인자)는 **모든 LLM 호출의 프롬프트에 실린다.** 도구가 많으면 대화를 시작하기도 전에 예산을 크게 차지한다. 도구 74개를 붙인 환경에서 스키마만 약 60k 토큰이었다.

- `/context` 에서 `MCP 도구` 몫을 따로 본다
- 안 쓰는 서버는 `koda mcp disable` 로 끈다. 꺼진 서버는 연결도 하지 않아 프롬프트에서도 빠진다
- koda 는 프롬프트 상한을 잡을 때 실제 스키마 크기를 재서 그만큼을 비워 둔다. 도구가 많을수록 대화 이력이 일찍 잘린다는 뜻이다

## 동작 방식

- **서버별로 격리한다.** 한 서버가 죽거나 오프라인이어도 나머지는 그대로 쓴다
- **세션을 유지한다.** 프로세스가 사는 동안 서버마다 세션 하나를 열어 둔다. Playwright 처럼 서버 안에 상태(열린 페이지)를 두는 도구도 호출 사이에 상태가 이어진다
- **TUI 는 화면이 뜬 뒤 백그라운드로 붙는다.** 연결을 기다리지 않고 바로 입력할 수 있다
- **stdio 서버의 로그는 파일로 간다.** stderr 는 `<workspace>/.koda/logs/mcp.log` 에 쌓인다. 화면에 섞이면 TUI 가 깨지기 때문이다. 4MB 를 넘으면 다음 연결 때 비운다
- **긴 출력은 자른다.** 도구 출력은 8,000자에서, 웹 검색·크롤 계열은 3,000자에서 자르고, 원문은 `.koda/artifacts/` 에 보존한다. 에이전트가 필요하면 `read_artifact` 로 다시 읽는다. 두 상한은 `CONTEXT_BUDGET_TOKENS` 에 비례해 커진다
- **계획 단계는 벌크 검색을 쓰지 않는다.** planner 에게는 이름에 `web_search`·`crawl`·`scrape`·`deep_research` 가 든 도구를 주지 않는다. 계획을 세우다 과다 조사로 컨텍스트를 넘기는 것을 막으려는 것이다

## 문제 해결

| 표시 (`/mcp` · `koda mcp list`) | 원인 → 조치 |
|---|---|
| `bad.json` · `형식 오류: JSONDecodeError …` | 설정 파일을 읽지 못했다. 쉼표·따옴표를 고친다. 서버명을 알 수 없어 파일명으로 표시한다 |
| `형식 오류: mcpServers 항목이 없다` | `{"mcpServers": {…}}` 래퍼가 빠졌다 |
| `환경변수 미설정: EXA_API_KEY` | `${EXA_API_KEY}` 를 채울 값이 없다 → `~/.koda/.env` 에 넣는다 |
| 연결 실패 (`ConnectError`·`TimeoutError` 등) | 주소·네트워크·키를 확인한다. stdio 서버라면 `.koda/logs/mcp.log` 를 본다 |
| 연결됐는데 도구를 쓰지 않는다 | 도구를 쓸지는 LLM 이 판단한다. "최신 문서를 찾아보고" 처럼 요청에 의도를 드러내면 쓴다 |
| 컨텍스트 게이지가 처음부터 높다 | 도구 스키마 몫이다 → 위의 "컨텍스트 비용" |

`koda --no-mcp` 로 띄우면 MCP 를 통째로 건너뛴다. 문제가 MCP 때문인지 가를 때 쓴다.
