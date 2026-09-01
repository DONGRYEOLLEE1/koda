# koda

**로컬 LLM 기반 분기 멀티에이전트 coding agent** (langchain 1.x). `router` 가 요청을 분류해 워크스페이스와 무관한 일반 질문은 **answer**, 읽기는 **reader**, 셸 조사는 **investigator**, 파일 변경은 **coder** 로 보내고, 부수효과가 있는 편집 경로에만 **verifier** 를 건다. 각 에이전트는 도구를 자율 호출하며, 외부 **MCP**(문서·웹검색)와 워크스페이스 **스킬**을 스스로 판단해 쓴다.

- **분기 실행** — `router → { answer | reader | investigator → 끝 | [plan →] coder → verifier }`. 일반 질문은 파일 도구 없이 지식으로 답하고(외부 참조만 보유), 복잡 편집은 독립 plan 노드가 체크리스트를 만들고 coder 가 순서대로 수행한다. 전 경로 async.
- **컨텍스트 효율** — 줄번호·범위 읽기, 검색 캡, 일괄 탐색 셸, `explore` 위임(요약만 반환), 에이전트별 도구 스텝 상한, 결정론 히스토리 트림.
- **안전** — 정적 deny gate → 승인 게이트(default-deny) → bubblewrap 격리 → 출력 시크릿 redaction. 모든 도구 경로가 워크스페이스에 샌드박스된다.
- **원자적 편집** — 임시파일+교체 쓰기(실행비트 보존), 라인패치(앵커 자기검증), 다중 편집(전체 롤백), 실패 시 근접 후보 안내.
- **멀티턴·메모리** — 세션 저장/재개, 영구 메모리 주입, 히스토리 문맥 압축, llama.cpp 프롬프트 캐시(멀티턴 84~87% 히트).
- **스트리밍·추적** — TTY 는 Rich 마크다운 실시간 렌더, langfuse 로 툴·모델 IO + 단계별 토큰 추적.

## 요구사항

- Python ≥ 3.13, [uv](https://docs.astral.sh/uv/)
- OpenAI 호환 엔드포인트 하나. 모델명으로 provider(OpenAI·DeepSeek)를 유추하므로 모델명과 키만 있으면 되고, 로컬 llama.cpp 를 쓰려면 `PRIMARY=local` 로 바꾼다.
- (선택) Docker + Compose — langfuse 추적 스택

## 빠른 시작

```bash
git clone https://github.com/DONGRYEOLLEE1/koda && cd koda
uv tool install ".[tui]"          # 어디서든 `koda` 로 실행
export DEEPSEEK_API_KEY=...       # 기본 모델 deepseek-v4-flash 의 키 — 설정은 이것뿐이다
koda                              # TUI 진입 (= koda tui)
```

- `--yolo` 없이 뜨면 위험 도구(쓰기·삭제·명령·MCP) 전에 승인을 묻고, `koda --yolo` 는 자동 승인한다. 재앙적 명령(`sudo`·`rm -rf /` 류)은 `--yolo` 로도 막힌다.
- TUI 슬래시 명령: `/help` 목록 · `/status` 세션·모델·컨텍스트·MCP 상태 · `/model` 세션 중 모델 전환(local·openai·deepseek 중 고르면 모델명·base_url·API 키를 차례로 묻는 창이 뜨고, 단축으로 `/model <모델명> [provider=…] [base_url=…] [key=…]` 도 된다. provider 가 바뀌면 그 프로필을 다시 적용) · `/resume` 저장된 세션을 골라 열고 이어서 진행 · `/rename <이름>` 세션 이름 바꾸기 · `/mcp` MCP 서버 상태·도구 보기와 재연결·끄기 · `/compact` 이력 압축 · `/clear` 새 스레드 · `/remember <내용>` 영구 메모리 · `/thinking [show|hide]` 사고 줄 표시 전환 · `/exit`.
- 줄바꿈은 `Ctrl+J`(터미널이 확장 키를 지원하면 `Alt+Enter`·`Shift+Enter` 도 된다), `Ctrl+Q`/`Ctrl+C` 종료.
- 화면 왼쪽 세션 패널의 머리글은 현재 워크스페이스 이름(`📁 build-harness`)이고, 그 아래에 세션들이 `제목 + 마지막 답변 시각` 으로 보인다. 시각은 그 세션이 마지막으로 답변을 받은 때로 고정되며 목록도 그 순서라, 세션을 오가거나 이름을 바꿔도 순서가 흔들리지 않는다. 행 앞 글리프는 병렬 에이전트 TUI 들의 관례를 따른다. 보고 있는 세션은 초록 `●`, 답변 중은 청록 스피너와 `답변 중`, 승인 대기는 노란 `◐`, 마지막 턴이 실패한 세션은 빨간 `✕` 와 `실패`, 나머지는 회색 `○` 다. 두 번째 줄에는 그 세션이 작업한 git 브랜치와 마지막 답변 시각이 함께 보인다. 목록에서 Enter 로 전환하고, `n` 새 세션, `d` 삭제(확인 모달, 보고 있는 세션과 답변 중인 세션은 불가), `Ctrl+B` 로 접고 펼친다. 터미널이 100열보다 좁으면 접힌 채 시작한다.
- 세션은 서로 독립적으로 돈다. 한 세션이 답변하는 동안 패널로 다른 세션을 열어 바로 질의할 수 있고, 배경 세션의 답변은 자기 대화 영역에 계속 쌓이다가 끝나면 알림이 뜬다. 답변 중인 세션에서는 그 턴이 끝날 때까지 입력창이 잠기므로, 전환은 패널(`Ctrl+B` → ↑↓ → Enter)로 한다. 배경 세션이 위험 도구 승인을 요청하면 화면을 가로채지 않고 패널에 `승인 대기` 로 표시했다가 그 세션을 열 때 묻는다.
- 세션에는 작업한 git 브랜치와 시작 시점의 커밋이 함께 기록된다(`/status` 로 확인). 세션 제목은 첫 실질 턴이 끝난 뒤 질의와 답변을 보고 LLM 이 10~15자로 짓는다. 인사말처럼 내용이 빈약하면 다음 실질 턴에서 한 번 더 시도하고(세션당 최대 2회), 그때까지는 첫 질의 앞부분이 임시로 보인다. 문맥 압축이 일어나면 압축 요약이 제목을 함께 갱신하며, `/rename <이름>` 으로 직접 바꾼 제목은 자동 갱신이 덮지 않는다.
- 입력창 위 상태줄은 `모델 · 승인 모드(yolo·manual) · ⎇ 브랜치 · mcp N · 컨텍스트 게이지` 다. 게이지는 **대화 히스토리가 문맥 압축 임계에 얼마나 다가갔는지**를 보여 준다. 턴마다 실제로 차오르고 100% 에 닿으면 다음 턴에서 압축이 일어나며, 절반을 넘으면 노랑, 80% 를 넘으면 빨강이 된다. koda 는 턴마다 프롬프트를 다시 조립하고 도구 결과를 버리므로, 모델 창 대비 프롬프트 크기는 이 게이지가 아니라 `/status` 의 `프롬프트 최대 / 예산` 에서 본다. 진행 상태는 바로 위 활동바(스피너·단계·경과)가 말하므로 상태줄에 문구로 겹쳐 쓰지 않는다.
- 입력창에서 `/` 를 치면 슬래시 명령 후보가 설명과 함께 뜨고 이어서 치는 글자에 맞춰 좁혀진다. ↑↓ 로 고르고 Tab/Enter 로 채우며, 명령을 다 치면 후보가 사라져 Enter 가 그대로 실행한다.
- 입력창에서 `@` 를 치면 워크스페이스의 파일·폴더 후보가 최대 5건 뜨고, 이어서 치는 글자에 맞춰 바로 갱신된다(경로 접두 → 파일명 접두 → 부분 일치 → 순서 부분열 순). ↑↓ 로 고르고 Tab/Enter 로 채우며, 폴더를 고르면 `폴더/` 까지 채워지고 하위 후보가 이어진다. Esc 로 닫는다.
- `Ctrl+C` 는 단계형이다. 답변 중에 한 번 누르면 그 턴을 중단하고(부분 답변은 히스토리에 남기지 않음), 2초 안에 한 번 더 누르면 세션을 저장하고 종료한다. 대기 중에는 첫 번째가 종료 예고, 두 번째가 종료다. `Ctrl+Q` 는 바로 저장 후 종료.
- 마우스로 드래그해 놓으면 그 텍스트가 클립보드로 복사된다(OSC 52, tmux 는 `set-clipboard on` 필요). 선택이 남아 있으면 `Ctrl+C` 는 중단·종료가 아니라 복사다.
- 턴이 끝나면 답변 아래에 `✓ 완료 · N초`(편집 경로면 검증 결과까지)만 남는다. 경로(route)와 langfuse trace 주소는 진단용이라 표시하지 않으며 `/status` 와 langfuse 에서 본다.
- 편집 도구(`edit_file`·`multi_edit`·`patch_file`)가 만든 diff 는 과정 그룹 밖 대화에 색상 그대로 붙는다. 결과이므로 턴이 끝나 과정이 접혀도 남고, 펼치는 클릭 없이 바로 보인다.
- 답변 전 과정(사고·툴 호출)은 흐린 한 줄 스텝으로 `과정` 그룹 안에 쌓인다. 연속된 읽기·검색·조사는 `⏺ 탐색 · a.py, b.py 읽음 · "add" 검색` 한 줄로 합쳐지고, 모델이 사고 내용(`reasoning_content`)을 따로 주면(vLLM·DeepSeek) `∙ 첫 문장` 으로 보인다. 턴이 끝나면 그룹이 `▸ 과정 · 사고 N · 탐색 N · 편집 N · Ns` 로 접히며, 클릭하면 다시 펼쳐 각 스텝의 출력 전체를 볼 수 있다(접힌 상태에서는 드래그 선택이 되지 않는다).

**모델 전환·로컬 모델** — 모델명으로 provider 를 유추해 base_url·reasoning·구조화 출력 방식을 맞추므로 `MODEL` 과 키만 바꾸면 된다. 회사별 환경변수 블록과 주의사항은 [`providers/`](providers/README.md) 에 있다. 환경변수로 넘기거나 `CWD/.env` · `~/.koda/.env` 에 둔다(환경변수가 파일보다 우선). 전체 항목은 `.env.example`.

```bash
export MODEL=gpt-5.6-terra OPENAI_API_KEY=sk-...                                   # OpenAI 로 전환 (base_url 자동)
export MODEL=my-model OPENAI_BASE_URL=https://my-host/v1 OPENAI_API_KEY=...        # 유추 안 되는 자체 호스팅
export PRIMARY=local LOCAL_OPENAI_BASE_URL=http://localhost:8080/v1 REASONING_STYLE=qwen STRUCTURED_METHOD=json_schema   # llama.cpp 우선, API 는 폴백
```

**자동화·파이프** — TUI 없이 쓰는 진입점. `--no-mcp` 는 외부 MCP 를 건너뛰어 빠르게 뜬다.

```bash
koda run "calc.py에 두 수를 빼는 sub 함수를 추가해줘" --workspace . --yolo
koda chat --workspace . --yolo
```

**개발** — 레포에서.

```bash
uv sync && uv tool install -e ".[tui]"   # editable 설치 — 코드 수정이 재설치 없이 반영된다
uv run pytest --ignore=tests/test_live_llm.py
docker compose up -d
```

## 엔드포인트 호환

모델명 접두로 프로필을 골라 아래 값을 채운다. 어느 칸이든 같은 이름의 환경변수를 주면 그 값이 우선한다. 실측 기준(2026-08). provider 별 환경변수 블록·문제 해결은 [`providers/<이름>.md`](providers/README.md).

| provider | 모델명 (`MODEL`) | base_url | 키 | API 형태 | reasoning (`REASONING_STYLE`) | 구조화 출력 (`STRUCTURED_METHOD`) | 비고 |
|---|---|---|---|---|---|---|---|
| **OpenAI** | `gpt-*`, `o1/o3/o4*`, `chatgpt-*` | `https://api.openai.com/v1` | `OPENAI_API_KEY` | Responses | `effort` → `reasoning_effort` | `json_schema` | Chat Completions 는 도구 + `reasoning_effort≠none` 을 400 으로 거절해 Responses API 로 보낸다. reasoning 파라미터를 모르는 gpt-4o 류는 `REASONING_STYLE=off` |
| **DeepSeek** (기본) | `deepseek-*` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` (없으면 `OPENAI_API_KEY` 겸용) | Chat Completions | `effort` → `reasoning_effort` | `function_calling` | `json_schema` 거절. reasoning 파라미터를 안 보내면 thinking 이 켜진 채라 강제 `tool_choice` 를 거절하므로 `off` 로 두지 말 것. `/anthropic` 경로는 Anthropic SDK 용이라 쓰지 않는다 |
| **llama.cpp** (로컬) | 임의 (`LOCAL_MODEL_NAME`) | `LOCAL_OPENAI_BASE_URL` (기본 `http://localhost:8080/v1`) | `LOCAL_API_KEY` | Chat Completions | `qwen` → `chat_template_kwargs.enable_thinking` + `thinking_budget_tokens` | `json_schema` (문법 강제) | `PRIMARY=local` 로 우선시키고, 죽어 있으면 API 로 폴백. 서버는 `--reasoning-budget` 없이 띄워야 요청별 예산이 먹는다 |
| **사내 vLLM** (LiteLLM 게이트웨이) | `LOCAL_MODEL_NAME=qwen3.8-27b` | `http://127.0.0.1:4000/v1` (WSL 안) · `http://llm-gateway.example.com:4000/v1` | `LOCAL_API_KEY` (Virtual Key) | Chat Completions | `effort` (`REASONING_EFFORT` 는 low·medium·xhigh 만) | `function_calling`·`json_schema` 둘 다 | `PRIMARY=local`. 상세는 [providers/inner-vllm.md](providers/inner-vllm.md) |
| **자체 호스팅·기타 OpenAI 호환** (vLLM 등) | 임의 | `OPENAI_BASE_URL` 명시 | `OPENAI_API_KEY` | Chat Completions | 명시 (기본 `effort`) | 명시 (기본 `function_calling`, 탐색은 `auto`) | 유추되지 않는 모델명은 이 행으로 떨어진다. 파라미터를 거절하면 `off`·`auto` 로 조정 |
| **Anthropic** | — | — | — | — | — | — | OpenAI 호환 API 가 아니라 미지원(별도 어댑터 필요) |

## 아키텍처

langgraph `StateGraph` 분기: **router → { answer | reader | investigator → 끝 | [plan →] coder → verifier(fail 시 plan 재수립 후 재시도 1회) }**.

- **LLM** — OpenAI 호환 엔드포인트 하나(`init_chat_model`). 모델명 접두로 provider 프로필(`gpt-*`→OpenAI, `deepseek-*`→DeepSeek)을 골라 base_url·`REASONING_STYLE`·`STRUCTURED_METHOD` 를 채우고, 명시한 환경변수가 우선한다. `PRIMARY=local` 이면 llama.cpp 를 우선하며 API 는 폴백(반대 방향도 대칭). reasoning 파라미터를 모르는 gpt-4o 류는 `REASONING_STYLE=off`, llama.cpp 는 `qwen` 으로 사고 예산을 쓴다.
- **에이전트** — `AgentSpec` 레지스트리 + `create_agent`. answer/reader/investigator/coder/planner + 컨텍스트 격리용 `explore`(위임 탐색). answer 는 워크스페이스 도구를 쥐지 않아 개념·용어 질문에 검색 루프를 돌지 않는다. verifier 는 diff 기반 단일 판정.
- **구조화 출력** — router `{route, plan_needed}`·verifier `{verdict, reasoning}` 를 `STRUCTURED_METHOD` 방식으로 받는다(기본 `function_calling`, llama.cpp 는 문법 강제 `json_schema`, 빈 값이면 강제력 순 탐색). 실패 시 텍스트 폴백.
- **미들웨어** — 도구 스텝 상한(루프 폭주 차단) → 승인 게이트 → 출력 트림+redaction → 히스토리 트림(토큰 예산).
- **프롬프트** — 외부 `prompts/*.md`, 코드 인라인 금지.

서브에이전트 추가 = `AGENTS` 레지스트리 항목 + `prompts/<name>.md`.

## 도구 (요약)

에이전트가 자율 호출하며, 판단은 LLM·집행(게이트·상한·샌드박스)은 코드가 맡는다.

- **읽기 (무승인)** — `read_file`(줄번호+범위 발췌), `list_files`, `search_text`(캡+glob), `run_readonly_command`(조사용 read-only 셸, 파이프·글롭 일괄 탐색), `explore`(위임 탐색), `read_artifact`, `analyze_image`, `skill`.
- **변경 (승인 게이트)** — `write_file` · `edit_file`(replace_all) · `multi_edit`(전체 롤백) · `patch_file`(라인패치, 앵커 검증) · `create_dir` · `delete_path`, `run_command`(명령 실행).
- **MCP** — `mcps/*.json` 로드, 기본 전부 승인. 읽기 전용은 `KODA_MCP_AUTO_APPROVE` 로 opt-in 자동승인.

편집은 원자적(실행비트 보존)이고, 큰 도구 출력은 8k 에서 트림하되 원문을 `.koda/artifacts/`에 보존(시크릿 redaction)해 `read_artifact` 로 재조회한다 — 증거는 잃지 않고 컨텍스트만 아낀다.

## 안전

1. **정적 deny gate** (`denygate.py`) — `sudo` · `curl|sh` · 대량 삭제 · `find -exec` 등은 `--yolo` 로도 우회 불가, 시크릿 접근은 대화형 승인 필수.
2. **승인 게이트** (`PermissionMiddleware`) — 읽기 외 모든 도구를 HITL 게이트(default-deny). sync·async 양쪽 훅에 걸려 MCP(async) 경로도 우회 불가.
3. **샌드박스** — 모든 경로가 워크스페이스 루트에 confine(`.koda/` 비노출). bubblewrap 설치 시 셸을 루트 read-only OS 격리로 실행.
4. **시크릿 redaction** — 접근 경로와 무관하게 모든 도구 출력의 시크릿 패턴을 마스킹(`.env`·키 유출 차단).

## MCP 확장

외부 MCP 서버를 `*.json`(또는 `*.yaml`) 파일 하나당 하나씩 두면 일괄 로드해 answer·reader·investigator·coder·planner 에 주입한다. LLM 이 최신 문서·웹검색이 필요하다고 판단하면 스스로 호출한다.

- **전역** `~/.koda/mcps/` — 모든 워크스페이스에서 잡힌다(exa·context7 같은 범용).
- **프로젝트** `<workspace>/mcps/` (`--mcps-dir` 로 변경) — 그 프로젝트에만. 같은 서버명이면 전역을 덮어쓴다.

```jsonc
// ~/.koda/mcps/exa.json — 웹 검색
{ "mcpServers": { "exa": { "url": "https://mcp.exa.ai/mcp" } } }
// ~/.koda/mcps/context7.json — 최신 라이브러리 문서
{ "mcpServers": { "context7": { "url": "https://mcp.context7.com/mcp" } } }
```

- `url` 만 있으면 `streamable_http`, `command` 면 `stdio` 로 자동 채운다.
- 설정 값의 `${VAR}` 는 `~/.koda/.env`·`CWD/.env`·환경변수에서 채운다(환경변수가 우선). API 키를 JSON 에 평문으로 두지 않기 위한 것이고, 참조한 변수가 없으면 그 서버는 연결하지 않고 `/mcp` 에 `환경변수 미설정: VAR` 로 표시된다. URL 쿼리에 실린 키 값은 `/mcp` 화면에서 `***` 로 가린다.

```jsonc
// ~/.koda/mcps/exa.json  ·  키는 ~/.koda/.env 의 EXA_API_KEY 에 둔다
{ "mcpServers": { "exa": { "url": "https://mcp.exa.ai/mcp?exaApiKey=${EXA_API_KEY}" } } }
// 헤더로 넘기는 서버
{ "mcpServers": { "hf": { "url": "https://huggingface.co/mcp",
                          "headers": { "Authorization": "Bearer ${HF_TOKEN}" } } } }
```
- 서버별 격리 로드(하나가 죽어도 나머지 유지), 오프라인이면 조용히 건너뛴다.
- 세션은 프로세스가 사는 동안 서버마다 하나를 열어 둔다. Playwright 처럼 서버 안에 상태(열린 페이지)를 두는 도구도 호출 사이에 상태가 유지되고, stdio 서버의 작업 디렉터리는 워크스페이스라 상대 경로로 저장한 파일이 워크스페이스 안에 떨어진다.
- 새 MCP 추가 = 파일 하나. 코드 수정 불필요.
- TUI 는 화면이 뜬 뒤 백그라운드로 서버에 붙는다(시작 로그 없음). 상태줄의 `mcp N` 이 쓸 수 있는 도구 수이고 연결 중엔 `mcp 연결 중`, 실패가 있으면 `(실패 M)` 과 함께 알림이 뜬다. `/mcp` 화면에서 서버별 상태(연결됨·실패·꺼짐)·출처(전역/프로젝트)·전송 방식·도구 목록과 설명을 보고, `r` 로 재연결하거나 `d` 로 이 세션 동안 끌 수 있다. 끈 서버의 도구는 다음 턴부터 빠진다.

## 멀티턴과 메모리

```
<workspace>/.koda/
  ├─ memory/
  │    ├─ semantic.md      # 사실: 사용자·프로젝트에 대한 지속적 사실·선호
  │    ├─ episodic.md      # 경험: 실제로 한 일과 결과 (날짜 포함), 다음 작업의 참고 사례
  │    └─ procedural.md    # 규칙: 사용자가 앞으로 지키라고 한 지시·작업 방식
  └─ sessions/<id>.json   # 세션(히스토리). TUI 에서 /resume 으로 목록 선택·재개
```

- 메모리는 langchain 의 구분(semantic·episodic·procedural)을 따르며, **턴이 끝날 때마다** 판정자 LLM 이 사용자 질의와 답변을 보고 저장할 것이 있는지 유형별로 고른다. 대부분의 턴은 아무것도 저장하지 않고, 저장되면 화면에 `기억(유형): …` 으로 알린다. 세션 종료 시 일괄 추출은 하지 않는다.
- `/remember [semantic|episodic|procedural] <내용>` 으로 직접 넣을 수 있다(유형 생략 시 semantic). 유형별 상한(3,000자)을 넘으면 그 유형만 LLM 이 통합 요약한다.
- 세 유형은 모든 에이전트의 시스템 프롬프트에 소제목별로 주입된다. 규칙(procedural)은 지시로, 경험(episodic)은 비슷한 작업의 참고 사례로 쓰인다.
- 세션이 히스토리를 유지·영속해 "방금 만든 파일에 …" 같은 지시어를 해소한다. 히스토리가 임계(≈12k tok)를 넘으면 오래된 턴을 LLM 요약으로 치환한다(최근 4턴 원본 유지).
- 안정 prefix(system + 메모리 + 도구 고정) + append-only 히스토리 구조로 프롬프트 캐시를 극대화한다.

## 추적 (langfuse)

로컬 파일로 저장하지 않고 langfuse(`http://localhost:28900`)에 기록한다. 턴 1건 = 1 trace 이며, trace 이름은 세션 제목(첫 실질 턴이 끝날 때 질의·답변로 10~15자로 짓고 이후 턴은 그대로 쓴다. 제목이 없는 동안은 질의 앞부분), 입력과 출력은 질의와 최종 답변의 원문이다. 같은 세션의 턴들은 `session_id`(세션 id)로 묶여 langfuse Sessions 뷰에서 한 대화로 보인다. 각 에이전트의 툴 호출 IO와 모델 호출별 토큰(input/output/total)이 그 아래 관측으로 남아 재구성·비용 분석이 가능하다.
