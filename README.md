# koda

**로컬 LLM 기반 분기 멀티에이전트 coding agent** (langchain 1.x). `router` 가 요청을 분류해 읽기는 **reader**, 파일 변경은 **coder** 로 보내고, 부수효과가 있는 편집 경로에만 **verifier** 를 건다. 각 에이전트는 도구를 자율 호출하며, 외부 **MCP**(문서·웹검색)와 워크스페이스 **스킬**을 스스로 판단해 쓴다.

- **분기 실행** — `router → { reader → 끝 | [plan →] coder → verifier }`. 복잡 편집은 독립 plan 노드가 체크리스트를 만들고 coder 가 순서대로 수행한다. 전 경로 async.
- **컨텍스트 효율** — 줄번호·범위 읽기, 검색 캡, 일괄 탐색 셸, `explore` 위임(요약만 반환), 에이전트별 도구 스텝 상한, 결정론 히스토리 트림.
- **안전** — 정적 deny gate → 승인 게이트(default-deny) → bubblewrap 격리 → 출력 시크릿 redaction. 모든 도구 경로가 워크스페이스에 샌드박스된다.
- **원자적 편집** — 임시파일+교체 쓰기(실행비트 보존), 라인패치(앵커 자기검증), 다중 편집(전체 롤백), 실패 시 근접 후보 안내.
- **멀티턴·메모리** — 세션 저장/재개, 영구 메모리 주입, 히스토리 문맥 압축, llama.cpp 프롬프트 캐시(멀티턴 84~87% 히트).
- **스트리밍·추적** — TTY 는 Rich 마크다운 실시간 렌더, langfuse 로 툴·모델 IO + 단계별 토큰 추적.

## 요구사항

- Python ≥ 3.13, [uv](https://docs.astral.sh/uv/), Docker + Compose (langfuse 스택)
- (운영) llama.cpp OpenAI 호환 엔드포인트. 미가용 시 `.env` 의 OpenAI API 폴백.

## 빠른 시작

```bash
# 설치 — 어디서든 koda 실행
uv tool install .                 # TUI 포함은 "koda[tui]"	단일 설치 스크립트로 개선 필요.
mkdir -p ~/.config/koda && cp .env.example ~/.config/koda/.env		# 개선 필요.
#   설정 탐색 순서: CWD/.env → ~/.config/koda/.env → 환경변수

# 개발 — 레포에서
uv sync
cp .env.example .env
docker compose up -d              # langfuse → http://localhost:3002
uv run pytest --ignore=tests/test_live_llm.py
```

**실행** (설치본은 `koda`, 레포에서는 `uv run koda`)

```bash
# 단발 — 답변 스트리밍 + 단계 표시
koda run "calc.py에 두 수를 빼는 sub 함수를 추가해줘" --workspace . --yes
koda run "이 프로젝트 구조를 설명해줘" --workspace .

# 멀티턴 대화 — 세션 저장/재개, 영구 메모리
koda chat --workspace . --yes
#   » calc.py 만들어 add 함수 작성해줘
#   » 방금 만든 파일에 mul 함수도 추가해줘      ← 히스토리로 지시어 해소
#   » /remember 함수 이름은 snake_case 로       ← 영구 메모리 (새 세션에도 적용)
#   » /exit
koda chat --workspace . --resume <session-id>

# TUI (기본 진입점) — 대화 스트림 + 상태/검증/메모리 패널 + 위험 도구 승인 modal
#   서브커맨드 없이 `koda` 만 실행하면 TUI 로 진입한다. --yes/--workspace/--resume 등도 그대로 받는다.
koda                        # = koda tui
koda --yes                  # 위험 도구 자동 승인으로 시작
koda --resume <session-id>  # 저장된 세션 재개
koda tui --workspace .      # 명시적 형태도 동일
```

- `--yes` 없이 실행하면 위험 도구(쓰기·삭제·명령·MCP 호출) 전에 승인을 묻는다. 비대화형(파이프)에서 `--yes` 가 없으면 안전하게 거부한다.
- `--no-mcp` 로 외부 MCP 없이 로컬만 빠르게 실행한다.

## 아키텍처

langgraph `StateGraph` 분기: **router → { reader → 끝 | [plan →] coder → verifier(fail 시 plan 재수립 후 재시도 1회) }**.

- **LLM** — 로컬 llama.cpp 우선 + OpenAI 폴백 (`init_chat_model`).
- **에이전트** — `AgentSpec` 레지스트리 + `create_agent`. reader/coder/planner + 컨텍스트 격리용 `explore`(위임 탐색). verifier 는 diff 기반 단일 판정.
- **구조화 출력** — router `{route, plan_needed}`·verifier `{verdict, reasoning}` 를 llama.cpp `json_schema`(문법 강제)로. 실패 시 텍스트 폴백.
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

1. **정적 deny gate** (`denygate.py`) — `sudo` · `curl|sh` · 대량 삭제 · `find -exec` 등은 `--yes` 로도 우회 불가, 시크릿 접근은 대화형 승인 필수.
2. **승인 게이트** (`PermissionMiddleware`) — 읽기 외 모든 도구를 HITL 게이트(default-deny). sync·async 양쪽 훅에 걸려 MCP(async) 경로도 우회 불가.
3. **샌드박스** — 모든 경로가 워크스페이스 루트에 confine(`.koda/` 비노출). bubblewrap 설치 시 셸을 루트 read-only OS 격리로 실행.
4. **시크릿 redaction** — 접근 경로와 무관하게 모든 도구 출력의 시크릿 패턴을 마스킹(`.env`·키 유출 차단).

## MCP 확장

외부 MCP 서버를 `mcps/` 폴더의 `*.json`(또는 `*.yaml`)으로 단일화해 일괄 로드하고 reader·coder·planner 에 주입한다. LLM 이 최신 문서·웹검색이 필요하다고 판단하면 스스로 호출한다.

```jsonc
// mcps/exa.json — 웹 검색
{ "mcpServers": { "exa": { "url": "https://mcp.exa.ai/mcp" } } }
// mcps/context7.json — 최신 라이브러리 문서
{ "mcpServers": { "context7": { "url": "https://mcp.context7.com/mcp" } } }
```

- `url` 만 있으면 `streamable_http`, `command` 면 `stdio` 로 자동 채운다.
- 서버별 격리 로드(하나가 죽어도 나머지 유지), 오프라인이면 조용히 건너뛴다.
- 새 MCP 추가 = 파일 하나. 코드 수정 불필요.

## 멀티턴과 메모리

```
<workspace>/.koda/
  ├─ memory.md            # 영구 메모리 — /remember + 세션 종료 시 LLM 자동 추출, 새 세션에 주입
  └─ sessions/<id>.json   # 세션(히스토리) — chat --resume <id> 로 재개
```

- 세션이 히스토리를 유지·영속해 "방금 만든 파일에 …" 같은 지시어를 해소한다.
- 히스토리가 임계(≈12k tok)를 넘으면 오래된 턴을 LLM 요약으로 치환한다(최근 4턴 원본 유지, user/assistant 쌍 경계 보존).
- 안정 prefix(system + 메모리 + 도구 고정) + append-only 히스토리 구조로 llama.cpp 프롬프트 캐시를 극대화한다.

## 추적 (langfuse)

로컬 파일로 저장하지 않고 langfuse(`http://localhost:3002`)에 기록한다. 실행 1건 = 1 trace 이며, 각 에이전트의 툴 호출 IO와 모델 호출별 토큰(input/output/total)이 관측으로 남아 재구성·비용 분석이 가능하다.
