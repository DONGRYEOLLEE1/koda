# 안전 모델

[English](safety.md) · **한국어** · [README](../README.ko.md) 로 돌아가기

네 겹을 차례로 거친다. `--yolo` 를 쓰기 전에 한계까지 알아 둔다.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/safety-layers-ko-dark.svg">
    <img src="assets/safety-layers-ko-light.svg" alt="모든 도구 호출이 차례로 거치는 koda 의 안전장치 네 겹: 1 셸 명령 정적 차단, 2 기본 거부 승인(읽기 전용 도구 8개는 묻지 않고, --yolo 는 나머지를 자동 승인), 3 파일 도구의 워크스페이스 제한과 Linux 에서만 셸 명령에 bubblewrap(macOS 는 없음), 4 도구 출력의 시크릿 가림(verifier 에 가는 스냅샷 diff 는 가리지 않음). --yolo 가 바꾸는 것은 2계층뿐이다" width="860">
  </picture>
</p>

## `--yolo` 로도 풀리지 않는 안전장치

정적 차단 목록이 승인 게이트보다 먼저 돈다. 목록에 오른 명령(`sudo`·`curl … | sh`·`git reset --hard` 등)은 어떤 모드에서도 실행되지 않고, 자격증명·`.env`·`git push --force` 를 건드리는 명령은 사람이 직접 승인해야 하므로 `--yolo` 에서는 거부된다.

- [`denygate.py`](../src/koda/denygate.py) 의 차단 11패턴 + 확인 6패턴 + `rm` 2단 판정, 단위 테스트 51건.
- 정규식 기반이다.
- OS 샌드박스는 Linux 에 bubblewrap 이 있을 때만 된다(아래 3계층).

## 안전장치 네 겹

| 계층 | 하는 일 | 한계 |
|---|---|---|
| 1. 정적 차단 | `sudo`·`su`, `curl … \| sh`, `git reset --hard`, `git clean -f`, `chmod 777`, 장치 대상 `mkfs`·`dd`, `find -delete`, 포크 폭탄, 위험 대상 재귀 `rm` 은 실행하지 않는다. 자격증명 경로·키 파일·`.env` 를 건드리는 명령, `git push --force`, `find -exec`, 워크스페이스 밖으로의 `cp`·`mv` 는 사람이 직접 승인해야 하고, `--yolo` 에서는 거부된다. | 정규식 기반이다. `run_command`·`run_readonly_command`(두 등급 모두 거부)에만 걸리고 파일 도구·MCP 에는 걸리지 않는다. |
| 2. 승인 게이트 | 읽기 전용 8개를 뺀 모든 도구가 먼저 묻고, 기본값은 거부다. MCP 도 `KODA_MCP_AUTO_APPROVE` 에 넣은 것이 아니면 여기에 걸린다. sync·async 양쪽에 걸린다. TUI 승인 창을 600초 두면 거부되고, `--yolo` 없이 파이프로 받는 `koda chat` 은 거부한다. | `--yolo` 는 1계층이 허락하는 것을 모두 승인한다. `run_readonly_command` 는 읽기 전용 8개에 들어 있어 묻지 않는다. |
| 3. 샌드박스 | 파일 도구는 워크스페이스 밖으로 못 나가고 `.koda/` 를 보지 못한다. bubblewrap 이 있으면 `run_command` 는 루트 읽기 전용에 워크스페이스와 `~/.cache` 만 쓰기로, 조사 셸은 `--unshare-all` 과 읽기 전용 바인드로 돈다. | bubblewrap 은 Linux 전용이고, `BH_SANDBOX=off` 로 끌 수 있다(네임스페이스를 못 만드는 컨테이너용). 없으면 셸 명령이 샌드박스 없이 돌고 경고가 뜬다. 이때 `run_readonly_command` 를 읽기 전용으로 묶는 것은 지시문과 차단 목록뿐이다. `run_command` 샌드박스는 네트워크를 끊지 않는다. |
| 4. 시크릿 가림 | 개인키 블록, `sk-`·`pk-`·`ghp_`·`gho_`·`xoxb`·`xoxp` 토큰, `api_key`·`secret`·`token`·`password`·`authorization` 대입값을 모델이 보기 전의 도구 결과, 저장되는 artifact, verifier 가 읽는 도구 실행 기록에서 가린다. | 패턴 기반이다. verifier 가 읽는 파일 diff 는 가리지 않는다. |

요약하면 `--yolo` 가 바꾸는 것은 2계층뿐이다. macOS 에는 bubblewrap 이 없어 셸 명령이 OS 샌드박스 없이 돌고, 조사 셸을 읽기 전용으로 묶는 것은 차단 목록과 지시문뿐이다.

## 자동 승인 상태에서 잰 결과

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/bench-guardrails-ko-dark.svg">
    <img src="assets/bench-guardrails-ko-light.svg" alt="차트: 자동 승인 상태의 가드레일 결과. 스크립트를 sh 로 파이프 실행하라는 요청은 세 도구 모두 3/3 유지. 팀원 커밋 위 force push 요청에서 팀원 커밋 보존은 koda·Hermes Agent 3/3, opencode 0/3. koda 모델이 시도한 위험 명령 40회 중 실행 0회(38회는 자체 verifier 재시도 뒤). exit code 의 실패 신호는 koda 부분적(실패 8런 중 2런만 0 이 아닌 exit)" width="860">
  </picture>
</p>

koda 의 모델이 시도한 `curl | sh`·`git push --force`·`git reset --hard` 40회 중 실행된 것은 없다. 다만 그중 38회는 멈추거나 되물은 첫 시도를 koda 자신의 verifier 가 fail 로 판정한 뒤의 재시도에서 나왔다. koda 가드레일 12런 중 1런은 목록 밖 명령으로 작업을 잃었다(Hermes Agent 12/12). opencode 는 내장 명령 차단 목록을 문서화하지 않았고, Hermes 모델은 `curl | sh`·force push 를 시도하지 않았으며 `git reset --hard` 는 작업을 먼저 저장한 뒤에만 실행했다. 런 `main-20260930`, 2026-09-30. 자세한 내용은 [benchmarks/README.md](../benchmarks/README.md#results-run-main-20260930)(영문).

샌드박스 경고나, 네임스페이스를 만들 수 없는 컨테이너에서의 문제 해결은 [onboarding.md 12절](onboarding.md#12-문제-해결).
