# 온보딩 가이드

설치부터 첫 작업, 설정, 개발 환경까지 순서대로 따라가면 된다. 각 절 끝의 링크는 더 깊은 참고 문서다.

## 1. koda 한눈에

koda 는 워크스페이스(작업 디렉터리) 안에서 파일을 읽고 고치고 명령을 실행하는 코딩 에이전트다. 요청이 들어오면 라우터가 성격을 분류해 알맞은 에이전트로 보낸다.

```
요청 → router ─┬─ answer        워크스페이스와 무관한 일반 질문 (파일 도구 없음)
               ├─ reader        파일을 읽어 답한다
               ├─ investigator  읽기 전용 셸로 조사한다
               └─ [plan →] coder → verify   파일을 바꾸거나 명령을 실행한다
```

읽기 경로는 워크스페이스를 바꾸지 않으므로 검증을 거치지 않는다. 편집 경로만 verify 가 턴 시작 대비 변경분(diff)과 실제 도구 실행 기록을 증거로 판정하고, 실패하면 한 번 다시 시도한다. 에이전트의 "했다" 는 주장은 증거로 치지 않는다.

## 2. 준비물

- Python 3.13 이상, [uv](https://docs.astral.sh/uv/)
- OpenAI 호환 엔드포인트 하나와 그 키. 기본은 DeepSeek(`deepseek-v4-flash`)이고, OpenAI·로컬 llama.cpp·자체 호스팅 서버도 쓸 수 있다
- (선택) Docker — langfuse 추적 스택
- (선택, Linux) bubblewrap — 셸 명령을 OS 수준으로 격리한다. macOS 에는 없으며, 없으면 정적 차단 목록만 적용된다

## 3. 설치

설치 방식이 둘이다. **코드를 고칠 생각이 있으면 개발용으로 설치한다.**

```bash
git clone https://github.com/DONGRYEOLLEE1/koda && cd koda

uv tool install ".[tui]"        # 사용자용: 소스를 복사해 설치한다
uv tool install -e ".[tui]"     # 개발용: 소스 디렉터리를 가리킨다 (editable)
```

| | 사용자용 | 개발용 (`-e`) |
|---|---|---|
| 설치 형태 | site-packages 로 복사 | 소스 디렉터리를 가리키는 링크 |
| 코드 수정 반영 | 재설치해야 반영된다 | 다음 실행부터 반영된다 |
| 다른 워크스페이스에서 실행하면 | 설치 시점의 코드가 돈다 | 저장소의 현재 코드가 돈다 |

어느 쪽이든 `koda` 명령을 어디서나 쓸 수 있다. 이미 설치된 것을 다른 방식으로 바꿀 때는 `--force` 를 붙인다.

개발용으로 설치했어도 다음 경우에는 재설치해야 한다.

- `pyproject.toml` 의 의존성을 추가하거나 버전을 바꿨을 때
- `[project.scripts]` 진입점이나 extra 구성을 바꿨을 때

지금 어떤 코드가 도는지는 이렇게 확인한다.

```bash
"$(uv tool dir)/koda/bin/python" -c "import koda; print(koda.__file__)"
# …/site-packages/koda/__init__.py   → 사용자용 (복사본)
# …/koda/src/koda/__init__.py        → 개발용 (저장소)
```

TUI 없이 자동화 경로만 쓸 거라면 extra 를 빼고 `uv tool install .` 로 설치해도 된다. 그러면 `koda`(TUI)는 설치 안내만 내고 끝나고, `koda run`·`koda chat` 은 그대로 쓴다.

## 4. 첫 실행

```bash
export DEEPSEEK_API_KEY=sk-...    # 기본 모델의 키. 설정은 이것뿐이다
cd my-project
koda                               # TUI 진입 (= koda tui)
```

쓸 수 있는 키가 없으면 첫 요청에서 401 을 내는 대신, 시작할 때 무엇을 넘겨야 하는지 안내하고 끝난다.

승인 모드는 두 가지다.

- `koda`: 파일 쓰기·삭제·명령 실행·MCP 도구를 쓰기 전에 승인을 묻는다
- `koda --yolo`: 자동으로 승인한다. 그래도 재앙적 명령(`sudo`·`rm -rf /`·`curl … | sh`·`git reset --hard` 등)은 막히고, 자격증명·`.env` 접근이나 `git push --force` 처럼 사람이 확인해야 하는 명령은 실행되지 않는다

처음 해 볼 만한 요청이다.

```
» 이 프로젝트 구조 파악해줘
» README 에서 설치 방법만 요약해줘
» calc.py 에 두 수를 빼는 sub 함수를 추가하고 테스트해줘
```

## 5. 설정이 읽히는 순서

설정은 세 곳에서 읽고, 위에 있는 것이 이긴다.

1. 셸 환경변수
2. 작업 디렉터리의 `.env`
3. `~/.koda/.env` (모든 워크스페이스에 공통으로 깔린다)

키처럼 어디서나 쓰는 값은 `~/.koda/.env` 에 두고, 프로젝트마다 다른 값만 그 디렉터리의 `.env` 에 둔다. 전체 항목은 저장소의 [`.env.example`](../.env.example) 에 있다.

koda 는 모델명으로 provider 를 유추해 base_url·reasoning 방식·구조화 출력 방식을 채운다. 그래서 provider 를 바꿀 때는 `MODEL` 과 그 provider 의 키만 바꾸면 된다.

| `MODEL` 접두 | provider | 키 |
|---|---|---|
| `deepseek-*` (기본 `deepseek-v4-flash`) | DeepSeek | `DEEPSEEK_API_KEY` |
| `gpt-*` · `o1` · `o3` · `o4` · `chatgpt-*` | OpenAI | `OPENAI_API_KEY` |
| 그 밖의 이름 | 자체 호스팅 | `OPENAI_BASE_URL` 을 직접 준다 |

로컬 llama.cpp 를 주로 쓰려면 `PRIMARY=local` 로 바꾼다. 그때 API 는 로컬 서버가 죽었을 때의 폴백이 된다. provider 별 환경변수 블록과 문제 해결은 [providers/](providers/README.md) 에 있다.

> **셸에 남은 변수를 조심한다.** 환경변수가 파일을 이기므로, 다른 provider 를 쓰던 셸에 `OPENAI_BASE_URL`·`REASONING_STYLE`·`STRUCTURED_METHOD` 가 남아 있으면 `.env` 를 아무리 고쳐도 반영되지 않고 401·400 이 난다. 먼저 이렇게 확인한다.
>
> ```bash
> env | grep -E '^(MODEL|PRIMARY|OPENAI_|DEEPSEEK_|LOCAL_|REASONING_|STRUCTURED_)'
> ```

`MODEL` 만 환경변수로 바꿨는데 `.env` 에 다른 provider 의 공식 주소(예: `https://api.deepseek.com`)가 남아 있으면, koda 가 그 주소와 파생 설정을 새 모델의 것으로 바꿔 쓴다. 어느 provider 에도 속하지 않는 주소(자체 호스팅)는 그대로 둔다.

## 6. 쓰는 방법 세 가지

| 명령 | 용도 |
|---|---|
| `koda` (= `koda tui`) | 사람이 쓰는 기본 화면. 세션 여러 개를 동시에 돌린다 |
| `koda run "작업"` | 한 번 실행하고 끝난다. 스크립트·자동화용이다 |
| `koda chat` | 줄 단위 REPL. 표준입력을 파이프로 넘기면 한 줄씩 배치로 실행한다 |

| 옵션 | 뜻 |
|---|---|
| `--workspace <경로>` | 작업할 디렉터리 (기본: 현재 디렉터리) |
| `--yolo` | 위험 도구 자동 승인 |
| `--no-mcp` | 외부 MCP 를 건너뛰어 빠르게 뜬다 |
| `--mcps-dir <경로>` | 프로젝트 MCP 설정 폴더 (기본: `<workspace>/mcps`) |
| `--resume <세션ID>` | 저장된 세션을 이어서 연다 (`koda`·`koda chat`) |

`koda run` 은 검증이 실패하거나 최종 응답이 비면 종료 코드 1 을, 설정 오류나 예외면 2 를 낸다.

```bash
koda run "calc.py 에 sub 함수를 추가해줘" --workspace . --yolo
```

## 7. TUI 기본 조작

| 키 | 동작 |
|---|---|
| `Enter` | 보내기 |
| `Ctrl+J` | 줄바꿈 (터미널이 지원하면 `Alt+Enter`·`Shift+Enter` 도 된다) |
| `/` | 슬래시 명령 후보. 이어서 치는 글자로 좁혀진다 |
| `@` | 워크스페이스 파일·폴더 후보 |
| `Ctrl+B` | 세션 패널 접기·펼치기. 패널에서 `Enter` 전환 · `n` 새 세션 · `d` 삭제 |
| `Ctrl+C` | 답변 중이면 그 턴을 중단한다. 2초 안에 한 번 더 누르면 저장하고 끝낸다 |
| `Ctrl+Q` | 바로 저장하고 끝낸다 |

| 명령 | 동작 |
|---|---|
| `/help` | 명령 목록 |
| `/status` | 세션·모델·환경 |
| `/context` | 컨텍스트 예산을 범주별(시스템 프롬프트·도구·MCP·메모리·대화 이력 등)로 나눠 본다 |
| `/model` | 세션 중 모델 전환 |
| `/mcp` | MCP 서버 상태·도구 목록, 추가·삭제·재연결 |
| `/resume` · `/rename <이름>` · `/clear` | 세션 재개 · 이름 바꾸기 · 새 스레드 |
| `/compact` | 대화 이력을 요약해 압축한다 |
| `/remember <내용>` · `/memory` | 영구 메모리에 넣기 · 보기 |
| `/thinking [show\|hide]` · `/theme [이름]` | 사고 줄 표시 전환 · 테마 전환 |
| `/exit` | 끝내기 |

화면 구성과 표시 규칙의 전체 설명은 루트 [README 의 빠른 시작](../README.md#빠른-시작) 에 있다.

## 8. koda 가 만드는 파일

워크스페이스 안에 `.koda/` 가 생긴다. 저장소라면 `.gitignore` 에 `.koda` 를 넣어 둔다.

```
<workspace>/.koda/
  ├─ sessions/<id>.json      세션 이력. /resume 으로 다시 연다
  ├─ memory/                 영구 메모리. 모든 에이전트의 시스템 프롬프트에 들어간다
  │    ├─ semantic.md        사실: 사용자·프로젝트에 대한 지속적인 사실과 선호
  │    ├─ episodic.md        경험: 실제로 한 일과 결과
  │    └─ procedural.md      규칙: 앞으로 지키라고 지시받은 작업 방식
  ├─ artifacts/<세션>/        잘린 도구 출력의 원문. 에이전트가 read_artifact 로 다시 읽는다
  ├─ logs/mcp.log            stdio MCP 서버의 stderr
  └─ run/cmd-*.log           background 로 띄운 명령(개발 서버 등)의 출력
```

에이전트의 파일 도구는 `.koda/` 를 보지 못한다. 세션과 메모리가 작업 대상에 섞이지 않게 하려는 것이다.

홈 디렉터리에는 모든 워크스페이스가 함께 쓰는 설정이 있다.

```
~/.koda/
  ├─ .env        공통 설정과 키
  └─ mcps/       전역 MCP 서버 (koda mcp add 의 기본 저장 위치)
```

`~/.koda/` 가 없고 예전 위치 `~/.config/koda/` 만 있으면 그쪽을 읽는다.

## 9. 안전 모델

네 겹으로 막는다.

1. **정적 차단** — `sudo`, 원격 스크립트 파이프 실행, 워크스페이스 밖이나 대량으로 지우는 `rm -rf`, `git reset --hard`·`git clean -f`, `chmod 777`, 디스크 파괴 명령은 어떤 모드에서도 실행되지 않는다. 자격증명(`.ssh`·`.aws`·키 파일)과 `.env` 접근, `git push --force`, `find -exec`, 워크스페이스 밖 경로로의 `cp`·`mv` 는 사람이 직접 승인해야 한다. `--yolo` 의 자동 승인으로는 실행되지 않는다
2. **승인 게이트** — 읽기 도구를 뺀 모든 도구(쓰기·삭제·명령 실행·MCP)는 승인 대상이다. 기본값이 거부다
3. **샌드박스** — 파일 도구는 워크스페이스 밖으로 나가지 못한다. bubblewrap 이 있으면 셸 명령도 OS 수준에서 격리한다
4. **시크릿 가림** — 모든 도구 출력에서 API 키·토큰·개인키 패턴을 `[REDACTED]` 로 바꾼다

읽기 전용 조사 셸(`run_readonly_command`)은 승인 없이 돈다. 대신 1번의 두 등급을 모두 차단하고, bubblewrap 이 있으면 읽기 전용으로 격리한다. 사용자의 `PATH`·`HOME` 을 그대로 쓰므로 터미널에서 보이는 도구는 조사 셸에서도 보인다.

## 10. 확장하기

- **MCP** — 웹 검색·최신 문서 조회 같은 외부 도구를 붙인다. `koda mcp add --url …` 한 줄이면 된다. → [mcp/](mcp/README.md)
- **스킬** — `<workspace>/skills/<이름>/SKILL.md` 를 두면 에이전트가 목록을 보고 필요할 때 본문을 읽는다
- **메모리** — 턴이 끝날 때마다 판정자 LLM 이 기억할 만한 것을 골라 저장한다. 직접 넣으려면 `/remember [semantic|episodic|procedural] <내용>` (유형을 빼면 semantic)
- **에이전트** — `src/koda/agents/specs.py` 의 `AGENTS` 에 항목을 더하고 `src/koda/prompts/<이름>.md` 를 둔다
- **provider** — [providers/ 의 "새 provider 추가"](providers/README.md#새-provider-추가)

스킬 파일은 머리말에 `name`·`description` 을 적는다. 에이전트는 이 설명을 보고 스킬을 쓸지 정한다.

```markdown
---
name: release-notes
description: 커밋 로그를 읽어 사용자용 릴리스 노트를 쓴다
---

1. git log 로 지난 태그 이후 커밋을 모은다
2. …
```

## 11. 개발 환경

```bash
uv sync --extra tui                            # .venv 구성. --extra tui 를 빼면 textual 이 지워진다
uv tool install -e ".[tui]" --force            # 어디서나 저장소 코드로 도는 koda
uv run python -m pytest --ignore=tests/test_live_llm.py -q
```

- `uv run pytest` 는 콘솔 스크립트를 찾지 못해 `Failed to spawn: pytest` 를 내는 경우가 있다. `python -m pytest` 로 부른다
- `tests/test_live_llm.py` 는 실제 LLM 엔드포인트를 호출하므로 평소에는 뺀다
- 테스트는 셸에 export 된 koda 설정 변수(`MODEL`·`PRIMARY`·`OPENAI_*`·`LOCAL_*` 등)를 자동으로 걷어낸다(`tests/conftest.py`). 셸 상태와 무관하게 같은 결과가 나와야 하기 때문이다
- push 와 PR 마다 `.github/workflows/test.yml` 이 같은 테스트를 돌린다

추적이 필요하면 langfuse 를 띄운다.

```bash
docker compose up -d        # 웹 UI: http://localhost:28900
```

`.env` 에 `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY` 를 넣으면 턴마다 trace 가 남는다. 턴 하나가 trace 하나이고, 같은 세션의 턴은 langfuse Sessions 뷰에서 한 대화로 묶인다. 키가 없으면 추적을 끈다.

## 12. 문제 해결

| 증상 | 원인 → 조치 |
|---|---|
| 코드를 고쳤는데 `koda` 에 반영되지 않는다 | `-e` 없이 설치해 복사본이 돈다 → `uv tool install -e ".[tui]" --force` (3절) |
| `.env` 를 고쳤는데 값이 바뀌지 않는다 | 셸에 export 된 같은 이름의 변수가 이긴다 → 5절의 `env \| grep` 으로 찾아 `unset` |
| 시작하자마자 "API 키가 없습니다" 로 끝난다 | 쓸 수 있는 엔드포인트가 없다 → `DEEPSEEK_API_KEY`, 또는 `MODEL` 과 그 provider 의 키 |
| 401 · 400 | 다른 provider 의 base_url·reasoning·구조화 방식이 남아 있다 → [providers/](providers/README.md) 의 해당 provider 문제 해결 표 |
| `textual 미설치` 로 끝난다 | TUI extra 없이 설치했다 → `uv tool install ".[tui]" --force` |
| `uv run pytest` → `Failed to spawn: pytest` | `uv run python -m pytest` 로 부른다 |
| TUI 테스트가 `No module named 'textual'` | `uv sync` 가 extra 를 지웠다 → `uv sync --extra tui` |
| `OS 샌드박스(bubblewrap) 비활성` 경고 | bubblewrap 이 없어 정적 차단만 적용된다. Linux 는 `apt install bubblewrap`, macOS 는 해당 없다 |
| MCP 도구가 안 보이거나 실패한다 | [mcp/ 의 문제 해결](mcp/README.md#문제-해결) |
