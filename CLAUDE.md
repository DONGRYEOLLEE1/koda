# build-harness — Claude Code 작업 지침

이 저장소는 로컬 LLM 기반 coding-agent 하네스를 만든다. 전체 설계는 `plan/PLAN.md` 참조.

## 코드 스타일 (엄격)

- 요청한 동작을 만족하는 **가장 작은 변경**을 선호한다. 사변적 추상화, 미래 대비용 일반화, 미사용 코드를 만들지 않는다.
- 기존 코드의 관례·구조를 따르되, 새 관례를 늘리지 않는다.
- 함수·모듈은 짧고 단일 책임으로. 불필요한 계층·래퍼·설정 옵션을 만들지 않는다.
- 주석은 "왜"가 필요할 때만. 코드로 자명한 것을 설명하지 않는다.

## 테스트 (pytest · 핵심 모듈 우선)

- 프레임워크는 **pytest**를 쓴다.
- **필수(핵심) 모듈은 반드시 단위 테스트로 커버한다**: `workspace`(샌드박스), `permissions`(권한 게이트), `tools`, `config`, `ledger`, `llm`, `orchestrator`.
- 사용자 흐름은 **시나리오 기반 통합 테스트**로 검증하고, 직접 실행해 관찰할 수 있게 한다(`tests/scenarios.py`).
- 단, 자명한 코드에 커버리지용 테스트를 남발하지 않는다. 핵심 동작·경계·안전 불변식에 집중한다.
- 기존 테스트 **실행**은 검증 신호로 적극 사용한다. (작성 억제 ≠ 실행 억제)

## 서브에이전트 (codex 적극 활용)

- 다음 작업은 **codex 서브에이전트에 적극 위임**한다: 리팩토링, 광범위한 웹검색·조사, 코드리뷰, 구현 검증, 막혔을 때의 2차 진단.
- 기능 구현 시 **기능당 codex 리뷰 1회**를 기본으로 한다.
- codex 산출물은 Claude가 최종 검증한다(맹신 금지). codex quota 소진 시 일반 서브에이전트로 폴백.

## 하네스: DeepSeek 위임

**목표:** codex와 같은 범주의 위임 작업(리팩토링·조사·코드리뷰 2차 의견·구현 검증·2차 진단)을 DeepSeek API로도 보낼 수 있게 한다.

**트리거:** 위 작업 중 DeepSeek로 위임/보완하고 싶을 때 `deepseek-delegate` 서브에이전트를 Agent 툴로 호출하라(model: sonnet). 이 에이전트는 항상 `.claude/skills/deepseek-delegate`의 `scripts/delegate.sh`를 통해서만 위임한다 (모델 고정: `deepseek-v4-pro[1m]`, 권한: yolo/`--dangerously-skip-permissions`, 예산 캡 없음). 산출물은 codex와 동일하게 2차 의견으로 취급하고 Claude가 검토한다. 단순 질문은 직접 응답 가능.

## 하네스: Antigravity(agy) 리뷰/검증 위임

**목표:** codex·deepseek와 같은 범주의 위임 작업 중 **엄격한 리뷰/검증**(코드 리뷰 2차 의견·계획문서 검증·구현 검증·2차 진단)을 Antigravity CLI(`agy`)로도 보낼 수 있게 한다. Claude와 다른 모델 계열(Gemini)의 독립적 2차 의견을 얻는 창구다.

**절대 원칙 — 수정 위임 금지:** agyy(Gemini 계열)는 codex·deepseek·Claude 대비 **코드 생성 모델 품질이 낮다고 판단**한다. 그래서 **파일 생성/수정/삭제, 코드 작성·리팩토링은 절대 위임하지 않는다** — 오직 **리뷰·검증**(읽고 판단)만 위임한다. yolo(`--dangerously-skip-permissions`) 모드라 도구가 쓰기를 막아주지 않으므로, 위임 프롬프트에 매번 read-only를 명시해 강제한다(테스트 **실행**을 통한 관찰은 허용, 소스 수정은 금지).

**트리거 — 모든 작업에 적극적으로 사용:** 추론 속도가 매우 빨라 호출 부담이 거의 없다. codex처럼 "기능당 1회"로 제한하지 않고, **코드/문서 변경이 있는 모든 작업에서 완료 시점마다 기본으로** antigravity-review 1차 검증을 받는 것을 원칙으로 한다. 예:

- 코드 변경/구현 완료 직후 1차 검증
- 계획 문서(`plan/*.md`) 작성·수정 후 검증
- codex/deepseek 산출물에 대한 교차검증 (독립된 세 번째 의견)
- 막힌 문제의 대안 진단
- 그 외에도 판단이 필요한 시점이면 망설이지 않고 호출한다

**호출:** 위 상황에서 `antigravity-review` 서브에이전트를 Agent 툴로 호출하라(model: sonnet). 이 에이전트는 항상 `.claude/skills/antigravity-review`의 `scripts/review.sh`를 통해서만 위임한다 (기본 모델: `Gemini 3.1 Pro (High)`, 권한: yolo=`agyy`=`agy --dangerously-skip-permissions`). `agyy`는 셸 alias라 비대화형 스크립트에서 확장되지 않으므로 스크립트가 `agy --dangerously-skip-permissions`를 직접 호출한다. 산출물은 codex/deepseek와 동일하게 2차 의견으로 취급하고 Claude가 최종 검토한다(맹신 금지).

**폴백 (사용량 제한 대응, codex와 동일한 패턴):** agy 사용량 제한(quota/rate limit)에 도달해 위임이 실패하면 **일반 서브에이전트로 폴백**해 동일한 리뷰/검증 작업을 계속 진행한다. antigravity-review는 실패 원인이 사용량 제한으로 보이면 그 사실을 보고에 명시해, 호출자가 폴백 여부를 바로 판단할 수 있게 한다. CLI 미설치·타임아웃 등 사용량 제한이 아닌 실패는 기존대로 실패를 그대로 보고한다 — 설정 문제를 폴백으로 가리지 않는다.

## 일반

- 불확실하면 코드를 늘리기 전에 먼저 묻는다.
- 새 의존성 추가·큰 리팩터링은 사전에 합의한다. (Python 3.14, 의존성 최소 원칙)
