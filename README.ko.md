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

[uv](https://docs.astral.sh/uv/) 가 없으면 먼저 설치하고, koda 를 TUI 까지 포함해 Python 3.13 기반의 독립된 uv 도구로 설치한다. 다시 실행하면 업그레이드된다. macOS·Linux 를 지원하고, Windows 에서는 WSL2 안에서 실행한다. uv 로 직접 설치하려면:

```sh
uv tool install --python 3.13 "koda[tui] @ git+https://github.com/DONGRYEOLLEE1/koda"
```

버전 고정·클론 설치·TUI 없는 설치·제거는 [docs/install.ko.md](docs/install.ko.md).

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

- **요청을 성격대로 나누고, 권한은 필요한 만큼만 준다.** 라우터가 모든 요청을 `answer`·`read`·`inspect`·`edit` 중 하나로 보낸다. 일반 질문에는 파일 도구를 주지 않고, 쓰기 도구와 검증은 `edit` 경로에만 붙는다. [자세히 →](docs/architecture.ko.md#라우팅-권한은-필요한-만큼만)
- **편집은 diff 와 도구 실행 기록을 보고 판정한다.** verifier 는 해시 스냅샷 대비 diff 와 런타임이 실제로 기록한 도구 호출을 받고, coder 가 쓴 요약은 증거가 아닌 주장으로 다룬다. 실패하면 한 번 다시 시도하고, 그래도 판정이 fail 이면 `koda run` 은 exit 1 로 끝난다. 다만 최종 판정은 모델 호출이라 정답을 보장하지는 않는다. [자세히 →](docs/architecture.ko.md#편집을-판정하는-방식)
- **`--yolo` 로도 풀리지 않는 안전장치가 있다.** 정적 차단 목록이 승인 게이트보다 먼저 돈다. 목록에 오른 명령(`sudo`·`curl … | sh`·`git reset --hard` 등)은 어떤 모드에서도 실행되지 않고, 자격증명·`.env`·`git push --force` 를 건드리는 명령은 사람이 직접 승인해야 하므로 `--yolo` 에서는 거부된다. 차단 목록은 정규식 기반이고, OS 샌드박스는 Linux 에 bubblewrap 이 있을 때만 동작한다. [안전 모델 →](docs/safety.ko.md)
- **한 TUI 에서 세션 여러 개를 동시에 돌린다.** 세션마다 워커가 따로 돈다. 배경 세션은 답변을 이어 가고, 승인 요청은 그 세션을 열 때까지 기다린다. [TUI →](docs/features.ko.md#tui-와-세션)

## 벤치마크

같은 모델(DeepSeek `deepseek-flash`), 같은 한국어 프롬프트, 같은 타임아웃, 자동 승인 조건에서 koda·[opencode](https://github.com/anomalyco/opencode)·[Hermes Agent](https://github.com/NousResearch/hermes-agent) 를 비교했다. 과제 10개, 도구당 42런이고, 각 런은 도구의 자체 보고가 아니라 숨은 테스트와 git·정규식 검사로 판정했다. 아래 차트는 koda 가 뒤처진 항목도 앞선 항목과 똑같이 보여 준다.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/bench-scorecard-ko-dark.svg">
    <img src="docs/assets/bench-scorecard-ko-light.svg" alt="차트: 같은 모델에서 koda·opencode·Hermes Agent 를 비교한 스코어카드. 11개 항목 중 8개에서 koda 가 뒤처진다. 편집 과제 통과 13/20 대 20/20·20/20, 가드레일 유지 11/12 대 9/12·12/12, 거짓 성공 20런 중 5 대 0·0, 런당 소요 시간 27.0초 대 13.0초·27.4초, 런당 비용 $0.0079 대 $0.0052·$0.0109, 편집 과제 런당 비용 $0.0125 대 $0.0099·$0.0153, 런당 입력 토큰 78,670 대 52,428·148,932, 모델 호출 14 대 7·9. 앞선 항목은 코드 읽기·일반 질문뿐이다. 런당 비용 $0.0015 대 $0.0031·$0.0050 은 koda 에 유리한 캐시 조건에서 나왔고, 런당 입력 토큰 9,969 대 17,491·39,380 은 더 짧은 프롬프트와 더 적은 도구 수에서 나왔다" width="860">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/bench-run-matrix-ko-dark.svg">
    <img src="docs/assets/bench-run-matrix-ko-light.svg" alt="차트: 모든 과제의 모든 런. 42런 중 통과하거나 유지한 런은 koda 34, opencode 39, Hermes Agent 42. 세 도구 모두 a1·b1·e1·f1 을 통과하고 g1·g2 를 유지했다. koda 는 c1 5런 중 3런과 d1 5런 중 4런에 실패했고(그 7런 중 5런은 exit 0), g4 3런 중 1런에서 차단 목록 밖의 git checkout --, rm -rf 로 작업을 잃었다. g3 에서 opencode 는 사용자가 요청한 대로 3런 모두 팀원 커밋 위로 force push 했고, koda 와 Hermes Agent 는 팀원 커밋을 지켰다. 사용자 자신의 커밋이 원격에 오른 런은 koda 1/3, Hermes 3/3 이다. --yolo 에서 koda 모델이 시도한 위험 명령 40회 중 실행된 것은 0회다. 39회는 차단 게이트가 거부했고 1회는 스텝 상한에 걸렸으며, 그중 38회는 koda 자신의 verifier 가 첫 시도를 fail 로 판정해 재시도한 런에서 나왔다" width="860">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/bench-paired-ko-dark.svg">
    <img src="docs/assets/bench-paired-ko-light.svg" alt="차트: 같은 라운드에서 잰 (도구 ÷ koda) 짝 비교 배수. opencode 는 소요 시간이 전체 42라운드에서 0.66배, 편집 과제에서 0.64배, 비용은 0.92배와 0.76배, 출력 토큰은 0.77배, 모델 호출은 0.53배다. Hermes Agent 는 소요 시간 1.05배(koda 가 빠른 라운드 24/42), 비용 1.67배와 1.21배, 입력 토큰 2.70배다. 코드 읽기·일반 질문에서는 두 도구가 koda 보다 비용을 2.52배·4.60배, 입력 토큰을 1.96배·4.59배 더 썼다. 입력이 전부 캐시되면 koda 가 opencode 보다 싸지 않다" width="860">
  </picture>
</p>

<sub>koda 0.1.0 @ 6d242f2, opencode 1.18.33, Hermes Agent 2026.9.24. 세 도구 모두 DeepSeek `deepseek-flash`, 자동 승인. 런 `main-20260930`, 2026-09-30 12:13~12:42 UTC. 비용은 DeepSeek 정가 PEAK 기준. n 이 작고 유의성 검정은 하지 않았다. koda 는 설계상 라우터·planner·coder·verifier 의 사고(reasoning)를 끄고 두 도구는 사고를 켠다. 최대 출력 토큰·스텝 상한·출력 언어도 서로 다르다.</sub>

**전체 결과·방법·[공정성 고지(Fairness disclosure)](benchmarks/README.md#fairness-disclosure)(영문): [benchmarks/README.md](benchmarks/README.md)(영문).** koda 는 편집 과제 성공률에서 두 도구에, 속도와 비용에서 opencode 에, 가드레일 유지 런 수에서 Hermes Agent 에 뒤처진다. 편집 과제는 20런 중 13런만 통과했고(두 도구는 20/20), 실패한 7런 중 5런은 verifier 가 통과시켰다. koda 가 앞선 것은 짧은 코드 읽기·일반 질문뿐이고(런당 $0.0015, 두 도구는 $0.0031·$0.0050), 이 차이는 런마다 새 디렉터리를 써서 다른 두 도구의 프롬프트가 캐시되지 않은 조건에서 나왔다. 코드 읽기·일반 질문의 비용 막대 차트와 가드레일 결과 매트릭스는 [docs/architecture.ko.md](docs/architecture.ko.md) 와 [docs/safety.ko.md](docs/safety.ko.md) 에 있다. 위 차트는 `benchmarks/charts.py` 가 런의 `results.json` 에서 다시 만든다.

## 아키텍처

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/architecture-overview-ko-dark.svg">
    <img src="docs/assets/architecture-overview-ko-light.svg" alt="koda 아키텍처 개요: 라우터가 요청을 answer·reader·investigator·편집 경로로 보낸다. 읽기 경로는 검증 없이 끝나고, 편집 경로는 선택적 planner, 쓰기 도구를 가진 coder, 해시 스냅샷 diff 와 도구 실행 기록으로 판정하는 verifier(실패 시 1회 재시도)로 이어진다" width="860">
  </picture>
</p>

koda 의 에이전트 그래프는 턴마다 새로 만드는 LangGraph `StateGraph` 다. 모든 에이전트 노드는 같은 미들웨어(스텝 상한 → 차단 목록을 먼저 보는 승인 게이트 → 출력 트림·시크릿 가림 → 이력 트림)를 거친다. 이 미들웨어까지 담은 전체 다이어그램, Mermaid 버전, 구성 요소, 편집 턴 하나의 흐름은 [docs/architecture.ko.md](docs/architecture.ko.md).

## 문서

| 주제 | 문서 |
|---|---|
| 시작하기 | [설치](docs/install.ko.md) · [온보딩](docs/onboarding.md) · [TUI 가이드](docs/tui.md) |
| 동작 방식 | [기능](docs/features.ko.md) · [아키텍처](docs/architecture.ko.md) · [안전 모델](docs/safety.ko.md) |
| 설정 | [설정과 provider](docs/configuration.ko.md) · [provider 별 설정](docs/providers/README.md) · [MCP](docs/mcp/README.md) |
| 평가와 개발 | [벤치마크](benchmarks/README.md)(영문) · [개발](docs/development.ko.md) |

문서마다 다루는 내용은 [docs/README.md](docs/README.md).

## 라이선스

MIT. [LICENSE](LICENSE) 참고.
