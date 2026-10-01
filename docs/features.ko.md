# 기능

[English](features.md) · **한국어** · [README](../README.ko.md) 로 돌아가기

[TUI 와 세션](#tui-와-세션) · [도구](#도구) · [MCP](#mcp) · [메모리](#메모리) · [컨텍스트 예산과 프롬프트 캐시](#컨텍스트-예산과-프롬프트-캐시) · [provider](#provider) · [추적 (Langfuse)](#추적-langfuse)

## TUI 와 세션

- 왼쪽 패널에 세션이 상태 글리프와 함께 보인다. 보고 있는 세션 `●`, 답변 중(스피너), 승인 대기 `◐`, 실패 `✕`, 대기 `○`. `Ctrl+B` 로 접고 펴며, `n` 새 세션 · `d` 삭제 · `Enter` 전환.
- 세션은 병렬로 돈다. 세션마다 워커가 따로 있어 한 세션에 작업을 맡겨 두고 다른 세션을 열어 계속 일할 수 있다([`tui/app.py`](../src/koda/tui/app.py) 의 세션별 워커 그룹). 배경 세션은 답변을 이어 가다 끝나면 알리고, 승인 요청은 그 세션을 열 때 묻는다.
- 상태줄은 `모델 · 승인 모드 · git 브랜치 · MCP 도구 수 · 컨텍스트 게이지` 다. 게이지는 프롬프트에 실리는 전부(시스템 프롬프트·메모리·스킬·도구 스키마·이력·그 턴의 도구 출력)를 예산과 견주고, `/context` 에서 범주별로 나눠 본다.
- `/` 는 명령, `@` 는 워크스페이스 경로를 자동완성한다. `Ctrl+C` 는 답변 중인 턴을 멈추고, 2초 안에 한 번 더 누르면 저장하고 끝낸다.
- 편집 diff 는 대화에 색상 그대로 남고, 도구 스텝은 턴마다 한 줄로 접힌다. DeepSeek·vLLM 이 주는 `reasoning_content` 는 사고 줄로 보인다.
- 세션은 `.koda/sessions/<id>.json` 에 git 브랜치·시작 커밋과 함께 저장되고, `/resume` 으로 다시 연다. 제목은 첫 실질 턴이 끝난 뒤 LLM 이 짓는다.
- 명령: `/help` `/status` `/context` `/model` `/mcp` `/resume` `/rename` `/clear` `/compact` `/memory` `/remember` `/thinking` `/theme` `/exit`.
- TUI 밖의 `koda run`·`koda chat` 은 TTY 에서 Rich 마크다운으로 실시간 렌더한다. `koda chat` 에 표준입력을 파이프로 넘기면 한 줄씩 배치로 실행한다.

화면 구성과 표시 규칙 전체: [tui.md](tui.md).

## 도구

무엇을 부를지는 모델이 정하고, 게이트·상한·샌드박스는 코드가 집행한다.

| | 도구 |
|---|---|
| 읽기 (무승인) | `read_file`(줄번호·범위) · `list_files` · `search_text`(캡·glob) · `run_readonly_command` · `explore` · `read_artifact` · `analyze_image` · `skill` |
| 변경 (승인 대상) | `write_file` · `edit_file` · `multi_edit` · `patch_file` · `create_dir` · `delete_path` · `run_command` |

- 편집은 원자적이다. 임시 파일에 쓰고 `os.replace` 로 바꿔 실행 비트와 CRLF 를 지킨다. `multi_edit` 는 전부 되거나 전부 안 되고, `patch_file` 은 앵커로 드리프트를 잡으며, 일치하지 않으면 근접 후보를 돌려준다.
- 기본 예산에서 8,000자(웹 검색·크롤 계열은 3,000자)를 넘는 도구 출력은 자르고, 두 상한 모두 `CONTEXT_BUDGET_TOKENS` 에 비례해 커진다. 시크릿을 가린 원문은 `.koda/artifacts/` 에 남아 `read_artifact` 로 다시 읽을 수 있다.
- 스킬: 워크스페이스에 `skills/<이름>/SKILL.md` 를 두면 에이전트가 목록을 보고 필요할 때 본문을 읽는다.

도구마다 어디까지 건드릴 수 있고 `--yolo` 가 무엇을 바꾸는지는 [safety.ko.md](safety.ko.md).

## MCP

```sh
koda mcp add --url https://mcp.context7.com/mcp    # 기본은 전역(~/.koda/mcps), 이 워크스페이스만이면 --scope project. 저장 전에 실제로 붙여 본다
koda mcp add -- npx -y @playwright/mcp@latest      # `--` 뒤는 stdio 실행 명령
koda mcp list | remove <이름> | enable|disable <이름> [--here]
```

- 전역 `~/.koda/mcps/` 와 프로젝트 `<workspace>/mcps/` 를 읽고, 같은 서버명이면 프로젝트가 키 단위로 덮는다. 그래서 `"enabled": false` 한 줄로 그 워크스페이스에서만 끌 수 있다.
- 키는 `${VAR}` 로 참조하고 `.env` 와 환경변수에서 채운다. 채울 값이 없거나 파일이 깨지면 조용히 버리지 않고 `/mcp` 에 드러낸다.
- 서버마다 세션 하나를 프로세스가 사는 동안 유지해, Playwright 처럼 상태가 있는 서버도 호출 사이에 상태가 이어진다.
- MCP 호출은 도구 이름을 `KODA_MCP_AUTO_APPROVE` 에 넣지 않는 한 승인 대상이다.

가이드: [mcp/](mcp/README.md).

## 메모리

- `<workspace>/.koda/memory/` 에 세 유형으로 둔다. `semantic`(사실), `episodic`(한 일과 결과, 날짜 포함), `procedural`(지키라고 한 규칙).
- TUI·`koda chat` 에서 턴이 끝날 때마다 판정 호출이 유형별로 최대 2개(각 200자 이하)를 고른다. 대부분의 턴은 아무것도 저장하지 않고, 저장하면 화면에 알린다. 세션을 끝낼 때 몰아서 추출하지는 않는다. `koda run` 은 읽기만 한다.
- 한 유형이 3,000자를 넘으면 LLM 이 그 유형만 통합 요약한다. `/remember [유형] <내용>` 으로 직접 넣을 수도 있다.
- 세 유형 모두 모든 에이전트의 시스템 프롬프트에 들어간다. 규칙은 지시로, 경험은 비슷한 작업의 참고 사례로 쓰인다.
- 메모리와 별개로, 세션 이력이 턴 사이를 이어 "방금 만든 파일에 …" 같은 지시어를 풀어 준다.

## 컨텍스트 예산과 프롬프트 캐시

컨텍스트 예산은 `CONTEXT_BUDGET_TOKENS`(기본 122k) 하나로 조절한다. 이 값을 바꾸면 이력 트림·세션 압축·도구 출력 상한이 함께 따라 움직인다. 도구 스키마 몫은 실제로 붙은 도구로 재고, 한글 토큰 수는 보정해서 센다. 코드: [`config.py`](../src/koda/config.py), [`middleware/history.py`](../src/koda/middleware/history.py).

- 기본 122k 는 시스템 프롬프트·도구 스키마 약 17k + 압축 후 이력 약 12k + 턴 안의 도구 출력 약 93k 로 잡은 값이다.
- 모델을 부를 때마다 예산에서 시스템 프롬프트·실측 도구 스키마·출력 4,096 토큰을 뺀 만큼으로 이력을 자른다. 첫 사용자 메시지와 도구 호출 쌍은 깨지 않는다.
- 이력이 약 36,000자(예산에 비례)를 넘으면 최근 4턴만 원문으로 두고 나머지를 요약한다. `/compact` 로 직접 할 수도 있다.
- 읽기 상한: 범위 없는 `read_file` 은 400줄·120k자까지, `search_text` 는 기본 80건·최대 200건.
- 도구 스키마도 예산을 먹는다. 도구 44개가 약 12k 토큰, 74개가 약 61k 토큰으로 측정됐다. 안 쓰는 MCP 서버는 끈다.
- 시스템 프롬프트를 정적 → 동적 순서(날짜만 넣고 시각은 넣지 않음)로 쌓고, MCP 도구는 세션당 한 번 읽고, 이력은 압축 사이에는 뒤에 덧붙기만 한다. 그래서 에이전트가 바뀌거나 메모리가 저장되거나 이력이 압축되기 전까지는 프롬프트 앞부분이 캐시에 맞게 유지된다. DeepSeek `deepseek-flash` 로 읽기 전용 질문 5턴짜리 `koda chat` 세션 3개를 잰 결과(2026-09-30), 2~5턴 프롬프트 토큰의 69~74% 가 캐시 적중이었고, 한 턴의 도구 루프 안에서는 중앙값 88% 였다. 다른 에이전트로 경로가 바뀌었거나 그사이 메모리가 저장된 턴의 첫 요청은 14~37% 에 그쳤다([데이터](../benchmarks/results/chat-cache-20260930/README.md)).

## provider

- 모델명 접두로 프로필을 고르므로, provider 를 바꿀 때는 `MODEL` 과 그 provider 의 키만 바꾸면 된다. 세션 중에는 `/model` 로 바꾼다.
- `PRIMARY=local` 이면 로컬 llama.cpp 서버를 먼저 쓰고, 서버가 죽어 있으면 API 키로 폴백한다(반대 방향도 된다).
- `MODEL` 을 환경변수로 바꿨는데 `.env` 에 다른 provider 의 공식 주소가 남아 있으면, koda 가 그 주소와 파생 설정을 새 provider 것으로 바꿔 쓴다. 자체 호스팅 주소는 그대로 둔다.
- 쓸 수 있는 키가 없으면 첫 요청에서 401 을 내는 대신 시작할 때 안내하고 끝난다.

provider 표와 튜닝 항목은 [configuration.ko.md](configuration.ko.md), provider 별 환경변수 블록과 문제 해결은 [providers/](providers/README.md).

## 추적 (Langfuse)

- 로컬 파일로 저장하지 않고 Langfuse 에 기록한다.
- 턴 하나가 trace 하나다. 이름은 세션 제목이고, 입력·출력은 질의와 최종 답변 원문이다. `session_id` 로 묶여 Langfuse Sessions 뷰에서 한 대화로 보인다.
- 그 아래에 에이전트별 도구 입출력과 모델 호출별 토큰이 남는다.
- 키가 없거나 인증 확인에 실패하면 조용히 꺼지고, 추적 실패가 턴을 실패시키지 않는다. `docker compose up -d` 로 로컬 스택(`http://localhost:28900`)을 띄운다.
