<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img src="docs/assets/logo-light.svg" alt="koda" width="280">
  </picture>
</p>

<p align="center"><b>요청을 분류하고, 편집을 증거로 검증하는 터미널 코딩 에이전트.</b></p>
<p align="center">koda 는 요청을 처리에 꼭 필요한 권한만 가진 경로로 보내고,<br>편집 결과는 모델의 말이 아니라 파일 diff 와 실제 도구 실행 기록을 보고 판정한다.</p>

<p align="center">
  <a href="https://github.com/DONGRYEOLLEE1/koda/actions/workflows/test.yml"><img alt="tests" src="https://github.com/DONGRYEOLLEE1/koda/actions/workflows/test.yml/badge.svg"></a>
  <img alt="Python 3.13+" src="https://img.shields.io/badge/python-3.13%2B-3776ab?style=flat-square">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue?style=flat-square"></a>
  <a href="benchmarks/README.md"><img alt="opencode·Hermes 와 실측 비교" src="https://img.shields.io/badge/benchmarked-vs%20opencode%20%C2%B7%20hermes-7c74f4?style=flat-square"></a>
</p>

<p align="center"><a href="README.md">English</a> · <b>한국어</b></p>

## 설치

```sh
curl -fsSL https://raw.githubusercontent.com/DONGRYEOLLEE1/koda/main/install.sh | sh
```

[uv](https://docs.astral.sh/uv/) 가 없으면 먼저 설치하고, koda 를 TUI 까지 포함해 Python 3.13 기반의 독립된 uv 도구로 설치한다. 다시 실행하면 업그레이드된다. macOS·Linux 를 지원하고, Windows 에서는 WSL2 안에서 실행한다.

<details>
<summary>다른 설치 방법</summary>

```sh
# 브랜치·태그·커밋 고정
curl -fsSL https://raw.githubusercontent.com/DONGRYEOLLEE1/koda/main/install.sh | sh -s -- --ref <ref>

# uv 로 직접 (uv 는 requires-python 을 보고 인터프리터를 고르지 않으므로 --python 을 준다)
uv tool install --python 3.13 "koda[tui] @ git+https://github.com/DONGRYEOLLEE1/koda"

# 클론해서 설치. -e 면 작업 트리의 코드가 그대로 돈다
git clone https://github.com/DONGRYEOLLEE1/koda && cd koda
uv tool install --python 3.13 -e ".[tui]"
```

TUI 없이 `koda run`·`koda chat` 만 쓸 거라면 `[tui]` 를 뺀다. 제거는 `uv tool uninstall koda`.

</details>

<!-- DEMO, public 전환 후: GIF 를 영상으로 바꾼다.
     1. 한국어 자막 하이라이트 컷(koda-readme.ko.mp4, 32초, 약 1.8MB. 한도 10MB)을 github.com 의 이슈나 PR 코멘트 입력창에
        끌어다 놓고, 입력창에 들어가는 https://github.com/user-attachments/assets/<uuid> URL 을 복사한다.
     2. 아래 <p align="center"> 블록 전체를 그 URL 한 줄로 바꾼다. 앞뒤는 빈 줄로 둔다.
        GitHub 는 단독 줄의 user-attachments URL 만 영상으로 재생하고, <video> 태그나 레포 경로는 재생하지 않는다.
     3. 캡션은 그 아래 <sub> 한 줄로 남기고, 로그아웃한 브라우저에서 재생되는지 확인한 뒤,
        docs/assets/koda-demo.ko.gif 를 더 쓰지 않으면 지운다. -->
<p align="center">
  <img src="docs/assets/koda-demo.ko.gif" alt="TUI 에서 요청이 분류되고, 도구가 실행되고, diff 가 보이고, 검증이 통과하는 모습" width="860">
  <br><sub>DeepSeek <code>deepseek-flash</code> 로 실제 사용한 화면이다.</sub>
</p>

<!-- CODESPACES: public 전환 후 아래 뱃지를 추가한다
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/DONGRYEOLLEE1/koda?quickstart=1)
(.devcontainer/ 가 DEEPSEEK_API_KEY 를 권장 시크릿으로 묻는다. 컴퓨트 비용은 방문자 계정에 청구된다.) -->

## 빠른 시작

```sh
export DEEPSEEK_API_KEY=sk-...   # 필요한 설정은 이것 하나
cd your-project
koda                             # TUI. 읽기 전용 도구 8개 밖의 호출은 먼저 승인을 묻는다
```

TUI 없이: `koda run "calc.py 에 두 수를 빼는 sub 함수를 추가하고 테스트해줘" --workspace . --yolo`. 종료 코드는 턴을 마치면 `0`(편집이면 검증 통과), 검증 실패나 빈 응답이면 `1`, 설정 오류나 실행 중 예외면 `2` 다.

## 왜 koda 인가

- **요청을 성격대로 나누고, 권한은 필요한 만큼만 준다.** 라우터가 모든 요청을 `answer`·`read`·`inspect`·`edit` 중 하나로 보낸다. 일반 질문에는 파일 도구를 주지 않고, 쓰기 도구와 검증은 `edit` 경로에만 붙는다.<br>
  <sub>[`agents/graph.py`](src/koda/agents/graph.py) 의 7노드 LangGraph 그래프 · 에이전트별 도구 스텝 상한 5(answer)~35(coder) · 읽기 경로는 검증을 건너뛴다 · 같은 모델로 코드 읽기·일반 질문 2개(각 5회)를 잰 결과 런당 입력 토큰 중앙값이 9,969 로, opencode 17,491, Hermes Agent 39,380 보다 적었다([벤치마크](#벤치마크)).</sub>
- **편집은 diff 와 도구 실행 기록을 보고 판정한다.** verifier 는 coder 가 시작할 때 떠 둔 해시 스냅샷 대비 diff(새 파일 포함)와 런타임이 실제로 기록한 도구 호출을 받는다. coder 가 쓴 요약은 "증거가 아닌 주장" 이라고 표시해서 넘긴다. 도구를 한 번도 안 쓰고 바뀐 것도 없으면 LLM 을 부르지 않고 실패 처리하고, 실패하면 그 이유를 들고 한 번 다시 시도한다. 그래도 판정이 fail 이면 `koda run` 은 exit 1 로 끝난다.<br>
  <sub>[`diffscan.py`](src/koda/diffscan.py), [`agents/toollog.py`](src/koda/agents/toollog.py), [`prompts/verifier.md`](src/koda/prompts/verifier.md) · 최종 판정은 이 증거를 받은 모델 호출이라 정답을 보장하지는 않는다 · `test_no_tool_and_no_change_fails_without_llm` 등 테스트로 고정.</sub>
- **`--yolo` 로도 풀리지 않는 안전장치가 있다.** 정적 차단 목록이 승인 게이트보다 먼저 돈다. 목록에 오른 명령(`sudo`·`curl … | sh`·`git reset --hard` 등)은 어떤 모드에서도 실행되지 않고, 자격증명·`.env`·`git push --force` 를 건드리는 명령은 사람이 직접 승인해야 하므로 `--yolo` 에서는 거부된다.<br>
  <sub>[`denygate.py`](src/koda/denygate.py) 의 차단 11패턴 + 확인 6패턴 + `rm` 2단 판정, 단위 테스트 51건 · 정규식 기반 · OS 샌드박스는 Linux 에 bubblewrap 이 있을 때만([안전 모델](#안전-모델)).</sub>
- **한 TUI 에서 세션 여러 개를 동시에 돌린다.** 세션마다 워커가 따로 돈다. 한 세션에 작업을 맡겨 두고 다른 세션을 열어 계속 일할 수 있다. 배경 세션은 답변을 이어 가고, 승인 요청은 그 세션을 열 때까지 기다린다.<br>
  <sub>[`tui/app.py`](src/koda/tui/app.py) 의 세션별 워커 그룹.</sub>
- **컨텍스트 예산은 설정 하나로 조절한다.** `CONTEXT_BUDGET_TOKENS`(기본 122k) 하나를 바꾸면 이력 트림·세션 압축·도구 출력 상한이 함께 따라 움직인다. 도구 스키마 몫은 실제로 붙은 도구로 재고, 한글 토큰 수는 보정해서 센다.<br>
  <sub>[`config.py`](src/koda/config.py), [`middleware/history.py`](src/koda/middleware/history.py).</sub>

## 벤치마크

같은 모델(DeepSeek `deepseek-flash`), 같은 한국어 프롬프트, 같은 타임아웃, 자동 승인 조건에서 koda·[opencode](https://github.com/anomalyco/opencode)·[Hermes Agent](https://github.com/NousResearch/hermes-agent) 를 비교했다. 과제 10개, 도구당 42런이고, 각 런은 도구의 자체 보고가 아니라 숨은 테스트와 git·정규식 검사로 판정했다.

**koda 가 실측으로 앞선 항목: 런 `main-20260930`, 2026-09-30.** koda 가 앞선 행만 싣고, 행마다 측정 범위를 적었다.

| 측정(범위) | koda | opencode | Hermes Agent |
|---|---:|---:|---:|
| 런당 입력 토큰 중앙값 · 코드 읽기·일반 질문(e1 + f1 과제, 도구당 10런) | **9,969** | 17,491 | 39,380 |
| 런당 비용 중앙값 · 같은 질문, 런마다 새 디렉터리에서 단발 실행 ¹ | **$0.0015** | $0.0031 | $0.0050 |
| 일반 질문에 답하는 요청의 시스템 프롬프트(f1, 5런 모두 같음) ² | **892자, 도구 2개** | 9,850자, 도구 10개 | 21,004자, 도구 24개 |
| "`git push --force` 로 덮어써 줘" 요청 뒤 원격에 남은 팀원 커밋(g3, 3런) ³ | **3/3** | 0/3 | **3/3** |
| `--yolo` 에서 `curl … \| sh`·`git push --force`·`git reset --hard`: 모델 시도 중 실행된 것(g2~g4, 각 3런) ⁴ | **40회 중 0** | 해당 없음 | 해당 없음 |

<sub>koda 0.1.0 @ 63fda57, opencode 1.18.33, Hermes Agent 2026.9.24. 세 도구 모두 DeepSeek `deepseek-flash`, 자동 승인. 런 `main-20260930`, 2026-09-30 12:13~12:42 UTC. 비용은 DeepSeek 정가 PEAK 기준. n 이 작고 유의성 검정은 하지 않았다.<br>
¹ 런마다 이름이 무작위인 새 디렉터리를 썼다. opencode·Hermes 는 프롬프트에 런마다 달라지는 내용(아마도 워크스페이스 경로)을 넣어서 런 사이 캐시가 앞부분 약 1.8k·2.6k 토큰만 재사용했고, 경로가 없는 koda 프롬프트는 거의 통째로 캐시됐다. 입력이 전부 캐시됐다고 가정하면 이 질문들에서도 koda 가 opencode 보다 싸지 않다. 편집 과제에서는 opencode 가 더 쌌다.<br>
² 답변 요청만 잰 값이다. koda 는 같은 턴에 router·제목 요청을 더 보내므로 턴 전체로는 3,083자 대 11,946자 대 21,946자다. 도구를 적게 싣는다는 뜻이지 품질 지표가 아니다.<br>
³ 시나리오 1개: 원격에 팀원 커밋 2개가 있는 줄 모르는 사용자가 덮어쓰라고 요청한다. Hermes 는 사용자 커밋까지 3/3 원격에 올렸다(koda 1/3). opencode 는 사용자가 요청한 force push 를 했다.<br>
⁴ 40회 중 38회는 멈추거나 되물은 첫 시도를 koda 자신의 verifier 가 fail 로 판정한 뒤의 재시도에서 나왔다. koda 가드레일 12런 중 1런은 목록 밖 명령으로 작업을 잃었다(Hermes 12/12). opencode 는 내장 명령 차단 목록을 문서화하지 않았고, Hermes 모델은 <code>curl | sh</code>·force push 를 시도하지 않았으며 <code>git reset --hard</code> 는 작업을 먼저 저장한 뒤에만 실행했다.</sub>

**전체 결과(koda 가 뒤처진 항목 포함): [benchmarks/](benchmarks/README.md)**. koda 는 편집 과제 성공률에서 두 도구에, 속도와 비용(편집 과제·전체 런)에서 opencode 에, 가드레일 유지 런 수에서 Hermes Agent 에 뒤처진다.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/bench-query-cost-dark.svg">
    <img src="docs/assets/bench-query-cost-light.svg" alt="차트: 코드 읽기·일반 질문(e1·f1, 도구당 10런, 런마다 새 디렉터리에서 단발 실행)의 런당 비용 중앙값. koda $0.0015, opencode $0.0031(짝 비교 2.5배), Hermes Agent $0.0050(4.6배). 입력이 전부 캐시되면 koda 가 opencode 보다 싸지 않고, 편집 과제에서는 opencode 가 더 싸다" width="860">
  </picture>
</p>
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/bench-guardrails-dark.svg">
    <img src="docs/assets/bench-guardrails-light.svg" alt="차트: 자동 승인 상태의 가드레일 결과. 스크립트를 sh 로 파이프 실행하라는 요청은 세 도구 모두 3/3 유지. 팀원 커밋 위 force push 요청에서 팀원 커밋 보존은 koda·Hermes Agent 3/3, opencode 0/3. koda 모델이 시도한 위험 명령 40회 중 실행 0회(38회는 자체 verifier 재시도 뒤). exit code 의 실패 신호는 koda 부분적(실패 8런 중 2런만 0 이 아닌 exit)" width="860">
  </picture>
</p>

맞출 수 없는 조건은 [benchmarks/README.md](benchmarks/README.md#fairness-disclosure) 에 수치와 함께 밝혔다. koda 는 설계상 라우터·planner·coder·verifier 를 사고(reasoning) 없이 돌리고(읽기 전용 에이전트는 `REASONING_EFFORT` 로 사고한다), opencode·Hermes 는 사고를 켠 채 돈다. 최대 출력 토큰·스텝 상한·출력 언어도 서로 다르다.

## 아키텍처

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/architecture-dark.svg">
    <img src="docs/assets/architecture-light.svg" alt="koda 아키텍처: router 가 요청을 answer·reader·investigator·편집 경로로 보낸다. 편집 경로는 선택적 planner, 쓰기 도구를 가진 coder, 해시 스냅샷 diff 와 도구 실행 기록으로 판정하는 verifier(실패 시 1회 재시도)로 이어진다. 모든 에이전트 노드는 스텝 상한·차단 목록과 승인·워크스페이스 제한·시크릿 가림 미들웨어를 거친다" width="860">
  </picture>
</p>

<details>
<summary>같은 그래프의 Mermaid 버전</summary>

```mermaid
flowchart TD
    Q([요청]) --> R{router}
    R -- answer --> A[answer<br/>파일 도구 없음]
    R -- read --> RD[reader<br/>파일 + 읽기 전용 셸]
    R -- "inspect<br/>(파싱 실패 시 기본값)" --> I[investigator<br/>읽기 전용 셸]
    R -- "edit + 계획" --> P[planner<br/>체크리스트 최대 5개]
    R -- edit --> C[coder<br/>쓰기 도구 + 셸]
    P --> C
    C --> V{verifier<br/>해시 diff + 도구 기록}
    V -- "실패 + 계획: 1회 재시도" --> P
    V -- "실패: 1회 재시도" --> C
    V -- "통과 또는 재시도 소진" --> D([끝])
    A --> D
    RD --> D
    I --> D
```

</details>

- **그래프.** 턴마다 새로 만드는 LangGraph `StateGraph` 이고 전 경로가 async 다. 멀티턴 상태는 checkpointer 없이 세션 이력을 매 턴 주입해서 잇는다. 편집이 검증에 실패하면 한 번만 다시 시도하는데, 라우터가 계획이 필요하다고 본 요청이면 planner 부터 다시 탄다.
- **에이전트.** 각 에이전트는 `AgentSpec` 레지스트리로 만든 langchain 1.x `create_agent` 다. answer·reader·investigator·planner·coder 와 `explore` 가 있다. answer 는 워크스페이스 도구를 쥐지 않아 개념·용어 질문에 검색 루프를 돌지 않는다.
- **보조 에이전트.** reader·investigator·coder 는 턴당 한 번 읽기 전용 `explore` 서브에이전트에 탐색을 맡길 수 있고, 돌아오는 것은 요약뿐이다. verify 안에서는 워크스페이스 파일이 2개 이상 바뀌었거나 diff 가 2,000자를 넘으면 별도 reviewer 가 diff 를 한 번 더 읽는다.
- **모델.** provider 와 상관없이 OpenAI 호환 클라이언트 하나를 쓴다. `reasoning_content` 까지 보존하도록 `ChatOpenAI` 를 확장한 클래스다. 모델명 접두로 provider 프로필(base_url·reasoning 방식·구조화 출력 방식·API 형태)을 고르고, 같은 이름의 환경변수를 주면 그 값이 이긴다. router·planner·coder·verifier·reviewer 는 사고를 끄고, answer·reader·investigator·explore 는 켠다. `temperature=0`, `max_tokens=4096`.
- **구조화 출력.** router `{route, plan_needed}` 와 verifier `{verdict, reasoning}` 는 `STRUCTURED_METHOD` 방식으로 받는다. DeepSeek 와 프로필이 없는 엔드포인트는 function calling, OpenAI 는 `json_schema` 가 기본이고, llama.cpp 는 `json_schema` 를 지정하면 문법으로 형식을 강제한다. 구조화 출력이 실패하면 둘 다 일반 응답으로 폴백한다. router 는 첫 단어를 보고 애매하면 `inspect` 로 보내고, verifier 는 비어 있지 않은 첫 줄이 `PASS` 일 때만 통과시킨다.
- **미들웨어(바깥 → 안).** 도구 스텝 상한 → 승인 게이트(차단 목록 먼저) → 출력 트림·시크릿 가림 → 토큰 예산에 맞춘 이력 트림.
- **프롬프트.** 에이전트 지시문은 [`src/koda/prompts/`](src/koda/prompts) 의 마크다운 23개이고, 코드는 컨텍스트 예산 안내·메모리·오늘 날짜만 덧붙인다. 에이전트를 새로 붙이려면 [`agents/specs.py`](src/koda/agents/specs.py) 의 `AGENTS` 항목과 `prompts/<이름>.md` 를 추가하고, 그 에이전트를 부르는 노드나 도구를 `graph.py` 에 둔다.

편집 턴 하나가 분류부터 판정까지 흘러가는 순서:

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/turn-lifecycle-dark.svg">
    <img src="docs/assets/turn-lifecycle-light.svg" alt="편집 턴 하나의 타임라인: router 가 요청을 분류하고, 선택적으로 planner 가 체크리스트를 쓰고, coder 가 도구를 반복 호출하는 동안 koda 가 모든 호출을 도구 기록에 남긴다. coder 가 시작할 때와 검증 직전에 워크스페이스 해시를 떠서 diff 를 만들고, 도구 기록과 diff 가 증거가 된다. 도구 호출도 변경도 없으면 LLM 없이 실패 처리하고, 그 밖에는 verifier 한 번이 coder 의 답을 주장으로 보고 증거를 판정하며, 실패하면 피드백을 들고 한 번 재시도한다" width="860">
  </picture>
</p>

## 기능

<details>
<summary><b>TUI 와 세션</b></summary>

- 왼쪽 패널에 세션이 상태 글리프와 함께 보인다. 보고 있는 세션 `●`, 답변 중(스피너), 승인 대기 `◐`, 실패 `✕`, 대기 `○`. `Ctrl+B` 로 접고 펴며, `n` 새 세션 · `d` 삭제 · `Enter` 전환.
- 세션은 병렬로 돈다. 배경 세션은 답변을 이어 가다 끝나면 알리고, 승인 요청은 그 세션을 열 때 묻는다.
- 상태줄은 `모델 · 승인 모드 · git 브랜치 · MCP 도구 수 · 컨텍스트 게이지` 다. 게이지는 프롬프트에 실리는 전부(시스템 프롬프트·메모리·스킬·도구 스키마·이력·그 턴의 도구 출력)를 예산과 견주고, `/context` 에서 범주별로 나눠 본다.
- `/` 는 명령, `@` 는 워크스페이스 경로를 자동완성한다. `Ctrl+C` 는 답변 중인 턴을 멈추고, 2초 안에 한 번 더 누르면 저장하고 끝낸다.
- 편집 diff 는 대화에 색상 그대로 남고, 도구 스텝은 턴마다 한 줄로 접힌다. DeepSeek·vLLM 이 주는 `reasoning_content` 는 사고 줄로 보인다.
- 세션은 `.koda/sessions/<id>.json` 에 git 브랜치·시작 커밋과 함께 저장되고, `/resume` 으로 다시 연다. 제목은 첫 실질 턴이 끝난 뒤 LLM 이 짓는다.
- 명령: `/help` `/status` `/context` `/model` `/mcp` `/resume` `/rename` `/clear` `/compact` `/memory` `/remember` `/thinking` `/theme` `/exit`.
- TUI 밖의 `koda run`·`koda chat` 은 TTY 에서 Rich 마크다운으로 실시간 렌더한다. `koda chat` 에 표준입력을 파이프로 넘기면 한 줄씩 배치로 실행한다.

화면 구성과 표시 규칙 전체: [docs/tui.md](docs/tui.md).

</details>

<details>
<summary><b>도구</b></summary>

무엇을 부를지는 모델이 정하고, 게이트·상한·샌드박스는 코드가 집행한다.

| | 도구 |
|---|---|
| 읽기 (무승인) | `read_file`(줄번호·범위) · `list_files` · `search_text`(캡·glob) · `run_readonly_command` · `explore` · `read_artifact` · `analyze_image` · `skill` |
| 변경 (승인 대상) | `write_file` · `edit_file` · `multi_edit` · `patch_file` · `create_dir` · `delete_path` · `run_command` |

- 편집은 원자적이다. 임시 파일에 쓰고 `os.replace` 로 바꿔 실행 비트와 CRLF 를 지킨다. `multi_edit` 는 전부 되거나 전부 안 되고, `patch_file` 은 앵커로 드리프트를 잡으며, 일치하지 않으면 근접 후보를 돌려준다.
- 기본 예산에서 8,000자(웹 검색·크롤 계열은 3,000자)를 넘는 도구 출력은 자르고, 두 상한 모두 `CONTEXT_BUDGET_TOKENS` 에 비례해 커진다. 시크릿을 가린 원문은 `.koda/artifacts/` 에 남아 `read_artifact` 로 다시 읽을 수 있다.
- 스킬: 워크스페이스에 `skills/<이름>/SKILL.md` 를 두면 에이전트가 목록을 보고 필요할 때 본문을 읽는다.

</details>

<details>
<summary><b>MCP</b></summary>

```sh
koda mcp add --url https://mcp.context7.com/mcp    # 기본은 전역(~/.koda/mcps), 이 워크스페이스만이면 --scope project. 저장 전에 실제로 붙여 본다
koda mcp add -- npx -y @playwright/mcp@latest      # `--` 뒤는 stdio 실행 명령
koda mcp list | remove <이름> | enable|disable <이름> [--here]
```

- 전역 `~/.koda/mcps/` 와 프로젝트 `<workspace>/mcps/` 를 읽고, 같은 서버명이면 프로젝트가 키 단위로 덮는다. 그래서 `"enabled": false` 한 줄로 그 워크스페이스에서만 끌 수 있다.
- 키는 `${VAR}` 로 참조하고 `.env` 와 환경변수에서 채운다. 채울 값이 없거나 파일이 깨지면 조용히 버리지 않고 `/mcp` 에 드러낸다.
- 서버마다 세션 하나를 프로세스가 사는 동안 유지해, Playwright 처럼 상태가 있는 서버도 호출 사이에 상태가 이어진다.
- MCP 호출은 도구 이름을 `KODA_MCP_AUTO_APPROVE` 에 넣지 않는 한 승인 대상이다.

가이드: [docs/mcp/](docs/mcp/README.md).

</details>

<details>
<summary><b>메모리</b></summary>

- `<workspace>/.koda/memory/` 에 세 유형으로 둔다. `semantic`(사실), `episodic`(한 일과 결과, 날짜 포함), `procedural`(지키라고 한 규칙).
- TUI·`koda chat` 에서 턴이 끝날 때마다 판정 호출이 유형별로 최대 2개(각 200자 이하)를 고른다. 대부분의 턴은 아무것도 저장하지 않고, 저장하면 화면에 알린다. 세션을 끝낼 때 몰아서 추출하지는 않는다. `koda run` 은 읽기만 한다.
- 한 유형이 3,000자를 넘으면 LLM 이 그 유형만 통합 요약한다. `/remember [유형] <내용>` 으로 직접 넣을 수도 있다.
- 세 유형 모두 모든 에이전트의 시스템 프롬프트에 들어간다. 규칙은 지시로, 경험은 비슷한 작업의 참고 사례로 쓰인다.
- 메모리와 별개로, 세션 이력이 턴 사이를 이어 "방금 만든 파일에 …" 같은 지시어를 풀어 준다.

</details>

<details>
<summary><b>컨텍스트 예산과 프롬프트 캐시</b></summary>

- 기본 122k 는 시스템 프롬프트·도구 스키마 약 17k + 압축 후 이력 약 12k + 턴 안의 도구 출력 약 93k 로 잡은 값이다.
- 모델을 부를 때마다 예산에서 시스템 프롬프트·실측 도구 스키마·출력 4,096 토큰을 뺀 만큼으로 이력을 자른다. 첫 사용자 메시지와 도구 호출 쌍은 깨지 않는다.
- 이력이 약 36,000자(예산에 비례)를 넘으면 최근 4턴만 원문으로 두고 나머지를 요약한다. `/compact` 로 직접 할 수도 있다.
- 읽기 상한: 범위 없는 `read_file` 은 400줄·120k자까지, `search_text` 는 기본 80건·최대 200건.
- 도구 스키마도 예산을 먹는다. 도구 44개가 약 12k 토큰, 74개가 약 61k 토큰으로 측정됐다. 안 쓰는 MCP 서버는 끈다.
- 시스템 프롬프트를 정적 → 동적 순서(날짜만 넣고 시각은 넣지 않음)로 쌓고, MCP 도구는 세션당 한 번 읽고, 이력은 압축 사이에는 뒤에 덧붙기만 한다. 그래서 에이전트가 바뀌거나 메모리가 저장되거나 이력이 압축되기 전까지는 프롬프트 앞부분이 캐시에 맞게 유지된다. DeepSeek `deepseek-flash` 로 읽기 전용 질문 5턴짜리 `koda chat` 세션 3개를 잰 결과(2026-09-30), 2~5턴 프롬프트 토큰의 69~74% 가 캐시 적중이었고, 한 턴의 도구 루프 안에서는 중앙값 88% 였다. 다른 에이전트로 경로가 바뀌었거나 그사이 메모리가 저장된 턴의 첫 요청은 14~37% 에 그쳤다([데이터](benchmarks/results/chat-cache-20260930/README.md)).

</details>

<details>
<summary><b>provider</b></summary>

- 모델명 접두로 프로필을 고르므로, provider 를 바꿀 때는 `MODEL` 과 그 provider 의 키만 바꾸면 된다. 세션 중에는 `/model` 로 바꾼다.
- `PRIMARY=local` 이면 로컬 llama.cpp 서버를 먼저 쓰고, 서버가 죽어 있으면 API 키로 폴백한다(반대 방향도 된다).
- `MODEL` 을 환경변수로 바꿨는데 `.env` 에 다른 provider 의 공식 주소가 남아 있으면, koda 가 그 주소와 파생 설정을 새 provider 것으로 바꿔 쓴다. 자체 호스팅 주소는 그대로 둔다.
- 쓸 수 있는 키가 없으면 첫 요청에서 401 을 내는 대신 시작할 때 안내하고 끝난다.

</details>

<details>
<summary><b>추적 (Langfuse)</b></summary>

- 로컬 파일로 저장하지 않고 Langfuse 에 기록한다.
- 턴 하나가 trace 하나다. 이름은 세션 제목이고, 입력·출력은 질의와 최종 답변 원문이다. `session_id` 로 묶여 Langfuse Sessions 뷰에서 한 대화로 보인다.
- 그 아래에 에이전트별 도구 입출력과 모델 호출별 토큰이 남는다.
- 키가 없거나 인증 확인에 실패하면 조용히 꺼지고, 추적 실패가 턴을 실패시키지 않는다. `docker compose up -d` 로 로컬 스택(`http://localhost:28900`)을 띄운다.

</details>

## 안전 모델

네 겹을 차례로 거친다. `--yolo` 를 쓰기 전에 한계까지 알아 두자.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/safety-layers-dark.svg">
    <img src="docs/assets/safety-layers-light.svg" alt="모든 도구 호출이 차례로 거치는 koda 의 안전장치 네 겹: 1 셸 명령 정적 차단, 2 기본 거부 승인(읽기 전용 도구 8개는 묻지 않고, --yolo 는 나머지를 자동 승인), 3 파일 도구의 워크스페이스 제한과 Linux 에서만 셸 명령에 bubblewrap(macOS 는 없음), 4 도구 출력의 시크릿 가림(verifier 에 가는 스냅샷 diff 는 가리지 않음). --yolo 가 바꾸는 것은 2계층뿐이다" width="860">
  </picture>
</p>

| 계층 | 하는 일 | 한계 |
|---|---|---|
| 1. 정적 차단 | `sudo`·`su`, `curl … \| sh`, `git reset --hard`, `git clean -f`, `chmod 777`, 장치 대상 `mkfs`·`dd`, `find -delete`, 포크 폭탄, 위험 대상 재귀 `rm` 은 실행하지 않는다. 자격증명 경로·키 파일·`.env` 를 건드리는 명령, `git push --force`, `find -exec`, 워크스페이스 밖으로의 `cp`·`mv` 는 사람이 직접 승인해야 하고, `--yolo` 에서는 거부된다. | 정규식 기반이다. `run_command`·`run_readonly_command`(두 등급 모두 거부)에만 걸리고 파일 도구·MCP 에는 걸리지 않는다. |
| 2. 승인 게이트 | 읽기 전용 8개를 뺀 모든 도구가 먼저 묻고, 기본값은 거부다. MCP 도 `KODA_MCP_AUTO_APPROVE` 에 넣은 것이 아니면 여기에 걸린다. sync·async 양쪽에 걸린다. TUI 승인 창을 600초 두면 거부되고, `--yolo` 없이 파이프로 받는 `koda chat` 은 거부한다. | `--yolo` 는 1계층이 허락하는 것을 모두 승인한다. `run_readonly_command` 는 읽기 전용 8개에 들어 있어 묻지 않는다. |
| 3. 샌드박스 | 파일 도구는 워크스페이스 밖으로 못 나가고 `.koda/` 를 보지 못한다. bubblewrap 이 있으면 `run_command` 는 루트 읽기 전용에 워크스페이스와 `~/.cache` 만 쓰기로, 조사 셸은 `--unshare-all` 과 읽기 전용 바인드로 돈다. | bubblewrap 은 Linux 전용이고, `BH_SANDBOX=off` 로 끌 수 있다(네임스페이스를 못 만드는 컨테이너용). 없으면 셸 명령이 샌드박스 없이 돌고 경고가 뜬다. 이때 `run_readonly_command` 를 읽기 전용으로 묶는 것은 지시문과 차단 목록뿐이다. `run_command` 샌드박스는 네트워크를 끊지 않는다. |
| 4. 시크릿 가림 | 개인키 블록, `sk-`·`pk-`·`ghp_`·`gho_`·`xoxb`·`xoxp` 토큰, `api_key`·`secret`·`token`·`password`·`authorization` 대입값을 모델이 보기 전의 도구 결과, 저장되는 artifact, verifier 가 읽는 도구 실행 기록에서 가린다. | 패턴 기반이다. verifier 가 읽는 파일 diff 는 가리지 않는다. |

## 설정

셸 환경변수 → 현재 디렉터리의 `.env` → `~/.koda/.env` 순으로 읽고, 앞의 것이 이긴다. 전체 항목은 [`.env.example`](.env.example).

| provider | `MODEL` | 키 | 비고 |
|---|---|---|---|
| **DeepSeek** (기본) | `deepseek-*` (기본 `deepseek-v4-flash`) | `DEEPSEEK_API_KEY` | Chat Completions, 구조화 출력은 function calling. `deepseek-v4-flash` 는 DeepSeek 가 아직 받아 주는 퇴역한 이름이고, 요청은 DeepSeek-V4.1-Flash 가 처리한다. DeepSeek 문서와 데모·벤치마크는 `deepseek-flash` 를 쓴다. |
| **OpenAI** | `gpt-*`, `o1`·`o3`·`o4`, `chatgpt-*` | `OPENAI_API_KEY` | Chat Completions 가 도구와 reasoning 을 함께 받지 않아 Responses API 로 보낸다. reasoning 을 모르는 모델(gpt-4o 류)은 `REASONING_STYLE=off`. |
| **llama.cpp** (로컬) | 임의, `LOCAL_MODEL_NAME` 으로 | 서버가 요구하면 `LOCAL_API_KEY` | `PRIMARY=local LOCAL_OPENAI_BASE_URL=http://localhost:8080/v1 REASONING_STYLE=qwen STRUCTURED_METHOD=json_schema` |

그 밖의 OpenAI 호환 서버(vLLM, 프록시)는 `MODEL`·`OPENAI_BASE_URL`·`OPENAI_API_KEY` 로 붙는다. Anthropic API 는 OpenAI 호환이 아니라 지원하지 않는다. 튜닝 항목: `REASONING_STYLE`, `REASONING_EFFORT`, `THINKING_BUDGET_READER`/`_EXPLORE`, `STRUCTURED_METHOD`, `CONTEXT_BUDGET_TOKENS`, `KODA_MCP_AUTO_APPROVE`, `LANGFUSE_*`. provider 별 환경변수 블록과 문제 해결은 [docs/providers/](docs/providers/README.md).

## 문서

| | |
|---|---|
| [온보딩](docs/onboarding.md) | 설치 → 첫 실행 → 설정 → 워크스페이스 파일 → 안전 → 개발 → 문제 해결 |
| [TUI 가이드](docs/tui.md) | 세션 패널, 상태줄, 키, 명령, 턴 표시 규칙 |
| [provider](docs/providers/README.md) | provider 별 환경변수 블록, 엔드포인트 호환 표, 새 provider 추가 |
| [MCP](docs/mcp/README.md) | 서버 등록, 키 관리, 자동 승인, 문제 해결 |
| [벤치마크](benchmarks/README.md) | koda·opencode·Hermes 비교의 방법과 결과 |

## 개발

```sh
git clone https://github.com/DONGRYEOLLEE1/koda && cd koda
uv sync --extra tui                                          # extra 를 빼면 TUI 테스트가 깨진다
uv run python -m pytest --ignore=tests/test_live_llm.py -q   # `uv run pytest` 는 스크립트를 못 찾을 때가 있다
```

- 테스트 404건이 Python 3.13.13·3.14.5 에서 모두 통과한다(macOS, 2026-09-30, pytest 보고 34~38초). CI 워크플로는 `main` push 와 PR 마다 `ubuntu-latest` 의 Python 3.14(`.python-version`)로 같은 명령을 돌린다. `tests/test_live_llm.py` 는 실제 엔드포인트를 불러서 뺀다.
- 파이썬 44개 모듈 약 6.9k줄(그중 TUI 2.7k), 프롬프트 23개, 테스트 5.8k줄.
- `uv tool install --python 3.13 -e ".[tui]" --force` 로 설치하면 PATH 의 `koda` 가 작업 트리의 코드로 돈다.

## 라이선스

MIT. [LICENSE](LICENSE) 참고.
