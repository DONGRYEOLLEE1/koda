# koda docs · koda 문서

[English](#english) · [한국어](#한국어) · README: [English](../README.md) · [한국어](../README.ko.md)

## English

New to koda: [install](install.md), then the README [quick start](../README.md#quick-start). The rest are reference pages to look up when you need them. Pages marked (Korean) are written in Korean only for now.

| Doc | What's inside |
|---|---|
| [install.md](install.md) | One-line installer, its options, other ways to install, uninstalling |
| [onboarding.md](onboarding.md) (Korean) | Install → first run → settings → workspace files → safety → development → troubleshooting |
| [features.md](features.md) | TUI and sessions, tools, MCP, memory, context budget and prompt cache, providers, tracing |
| [architecture.md](architecture.md) | Routing, the agent graph (diagram and Mermaid), components, how an edit is judged |
| [safety.md](safety.md) | The four safety layers, their limits, guardrail results under auto-approve |
| [configuration.md](configuration.md) | Settings order, provider table, tuning variables |
| [tui.md](tui.md) (Korean) | TUI layout, session panel, status line, keys and slash commands, how turns are displayed |
| [providers/](providers/README.md) (Korean) | Environment blocks per provider, endpoint compatibility, adding a provider, troubleshooting |
| [mcp/](mcp/README.md) (Korean) | Adding external MCP servers, keys, auto-approval, troubleshooting |
| [development.md](development.md) | Running the tests, CI, code size, running your working tree |
| [benchmarks/](../benchmarks/README.md) | Method and results of the koda / opencode / Hermes comparison |

Overview (install, demo, why koda, benchmark summary): the root README ([English](../README.md) · [한국어](../README.ko.md)).

## 한국어

처음이라면 [온보딩 가이드](onboarding.md) 를 위에서부터 따라간다. 설치부터 첫 작업, 설정, 개발 환경까지 한 번에 잡힌다. 나머지는 필요할 때 찾아보는 참고 문서다.

| 문서 | 내용 | 이럴 때 본다 |
|---|---|---|
| [onboarding.md](onboarding.md) | 설치 → 첫 실행 → 설정 → 워크스페이스 파일 → 안전 → 개발 → 문제 해결 | 처음 쓸 때, 새 머신에 설치할 때 |
| [install.ko.md](install.ko.md) | 한 줄 설치 스크립트와 옵션, 다른 설치 방법, 제거 | 버전을 고정하거나 클론에서 설치할 때 |
| [features.ko.md](features.ko.md) | TUI 와 세션, 도구, MCP, 메모리, 컨텍스트 예산과 프롬프트 캐시, provider, 추적 | koda 가 무엇을 할 수 있는지 한눈에 볼 때 |
| [architecture.ko.md](architecture.ko.md) | 라우팅, 에이전트 그래프(다이어그램·Mermaid), 구성 요소, 편집을 판정하는 방식 | 내부 동작을 이해하거나 에이전트를 붙일 때 |
| [safety.ko.md](safety.ko.md) | 안전장치 네 겹과 한계, 자동 승인 상태의 가드레일 결과 | `--yolo` 를 쓰기 전에 |
| [configuration.ko.md](configuration.ko.md) | 설정이 읽히는 순서, provider 표, 튜닝 항목 | 모델·키·예산을 바꿀 때 |
| [tui.md](tui.md) | TUI 화면 구성, 세션 패널, 상태줄, 키·슬래시 명령, 턴 표시 규칙 | TUI 화면의 표시가 무슨 뜻인지 알고 싶을 때 |
| [providers/](providers/README.md) | provider 별 환경변수 블록, 엔드포인트 호환 표, 새 provider 추가, 문제 해결 | 모델·엔드포인트를 바꿀 때, 401·400 이 날 때 |
| [mcp/](mcp/README.md) | 외부 MCP 서버 등록·키 관리·자동 승인·문제 해결 | 웹 검색·문서 조회 도구를 붙일 때 |
| [development.ko.md](development.ko.md) | 테스트 실행, CI, 코드 규모, 작업 트리로 koda 돌리기 | 코드를 고치고 테스트할 때 |
| [benchmarks/](../benchmarks/README.md) (영문) | koda·opencode·Hermes 비교의 방법과 결과 | 수치의 조건과 koda 가 뒤처진 항목까지 볼 때 |

설치·데모·특징·벤치마크 요약은 루트 README([English](../README.md) · [한국어](../README.ko.md))에 있다.
